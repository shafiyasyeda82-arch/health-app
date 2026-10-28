"""Health readings tracker (blood pressure, blood glucose, weight, ...).

All the readings pages live here so main.py stays small. To support a new kind of
measurement later, add one more entry to READING_TYPES below.
"""

import math
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from app.database import get_session
from app.models import HealthReading, Profile, User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

NOTE_MAX = 300
RECENT_LIMIT = 4  # how many readings the main page shows before "View all"
CHART_POINTS = 30  # how many of the latest readings the chart uses

# ---------- What kinds of readings exist ----------
# The limits below only reject numbers that are impossible or obvious typing
# mistakes. They are NOT medical thresholds and say nothing about what is healthy.
# "noun" is the lower-case name used inside sentences ("Add a ... reading").

READING_TYPES = {
    "blood_pressure": {
        "label": "Blood pressure",
        "noun": "blood pressure",
        "units": ["mmHg"],
        "default_unit": "mmHg",
        "two_values": True,
        "first_name": "systolic",
        "second_name": "diastolic",
        "first_label": "Systolic (top number)",
        "second_label": "Diastolic (bottom number)",
        "first_limits": {"mmHg": (40, 300)},
        "second_limits": {"mmHg": (20, 200)},
        "series_names": ["Systolic", "Diastolic"],
        "contexts": ["Sitting at rest", "After activity", "Other"],
    },
    "blood_glucose": {
        "label": "Blood glucose (sugar)",
        "noun": "blood glucose (sugar)",
        "units": ["mg/dL", "mmol/L"],
        "default_unit": "mg/dL",
        "two_values": False,
        "first_name": "glucose value",
        "second_name": "",
        "first_label": "Glucose value",
        "second_label": "",
        "first_limits": {"mg/dL": (10, 1000), "mmol/L": (0.5, 55)},
        "second_limits": {},
        "series_names": ["Glucose"],
        "contexts": ["Fasting", "Before a meal", "After a meal", "Bedtime", "Random"],
    },
    "hba1c": {
        "label": "HbA1c",
        "noun": "HbA1c",
        "units": ["%", "mmol/mol"],
        "default_unit": "%",
        "two_values": False,
        "first_name": "HbA1c value",
        "second_name": "",
        "first_label": "HbA1c value",
        "second_label": "",
        "first_limits": {"%": (3, 20), "mmol/mol": (9, 200)},
        "second_limits": {},
        "series_names": ["HbA1c"],
        "contexts": ["Lab test", "Home test kit"],
        "help": (
            "HbA1c reflects your average blood glucose over roughly the last 2 to 3 "
            "months. It is usually measured in a lab, so there will be far fewer "
            "entries than daily glucose readings."
        ),
    },
    "weight": {
        "label": "Weight",
        "noun": "weight",
        "units": ["kg", "lb"],
        "default_unit": "kg",
        "two_values": False,
        "first_name": "weight",
        "second_name": "",
        "first_label": "Weight",
        "second_label": "",
        "first_limits": {"kg": (2, 500), "lb": (4.4, 1100)},
        "second_limits": {},
        "series_names": ["Weight"],
        "contexts": ["Morning, before eating", "Evening", "Other"],
        "bmi": True,
    },
    "heart_rate": {
        "label": "Heart rate",
        "noun": "heart rate",
        "units": ["bpm"],
        "default_unit": "bpm",
        "two_values": False,
        "first_name": "heart rate",
        "second_name": "",
        "first_label": "Heart rate (beats per minute)",
        "second_label": "",
        "first_limits": {"bpm": (20, 300)},
        "second_limits": {},
        "series_names": ["Heart rate"],
        "contexts": ["Resting", "After activity", "Other"],
    },
    "oxygen": {
        "label": "Oxygen (SpO₂)",
        "noun": "oxygen level (SpO₂)",
        "units": ["%"],
        "default_unit": "%",
        "two_values": False,
        "first_name": "oxygen saturation",
        "second_name": "",
        "first_label": "Oxygen saturation (SpO₂)",
        "second_label": "",
        "first_limits": {"%": (50, 100)},
        "second_limits": {},
        "series_names": ["SpO₂"],
        "contexts": ["At rest", "After activity", "Other"],
    },
}
DEFAULT_TYPE = "blood_pressure"


# ---------- Small helpers ----------


def get_current_user_or_none(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return session.get(User, user_id)


def get_owned_profile(profile_id: int, user: User, session: Session) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def get_owned_reading(
    reading_id: int, profile_id: int, session: Session
) -> HealthReading:
    reading = session.get(HealthReading, reading_id)
    if reading is None or reading.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Reading not found")
    return reading


def load_rows(
    session: Session, profile_id: int, reading_type: str
) -> List[HealthReading]:
    """All readings of one type, newest first."""
    return session.exec(
        select(HealthReading)
        .where(
            HealthReading.profile_id == profile_id,
            HealthReading.reading_type == reading_type,
        )
        .order_by(
            col(HealthReading.measured_at).desc(), col(HealthReading.id).desc()
        )
    ).all()


def match_option(value: Optional[str], options: List[str]) -> str:
    """Return the option that matches value (ignoring capitals), or '' if none does."""
    wanted = (value or "").strip().lower()
    for option in options:
        if option.lower() == wanted:
            return option
    return ""


def pick_type(value: Optional[str]) -> str:
    return value if value in READING_TYPES else DEFAULT_TYPE


def format_number(value) -> str:
    """5.0 -> '5', 5.60 -> '5.6', 120.0 -> '120'."""
    if value is None:
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


templates.env.filters["num"] = format_number


def parse_number(text: Optional[str]) -> Optional[float]:
    """Turn what was typed into a number, or None if it is not a usable number."""
    cleaned = (text or "").strip().replace(",", ".")
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    return number


def compute_bmi(weight: float, unit: str, height_cm: Optional[float]) -> Optional[float]:
    """BMI = kilograms / (metres squared). Returns None when there is no height."""
    if not height_cm or height_cm <= 0:
        return None
    kilograms = weight * 0.45359237 if unit == "lb" else weight
    metres = height_cm / 100
    return round(kilograms / (metres * metres), 1)


def after_change_url(request: Request, profile_id: int, reading_type: str) -> str:
    """After an edit or delete: go back to the full list if that's where we came from."""
    back = request.session.get("readings_return")
    if (
        isinstance(back, dict)
        and back.get("profile_id") == profile_id
        and back.get("type") == reading_type
    ):
        return f"/p/{profile_id}/readings/all?type={reading_type}"
    return f"/p/{profile_id}/readings?type={reading_type}"


def check_reading_form(
    reading_type: str,
    value1: str,
    value2: str,
    unit: str,
    context: str,
    measured_date: str,
    measured_time: str,
    notes: str,
):
    """Check everything that was submitted.

    Returns (clean_values, None) when it is fine, or (None, error_message) when not.
    This runs on the server, so it protects the data even if the browser checks fail.
    """
    config = READING_TYPES[reading_type]

    unit = match_option(unit, config["units"])
    if not unit:
        return None, "Please choose a unit from the list."

    first = parse_number(value1)
    if first is None:
        return None, f"Please enter a valid number for the {config['first_name']}."
    low, high = config["first_limits"][unit]
    if not low <= first <= high:
        return None, (
            f"The {config['first_name']} must be between "
            f"{format_number(low)} and {format_number(high)} {unit}."
        )
    first = round(first, 2)

    second = None
    if config["two_values"]:
        second = parse_number(value2)
        if second is None:
            return None, f"Please enter a valid number for the {config['second_name']}."
        low2, high2 = config["second_limits"][unit]
        if not low2 <= second <= high2:
            return None, (
                f"The {config['second_name']} must be between "
                f"{format_number(low2)} and {format_number(high2)} {unit}."
            )
        second = round(second, 2)
        if first <= second:
            return None, (
                f"The {config['first_name']} number must be higher than the "
                f"{config['second_name']} number."
            )

    date_text = (measured_date or "").strip()
    time_text = (measured_time or "").strip()
    if not date_text or not time_text:
        return None, "Please choose both a date and a time."
    try:
        when = datetime.fromisoformat(f"{date_text}T{time_text}")
    except ValueError:
        return None, "Please enter a valid date and time."
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    # The server clock can be in a different time zone from the person using the app,
    # so we allow one day of slack instead of rejecting a reading from "later today".
    if when > datetime.now() + timedelta(days=1):
        return None, "The date and time cannot be in the future."
    if when.year < 1990:
        return None, "That date is too far in the past."

    chosen_context = None
    if (context or "").strip():
        chosen_context = match_option(context, config["contexts"])
        if not chosen_context:
            return None, "Please choose a context from the list."

    text = (notes or "").strip()[:NOTE_MAX]

    return (
        {
            "measured_at": when,
            "value1": first,
            "value2": second,
            "unit": unit,
            "context": chosen_context,
            "notes": text or None,
        },
        None,
    )


def describe_reading(
    reading: HealthReading, config: dict, height_cm: Optional[float]
) -> dict:
    if config["two_values"]:
        text = (
            f"{format_number(reading.value1)}/{format_number(reading.value2)} "
            f"{reading.unit}"
        )
    else:
        text = f"{format_number(reading.value1)} {reading.unit}"

    bmi_text = None
    if config.get("bmi"):
        bmi = compute_bmi(reading.value1, reading.unit, height_cm)
        if bmi is not None:
            bmi_text = format_number(bmi)

    return {
        "id": reading.id,
        "when": reading.measured_at.strftime("%d %b %Y, %H:%M"),
        "text": text,
        "bmi": bmi_text,
        "context": reading.context,
        "notes": reading.notes,
    }


# ---------- Chart (drawn as an SVG picture on the server, no extra libraries) ----------

CHART_W = 600
CHART_H = 250
PAD_L = 48
PAD_R = 20
PAD_T = 16
PAD_B = 40
CHART_COLORS = ["#1b7f79", "#c2410c"]
X_TICKS = 4  # how many dates are labelled along the bottom


def build_chart(unit: str, items: List[HealthReading], config: dict) -> dict:
    """items must be oldest first and all use the same unit.

    Dots are placed by the real date and time, so a long gap between two readings
    looks like a long gap. Four dates are labelled along the bottom axis.
    """
    columns = [[r.value1 for r in items]]
    if config["two_values"]:
        columns.append([r.value2 for r in items])

    everything = [v for column in columns for v in column if v is not None]
    low, high = min(everything), max(everything)
    if low == high:
        low -= 1
        high += 1
    margin = (high - low) * 0.1
    low -= margin
    high += margin

    first_time = items[0].measured_at
    last_time = items[-1].measured_at
    real_span = (last_time - first_time).total_seconds()
    axis_seconds = real_span if real_span > 0 else 3600.0
    pad_seconds = axis_seconds * 0.04
    axis_start = first_time - timedelta(seconds=pad_seconds)
    axis_total = axis_seconds + 2 * pad_seconds

    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B

    def x_for(moment: datetime) -> float:
        fraction = (moment - axis_start).total_seconds() / axis_total
        return round(PAD_L + fraction * plot_w, 1)

    def y_for(value: float) -> float:
        return round(PAD_T + (high - value) / (high - low) * plot_h, 1)

    series = []
    for index, column in enumerate(columns):
        dots = []
        for reading, value in zip(items, column):
            dots.append(
                {
                    "x": x_for(reading.measured_at),
                    "y": y_for(value),
                    "title": (
                        f"{reading.measured_at.strftime('%d %b %Y, %H:%M')}: "
                        f"{format_number(value)} {unit}"
                    ),
                }
            )
        series.append(
            {
                "name": config["series_names"][index],
                "color": CHART_COLORS[index],
                "points": " ".join(f"{d['x']},{d['y']}" for d in dots),
                "dots": dots,
            }
        )

    y_ticks = []
    for step in range(4):
        value = low + (high - low) * step / 3
        y_ticks.append({"y": y_for(value), "label": format_number(round(value, 1))})

    # When everything happened within a few days, include the time so labels differ.
    show_time = real_span < 5 * 86400
    x_ticks = []
    for step in range(X_TICKS):
        moment = first_time + timedelta(seconds=axis_seconds * step / (X_TICKS - 1))
        label = moment.strftime("%d %b %H:%M") if show_time else moment.strftime("%d %b")
        x_ticks.append({"x": x_for(moment), "label": label})

    return {
        "unit": unit,
        "width": CHART_W,
        "height": CHART_H,
        "left": PAD_L,
        "right": CHART_W - PAD_R,
        "top": PAD_T,
        "bottom": CHART_H - PAD_B,
        "series": series,
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "count": len(items),
    }


# ---------- Page rendering ----------


def render_readings_page(
    request: Request,
    session: Session,
    user: User,
    profile: Profile,
    reading_type: str,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    config = READING_TYPES[reading_type]
    rows = load_rows(session, profile.id, reading_type)  # newest first

    total = len(rows)
    items = [
        describe_reading(r, config, profile.height_cm) for r in rows[:RECENT_LIMIT]
    ]

    oldest_first = list(reversed(rows))
    charts = []
    for unit in config["units"]:
        in_unit = [r for r in oldest_first if r.unit == unit][-CHART_POINTS:]
        if len(in_unit) >= 2:
            charts.append(build_chart(unit, in_unit, config))

    bmi_info = None
    if config.get("bmi"):
        latest_bmi = None
        if rows:
            latest_bmi = compute_bmi(rows[0].value1, rows[0].unit, profile.height_cm)
        bmi_info = {
            "height_cm": format_number(profile.height_cm) if profile.height_cm else None,
            "latest_bmi": format_number(latest_bmi) if latest_bmi is not None else None,
            "young": profile.age < 20,
        }

    if values is None:
        values = {
            "measured_date": "",  # the browser fills in today's date and time
            "measured_time": "",
            "value1": "",
            "value2": "",
            "unit": config["default_unit"],
            "context": "",
            "notes": "",
        }

    type_tabs = [
        {"key": key, "label": cfg["label"], "active": key == reading_type}
        for key, cfg in READING_TYPES.items()
    ]

    return templates.TemplateResponse(
        request,
        "readings.html",
        {
            "user": user,
            "profile": profile,
            "reading_type": reading_type,
            "config": config,
            "type_tabs": type_tabs,
            "values": values,
            "items": items,
            "total": total,
            "recent_limit": RECENT_LIMIT,
            "charts": charts,
            "chart_points": CHART_POINTS,
            "bmi_info": bmi_info,
            "flash": request.session.pop("flash", None),
            "error": error,
        },
        status_code=status_code,
    )


def render_all_readings_page(
    request: Request,
    session: Session,
    user: User,
    profile: Profile,
    reading_type: str,
):
    config = READING_TYPES[reading_type]
    rows = load_rows(session, profile.id, reading_type)
    items = [describe_reading(r, config, profile.height_cm) for r in rows]
    return templates.TemplateResponse(
        request,
        "readings_all.html",
        {
            "user": user,
            "profile": profile,
            "reading_type": reading_type,
            "config": config,
            "items": items,
            "total": len(items),
            "flash": request.session.pop("flash", None),
        },
    )


def render_edit_reading_page(
    request: Request,
    user: User,
    profile: Profile,
    reading: HealthReading,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    config = READING_TYPES[reading.reading_type]
    if values is None:
        values = {
            "measured_date": reading.measured_at.strftime("%Y-%m-%d"),
            "measured_time": reading.measured_at.strftime("%H:%M"),
            "value1": format_number(reading.value1),
            "value2": format_number(reading.value2),
            "unit": reading.unit,
            "context": reading.context or "",
            "notes": reading.notes or "",
        }
    return templates.TemplateResponse(
        request,
        "edit_reading.html",
        {
            "user": user,
            "profile": profile,
            "reading": reading,
            "config": config,
            "values": values,
            "error": error,
        },
        status_code=status_code,
    )


# ---------- Routes ----------


@router.get(
    "/p/{profile_id}/readings", response_class=HTMLResponse, include_in_schema=False
)
def readings_page(
    profile_id: int,
    request: Request,
    type: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    request.session.pop("readings_return", None)  # back on the main readings page
    return render_readings_page(request, session, user, profile, pick_type(type))


@router.get(
    "/p/{profile_id}/readings/all",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def readings_all_page(
    profile_id: int,
    request: Request,
    type: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    reading_type = pick_type(type)
    # Remember where we are, so edit and delete bring us back to this full list.
    request.session["readings_return"] = {
        "profile_id": profile_id,
        "type": reading_type,
    }
    return render_all_readings_page(request, session, user, profile, reading_type)


@router.post("/p/{profile_id}/readings/new", include_in_schema=False)
def add_reading_form(
    profile_id: int,
    request: Request,
    reading_type: str = Form(...),
    measured_date: str = Form(""),
    measured_time: str = Form(""),
    value1: str = Form(""),
    value2: str = Form(""),
    unit: str = Form(""),
    context: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    if reading_type not in READING_TYPES:
        raise HTTPException(status_code=400, detail="Unknown reading type.")

    clean, error = check_reading_form(
        reading_type,
        value1,
        value2,
        unit,
        context,
        measured_date,
        measured_time,
        notes,
    )
    if error:
        submitted = {
            "measured_date": measured_date,
            "measured_time": measured_time,
            "value1": value1,
            "value2": value2,
            "unit": unit,
            "context": context,
            "notes": notes.strip(),
        }
        return render_readings_page(
            request,
            session,
            user,
            profile,
            reading_type,
            values=submitted,
            error=error,
            status_code=400,
        )

    reading = HealthReading(
        profile_id=profile_id, reading_type=reading_type, **clean
    )
    session.add(reading)
    session.commit()
    request.session["flash"] = "Reading saved."
    return RedirectResponse(
        url=f"/p/{profile_id}/readings?type={reading_type}", status_code=303
    )


@router.get(
    "/p/{profile_id}/readings/{reading_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_reading_page(
    profile_id: int,
    reading_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    reading = get_owned_reading(reading_id, profile_id, session)
    return render_edit_reading_page(request, user, profile, reading)


@router.post("/p/{profile_id}/readings/{reading_id}/edit", include_in_schema=False)
def edit_reading_form(
    profile_id: int,
    reading_id: int,
    request: Request,
    measured_date: str = Form(""),
    measured_time: str = Form(""),
    value1: str = Form(""),
    value2: str = Form(""),
    unit: str = Form(""),
    context: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    reading = get_owned_reading(reading_id, profile_id, session)

    clean, error = check_reading_form(
        reading.reading_type,
        value1,
        value2,
        unit,
        context,
        measured_date,
        measured_time,
        notes,
    )
    if error:
        submitted = {
            "measured_date": measured_date,
            "measured_time": measured_time,
            "value1": value1,
            "value2": value2,
            "unit": unit,
            "context": context,
            "notes": notes.strip(),
        }
        return render_edit_reading_page(
            request,
            user,
            profile,
            reading,
            values=submitted,
            error=error,
            status_code=400,
        )

    reading.measured_at = clean["measured_at"]
    reading.value1 = clean["value1"]
    reading.value2 = clean["value2"]
    reading.unit = clean["unit"]
    reading.context = clean["context"]
    reading.notes = clean["notes"]
    session.add(reading)
    session.commit()
    request.session["flash"] = "Reading updated."
    return RedirectResponse(
        url=after_change_url(request, profile_id, reading.reading_type),
        status_code=303,
    )


@router.post("/p/{profile_id}/readings/{reading_id}/delete", include_in_schema=False)
def delete_reading_form(
    profile_id: int,
    reading_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    reading = get_owned_reading(reading_id, profile_id, session)
    reading_type = reading.reading_type
    session.delete(reading)
    session.commit()
    request.session["flash"] = "Reading deleted."
    return RedirectResponse(
        url=after_change_url(request, profile_id, reading_type), status_code=303
    )