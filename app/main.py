import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select
from starlette.middleware.sessions import SessionMiddleware

from app.database import create_db_and_tables, get_session
from app.models import (
    ContextNote,
    CycleEntry,
    CycleSetting,
    Profile,
    ProfileCreate,
    SymptomLog,
    User,
    VaccineCreate,
    VaccineRecord,
    VaccineReminder,
)
from app.security import hash_password, verify_password

load_dotenv()
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing. Create a .env file with SECRET_KEY=...")

# Set COOKIE_SECURE=true on the live site (HTTPS). Leave it unset on your laptop.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").strip().lower() == "true"

GENDER_OPTIONS = ["Female", "Male", "Other", "Prefer not to say"]
BLOOD_GROUP_OPTIONS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "Don't know"]
SYMPTOM_OPTIONS = [
    "Irregular periods",
    "Acne",
    "Excess hair growth",
    "Hair thinning",
    "Weight gain",
    "Dark skin patches",
    "Fatigue",
    "Mood changes",
    "Pelvic pain",
]
CONTEXT_OPTIONS = [
    "Stress",
    "Illness",
    "Travel",
    "Big weight change",
    "New medication",
    "Intense exercise",
]
LATE_AFTER_DAYS = 5  # show the late-period card this many days after the estimate
NOTE_MAX = 500
RECENT_LIMIT = 3  # how many entries each section shows before "View all"
HISTORY_TITLES = {
    "cycles": "Logged period dates",
    "symptoms": "Logged symptoms",
    "notes": "Your notes",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=COOKIE_SECURE,
    max_age=60 * 60 * 24 * 7,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- Helpers ----------


def tidy_name(name: str) -> str:
    """'  saida   SHAFIA ' -> 'Saida Shafia'. Also handles hyphens: 'mary-jane' -> 'Mary-Jane'."""
    words = name.split()
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-")) for word in words
    )


def match_option(value: Optional[str], options: List[str]) -> str:
    """Return the option that matches value (ignoring capitals), or '' if none does."""
    wanted = (value or "").strip().lower()
    for option in options:
        if option.lower() == wanted:
            return option
    return ""


def join_options(chosen: List[str], allowed: List[str]) -> str:
    """Keep only allowed options (in their usual order) and join them with commas."""
    return ",".join(option for option in allowed if option in chosen)


def split_options(text: Optional[str]) -> List[str]:
    return [part for part in (text or "").split(",") if part]


def after_change_url(request: Request, profile_id: int) -> str:
    """After an edit or delete: go back to the full list if that's where we came from."""
    back = request.session.get("history_return")
    if (
        isinstance(back, dict)
        and back.get("profile_id") == profile_id
        and back.get("kind") in HISTORY_TITLES
    ):
        return f"/p/{profile_id}/history/{back['kind']}"
    return f"/p/{profile_id}"


def get_current_user_or_none(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return session.get(User, user_id)


def require_user(user: Optional[User] = Depends(get_current_user_or_none)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Please log in first")
    return user


def get_owned_profile(profile_id: int, user: User, session: Session) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None or profile.user_id != user.id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def get_owned_vaccine(
    vaccine_id: int, profile_id: int, session: Session
) -> VaccineRecord:
    record = session.get(VaccineRecord, vaccine_id)
    if record is None or record.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Vaccine record not found")
    return record


def get_owned_cycle(cycle_id: int, profile_id: int, session: Session) -> CycleEntry:
    entry = session.get(CycleEntry, cycle_id)
    if entry is None or entry.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


def get_owned_symptom(
    symptom_id: int, profile_id: int, session: Session
) -> SymptomLog:
    log = session.get(SymptomLog, symptom_id)
    if log is None or log.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    return log


def get_owned_context(
    context_id: int, profile_id: int, session: Session
) -> ContextNote:
    note = session.get(ContextNote, context_id)
    if note is None or note.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


def delete_profile_data(session: Session, profile: Profile) -> None:
    """Delete a profile and everything attached to it. The caller commits."""
    for model in (VaccineRecord, CycleEntry, SymptomLog, ContextNote, CycleSetting):
        rows = session.exec(
            select(model).where(model.profile_id == profile.id)
        ).all()
        for row in rows:
            session.delete(row)
    session.delete(profile)


def tracks_cycle(profile: Profile) -> bool:
    return profile.gender.strip().lower() == "female"


def get_cycle_active(session: Session, profile_id: int) -> bool:
    setting = session.exec(
        select(CycleSetting).where(CycleSetting.profile_id == profile_id)
    ).first()
    return True if setting is None else setting.tracking_active


def cycle_enabled(session: Session, profile: Profile) -> bool:
    return tracks_cycle(profile) and get_cycle_active(session, profile.id)


def require_cycle_access(session: Session, profile: Profile) -> None:
    if not cycle_enabled(session, profile):
        raise HTTPException(status_code=404, detail="Not available for this profile")


def build_cycle_info(entries: List[CycleEntry]) -> Optional[dict]:
    starts = sorted(e.start_date for e in entries)
    if len(starts) < 2:
        return None
    gaps = [(later - earlier).days for earlier, later in zip(starts, starts[1:])]
    recent = gaps[-6:]
    average = round(sum(recent) / len(recent))
    if average < 1:
        return None
    next_start = starts[-1] + timedelta(days=average)
    return {
        "average_days": average,
        "cycles_used": len(recent),
        "gaps": recent,
        "next_start": next_start,
        "days_until": (next_start - date.today()).days,
    }


def build_late_info(
    profile: Profile,
    cycle_info: Optional[dict],
    symptom_logs: List[SymptomLog],
    context_notes: List[ContextNote],
) -> Optional[dict]:
    """Explain a late period using only what the person has logged. Never a diagnosis."""
    if cycle_info is None:
        return None
    days_late = -cycle_info["days_until"]
    if days_late < LATE_AFTER_DAYS:
        return None

    today = date.today()
    factors: List[str] = []

    gaps = cycle_info["gaps"]
    average = cycle_info["average_days"]
    if len(gaps) >= 2 and max(gaps) - min(gaps) >= 8:
        spread = max(gaps) - min(gaps)
        factors.append(
            f"Your logged cycles have varied by up to {spread} days in length, "
            "so a late period is not unusual for you."
        )
    elif average > 35:
        factors.append(f"Your cycles have been long (about {average} days on average).")
    elif average < 21:
        factors.append(f"Your cycles have been short (about {average} days on average).")

    seen_symptoms: List[str] = []
    for log in symptom_logs:
        if (today - log.log_date).days <= 90:
            for symptom in split_options(log.symptoms):
                if symptom not in seen_symptoms:
                    seen_symptoms.append(symptom)
    if seen_symptoms:
        factors.append(
            "You have logged symptoms that can go with hormonal conditions such as PCOS ("
            + ", ".join(s.lower() for s in seen_symptoms[:5])
            + "). These can make periods irregular. Only a doctor can tell whether "
            "that applies to you."
        )

    if profile.age <= 19:
        factors.append(
            "Cycles are often irregular in the first few years after periods begin."
        )
    elif profile.age >= 45:
        factors.append(
            "Cycles often become less regular in the years before menopause."
        )

    recent_tags: List[str] = []
    for note in context_notes:
        if (today - note.note_date).days <= 60:
            for tag in split_options(note.tags):
                if tag not in recent_tags:
                    recent_tags.append(tag)
    if recent_tags:
        ordered = [t for t in CONTEXT_OPTIONS if t in recent_tags]
        factors.append(
            "You noted these recently: "
            + ", ".join(t.lower() for t in ordered)
            + ". Things like these can delay a period."
        )

    return {
        "expected": cycle_info["next_start"],
        "days_late": days_late,
        "factors": factors,
    }


def build_reminders(
    session: Session, profile_id: int, days: int
) -> List[VaccineReminder]:
    today = date.today()
    cutoff = today + timedelta(days=days)

    statement = select(VaccineRecord).where(
        VaccineRecord.profile_id == profile_id,
        col(VaccineRecord.next_due).is_not(None),
        VaccineRecord.next_due <= cutoff,
    )

    reminders = []
    for record in session.exec(statement).all():
        days_left = (record.next_due - today).days
        status = "overdue" if days_left < 0 else "due soon"
        reminders.append(
            VaccineReminder(
                vaccine_name=record.vaccine_name,
                next_due=record.next_due,
                days_left=days_left,
                status=status,
            )
        )

    reminders.sort(key=lambda r: r.days_left)
    return reminders


def render_profile_page(
    request: Request,
    session: Session,
    user: User,
    profile: Profile,
    error: Optional[str] = None,
    cycle_error: Optional[str] = None,
    symptom_error: Optional[str] = None,
    context_error: Optional[str] = None,
    status_code: int = 200,
):
    vaccines = session.exec(
        select(VaccineRecord)
        .where(VaccineRecord.profile_id == profile.id)
        .order_by(col(VaccineRecord.date_taken).desc())
    ).all()
    reminders = build_reminders(session, profile.id, days=90)

    is_female = tracks_cycle(profile)
    show_cycle = is_female and get_cycle_active(session, profile.id)
    cycles: List[CycleEntry] = []
    cycle_info = None
    late_info = None
    symptom_logs: List[SymptomLog] = []
    context_notes: List[ContextNote] = []
    if show_cycle:
        cycles = session.exec(
            select(CycleEntry)
            .where(CycleEntry.profile_id == profile.id)
            .order_by(col(CycleEntry.start_date).desc())
        ).all()
        symptom_logs = session.exec(
            select(SymptomLog)
            .where(SymptomLog.profile_id == profile.id)
            .order_by(col(SymptomLog.log_date).desc(), col(SymptomLog.id).desc())
        ).all()
        context_notes = session.exec(
            select(ContextNote)
            .where(ContextNote.profile_id == profile.id)
            .order_by(col(ContextNote.note_date).desc(), col(ContextNote.id).desc())
        ).all()
        # The estimate and the late notice use EVERY logged entry.
        cycle_info = build_cycle_info(cycles)
        late_info = build_late_info(profile, cycle_info, symptom_logs, context_notes)

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": user,
            "profile": profile,
            "vaccines": vaccines,
            "reminders": reminders,
            "is_female": is_female,
            "show_cycle": show_cycle,
            "cycle_info": cycle_info,
            "late_info": late_info,
            # The card only shows the latest few; "View all" opens the full list.
            "recent_limit": RECENT_LIMIT,
            "recent_cycles": cycles[:RECENT_LIMIT],
            "cycle_total": len(cycles),
            "recent_symptoms": symptom_logs[:RECENT_LIMIT],
            "symptom_total": len(symptom_logs),
            "recent_notes": context_notes[:RECENT_LIMIT],
            "note_total": len(context_notes),
            "symptom_options": SYMPTOM_OPTIONS,
            "context_options": CONTEXT_OPTIONS,
            "today": date.today().isoformat(),
            "error": error,
            "cycle_error": cycle_error,
            "symptom_error": symptom_error,
            "context_error": context_error,
        },
        status_code=status_code,
    )


def render_edit_page(
    request: Request,
    user: User,
    profile: Profile,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            "name": profile.name,
            "age": profile.age,
            "gender": match_option(profile.gender, GENDER_OPTIONS),
            "blood_group": match_option(profile.blood_group, BLOOD_GROUP_OPTIONS),
            "allergies": profile.allergies or "",
        }
    return templates.TemplateResponse(
        request,
        "edit_profile.html",
        {
            "user": user,
            "profile": profile,
            "values": values,
            "gender_options": GENDER_OPTIONS,
            "blood_group_options": BLOOD_GROUP_OPTIONS,
            "error": error,
        },
        status_code=status_code,
    )


def render_edit_vaccine_page(
    request: Request,
    user: User,
    profile: Profile,
    record: VaccineRecord,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            "vaccine_name": record.vaccine_name,
            "date_taken": record.date_taken.isoformat(),
            "next_due": record.next_due.isoformat() if record.next_due else "",
            "notes": record.notes or "",
        }
    return templates.TemplateResponse(
        request,
        "edit_vaccine.html",
        {
            "user": user,
            "profile": profile,
            "record": record,
            "values": values,
            "error": error,
        },
        status_code=status_code,
    )


def render_edit_cycle_page(
    request: Request,
    user: User,
    profile: Profile,
    entry: CycleEntry,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            "start_date": entry.start_date.isoformat(),
            "end_date": entry.end_date.isoformat() if entry.end_date else "",
            "notes": entry.notes or "",
        }
    return templates.TemplateResponse(
        request,
        "edit_cycle.html",
        {
            "user": user,
            "profile": profile,
            "entry": entry,
            "values": values,
            "error": error,
        },
        status_code=status_code,
    )


def render_edit_symptom_page(
    request: Request,
    user: User,
    profile: Profile,
    log: SymptomLog,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            "log_date": log.log_date.isoformat(),
            "symptoms": split_options(log.symptoms),
            "notes": log.notes or "",
        }
    return templates.TemplateResponse(
        request,
        "edit_symptom.html",
        {
            "user": user,
            "profile": profile,
            "log": log,
            "values": values,
            "symptom_options": SYMPTOM_OPTIONS,
            "error": error,
        },
        status_code=status_code,
    )


def render_edit_context_page(
    request: Request,
    user: User,
    profile: Profile,
    note: ContextNote,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    if values is None:
        values = {
            "note_date": note.note_date.isoformat(),
            "tags": split_options(note.tags),
            "note": note.note or "",
        }
    return templates.TemplateResponse(
        request,
        "edit_context.html",
        {
            "user": user,
            "profile": profile,
            "note": note,
            "values": values,
            "context_options": CONTEXT_OPTIONS,
            "error": error,
        },
        status_code=status_code,
    )


def render_account_page(
    request: Request,
    session: Session,
    user: User,
    error: Optional[str] = None,
    status_code: int = 200,
):
    profiles = session.exec(select(Profile).where(Profile.user_id == user.id)).all()
    return templates.TemplateResponse(
        request,
        "account.html",
        {"user": user, "profile_count": len(profiles), "error": error},
        status_code=status_code,
    )


# ---------- Web pages: home, signup, login ----------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    profiles = []
    if user is not None:
        profiles = session.exec(
            select(Profile).where(Profile.user_id == user.id)
        ).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "profiles": profiles,
            "gender_options": GENDER_OPTIONS,
            "blood_group_options": BLOOD_GROUP_OPTIONS,
        },
    )


@app.get("/signup", response_class=HTMLResponse, include_in_schema=False)
def signup_page(
    request: Request, user: Optional[User] = Depends(get_current_user_or_none)
):
    if user is not None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "signup.html", {})


@app.post("/signup", include_in_schema=False)
def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    email = email.strip().lower()
    error = None
    if "@" not in email or "." not in email:
        error = "Please enter a valid email address."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif session.exec(select(User).where(User.email == email)).first():
        error = "An account with this email already exists."

    if error:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": error, "email": email},
            status_code=400,
        )

    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(
    request: Request, user: Optional[User] = Depends(get_current_user_or_none)
):
    if user is not None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login", include_in_schema=False)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    email = email.strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Wrong email or password.", "email": email},
            status_code=400,
        )

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------- Web pages: account ----------


@app.get("/account", response_class=HTMLResponse, include_in_schema=False)
def account_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return render_account_page(request, session, user)


@app.post("/account/delete", include_in_schema=False)
def delete_account_form(
    request: Request,
    password: str = Form(...),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    if not verify_password(password, user.password_hash):
        return render_account_page(
            request,
            session,
            user,
            error="Wrong password. Your account was not deleted.",
            status_code=400,
        )

    profiles = session.exec(select(Profile).where(Profile.user_id == user.id)).all()
    for profile in profiles:
        delete_profile_data(session, profile)
    session.delete(user)
    session.commit()

    request.session.clear()
    return RedirectResponse(url="/signup", status_code=303)


# ---------- Web pages: profiles ----------


@app.post("/profiles/new", include_in_schema=False)
def create_profile_form(
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    blood_group: str = Form(...),
    allergies: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    name = tidy_name(name)
    if not name:
        raise HTTPException(status_code=400, detail="Please enter a name.")
    if blood_group not in BLOOD_GROUP_OPTIONS:
        raise HTTPException(status_code=400, detail="Please choose a valid blood type.")
    if gender not in GENDER_OPTIONS:
        gender = "Prefer not to say"

    profile = Profile(
        name=name,
        age=age,
        gender=gender,
        blood_group=blood_group,
        allergies=allergies.strip() or None,
        user_id=user.id,
    )
    session.add(profile)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/p/{profile_id}", response_class=HTMLResponse, include_in_schema=False)
def profile_page(
    profile_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    request.session.pop("history_return", None)  # back on the main page
    return render_profile_page(request, session, user, profile)


@app.get(
    "/p/{profile_id}/history/{kind}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def history_page(
    profile_id: int,
    kind: str,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    if kind not in HISTORY_TITLES:
        raise HTTPException(status_code=404, detail="Page not found")
    require_cycle_access(session, profile)

    # Remember where we are, so edit and delete bring us back here.
    request.session["history_return"] = {"profile_id": profile_id, "kind": kind}

    if kind == "cycles":
        items = session.exec(
            select(CycleEntry)
            .where(CycleEntry.profile_id == profile_id)
            .order_by(col(CycleEntry.start_date).desc())
        ).all()
    elif kind == "symptoms":
        items = session.exec(
            select(SymptomLog)
            .where(SymptomLog.profile_id == profile_id)
            .order_by(col(SymptomLog.log_date).desc(), col(SymptomLog.id).desc())
        ).all()
    else:
        items = session.exec(
            select(ContextNote)
            .where(ContextNote.profile_id == profile_id)
            .order_by(col(ContextNote.note_date).desc(), col(ContextNote.id).desc())
        ).all()

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "user": user,
            "profile": profile,
            "kind": kind,
            "title": HISTORY_TITLES[kind],
            "items": items,
        },
    )


@app.get("/p/{profile_id}/edit", response_class=HTMLResponse, include_in_schema=False)
def edit_profile_page(
    profile_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    return render_edit_page(request, user, profile)


@app.post("/p/{profile_id}/edit", include_in_schema=False)
def edit_profile_form(
    profile_id: int,
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    blood_group: str = Form(...),
    allergies: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)

    name = tidy_name(name)
    submitted = {
        "name": name,
        "age": age,
        "gender": match_option(gender, GENDER_OPTIONS),
        "blood_group": match_option(blood_group, BLOOD_GROUP_OPTIONS),
        "allergies": allergies.strip(),
    }

    error = None
    if not name:
        error = "Please enter a name."
    elif age < 0 or age > 120:
        error = "Age must be between 0 and 120."
    elif gender not in GENDER_OPTIONS:
        error = "Please choose a gender from the list."
    elif blood_group not in BLOOD_GROUP_OPTIONS:
        error = "Please choose a blood type from the list."

    if error:
        return render_edit_page(
            request, user, profile, values=submitted, error=error, status_code=400
        )

    profile.name = name
    profile.age = age
    profile.gender = gender
    profile.blood_group = blood_group
    profile.allergies = allergies.strip() or None
    session.add(profile)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.post("/p/{profile_id}/delete", include_in_schema=False)
def delete_profile_form(
    profile_id: int,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    delete_profile_data(session, profile)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- Web pages: vaccines ----------


@app.post("/p/{profile_id}/vaccines/new", include_in_schema=False)
def add_vaccine_form(
    profile_id: int,
    request: Request,
    vaccine_name: str = Form(...),
    date_taken: str = Form(...),
    next_due: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)

    def show_error(message: str):
        return render_profile_page(
            request, session, user, profile, error=message, status_code=400
        )

    name = vaccine_name.strip()
    if not name:
        return show_error("Please enter a vaccine name.")

    try:
        taken = date.fromisoformat(date_taken)
        due = date.fromisoformat(next_due) if next_due else None
    except ValueError:
        return show_error("Please enter valid dates.")

    if due is not None and due < taken:
        return show_error("The next due date cannot be before the date taken.")

    record = VaccineRecord(
        profile_id=profile_id,
        vaccine_name=name,
        date_taken=taken,
        next_due=due,
        notes=notes.strip() or None,
    )
    session.add(record)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.get(
    "/p/{profile_id}/vaccines/{vaccine_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_vaccine_page(
    profile_id: int,
    vaccine_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    record = get_owned_vaccine(vaccine_id, profile_id, session)
    return render_edit_vaccine_page(request, user, profile, record)


@app.post("/p/{profile_id}/vaccines/{vaccine_id}/edit", include_in_schema=False)
def edit_vaccine_form(
    profile_id: int,
    vaccine_id: int,
    request: Request,
    vaccine_name: str = Form(...),
    date_taken: str = Form(...),
    next_due: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    record = get_owned_vaccine(vaccine_id, profile_id, session)

    submitted = {
        "vaccine_name": vaccine_name.strip(),
        "date_taken": date_taken,
        "next_due": next_due,
        "notes": notes.strip(),
    }

    def show_error(message: str):
        return render_edit_vaccine_page(
            request,
            user,
            profile,
            record,
            values=submitted,
            error=message,
            status_code=400,
        )

    if not submitted["vaccine_name"]:
        return show_error("Please enter a vaccine name.")

    try:
        taken = date.fromisoformat(date_taken)
        due = date.fromisoformat(next_due) if next_due else None
    except ValueError:
        return show_error("Please enter valid dates.")

    if due is not None and due < taken:
        return show_error("The next due date cannot be before the date taken.")

    record.vaccine_name = submitted["vaccine_name"]
    record.date_taken = taken
    record.next_due = due
    record.notes = submitted["notes"] or None
    session.add(record)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.post("/p/{profile_id}/vaccines/{vaccine_id}/delete", include_in_schema=False)
def delete_vaccine_form(
    profile_id: int,
    vaccine_id: int,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    record = get_owned_vaccine(vaccine_id, profile_id, session)
    session.delete(record)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


# ---------- Web pages: cycle tracker ----------


@app.post("/p/{profile_id}/cycle/tracking", include_in_schema=False)
def set_cycle_tracking(
    profile_id: int,
    active: str = Form(...),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    if not tracks_cycle(profile):
        raise HTTPException(status_code=404, detail="Not available for this profile")

    wanted = active == "1"
    setting = session.exec(
        select(CycleSetting).where(CycleSetting.profile_id == profile_id)
    ).first()
    if setting is None:
        setting = CycleSetting(profile_id=profile_id, tracking_active=wanted)
    else:
        setting.tracking_active = wanted
    session.add(setting)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.post("/p/{profile_id}/cycles/new", include_in_schema=False)
def add_cycle_form(
    profile_id: int,
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)

    def show_error(message: str):
        return render_profile_page(
            request,
            session,
            user,
            profile,
            cycle_error=message,
            status_code=400,
        )

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return show_error("Please enter valid dates.")

    if start > date.today():
        return show_error("The start date cannot be in the future.")
    if end is not None and end < start:
        return show_error("The end date cannot be before the start date.")

    entry = CycleEntry(
        profile_id=profile_id,
        start_date=start,
        end_date=end,
        notes=notes.strip() or None,
    )
    session.add(entry)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.get(
    "/p/{profile_id}/cycles/{cycle_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_cycle_page(
    profile_id: int,
    cycle_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)
    entry = get_owned_cycle(cycle_id, profile_id, session)
    return render_edit_cycle_page(request, user, profile, entry)


@app.post("/p/{profile_id}/cycles/{cycle_id}/edit", include_in_schema=False)
def edit_cycle_form(
    profile_id: int,
    cycle_id: int,
    request: Request,
    start_date: str = Form(...),
    end_date: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)
    entry = get_owned_cycle(cycle_id, profile_id, session)

    submitted = {
        "start_date": start_date,
        "end_date": end_date,
        "notes": notes.strip(),
    }

    def show_error(message: str):
        return render_edit_cycle_page(
            request,
            user,
            profile,
            entry,
            values=submitted,
            error=message,
            status_code=400,
        )

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date) if end_date else None
    except ValueError:
        return show_error("Please enter valid dates.")

    if start > date.today():
        return show_error("The start date cannot be in the future.")
    if end is not None and end < start:
        return show_error("The end date cannot be before the start date.")

    entry.start_date = start
    entry.end_date = end
    entry.notes = submitted["notes"] or None
    session.add(entry)
    session.commit()
    return RedirectResponse(url=after_change_url(request, profile_id), status_code=303)


@app.post("/p/{profile_id}/cycles/{cycle_id}/delete", include_in_schema=False)
def delete_cycle_form(
    profile_id: int,
    cycle_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    entry = get_owned_cycle(cycle_id, profile_id, session)
    session.delete(entry)
    session.commit()
    return RedirectResponse(url=after_change_url(request, profile_id), status_code=303)


# ---------- Web pages: symptom log ----------


@app.post("/p/{profile_id}/symptoms/new", include_in_schema=False)
def add_symptom_form(
    profile_id: int,
    request: Request,
    log_date: str = Form(...),
    symptoms: List[str] = Form(default=[]),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)

    def show_error(message: str):
        return render_profile_page(
            request,
            session,
            user,
            profile,
            symptom_error=message,
            status_code=400,
        )

    try:
        logged = date.fromisoformat(log_date)
    except ValueError:
        return show_error("Please enter a valid date.")
    if logged > date.today():
        return show_error("The date cannot be in the future.")

    chosen = join_options(symptoms, SYMPTOM_OPTIONS)
    text = notes.strip()[:NOTE_MAX]
    if not chosen and not text:
        return show_error("Please tick at least one symptom or write a note.")

    log = SymptomLog(
        profile_id=profile_id,
        log_date=logged,
        symptoms=chosen,
        notes=text or None,
    )
    session.add(log)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.get(
    "/p/{profile_id}/symptoms/{symptom_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_symptom_page(
    profile_id: int,
    symptom_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)
    log = get_owned_symptom(symptom_id, profile_id, session)
    return render_edit_symptom_page(request, user, profile, log)


@app.post("/p/{profile_id}/symptoms/{symptom_id}/edit", include_in_schema=False)
def edit_symptom_form(
    profile_id: int,
    symptom_id: int,
    request: Request,
    log_date: str = Form(...),
    symptoms: List[str] = Form(default=[]),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)
    log = get_owned_symptom(symptom_id, profile_id, session)

    chosen = join_options(symptoms, SYMPTOM_OPTIONS)
    text = notes.strip()[:NOTE_MAX]
    submitted = {
        "log_date": log_date,
        "symptoms": split_options(chosen),
        "notes": text,
    }

    def show_error(message: str):
        return render_edit_symptom_page(
            request,
            user,
            profile,
            log,
            values=submitted,
            error=message,
            status_code=400,
        )

    try:
        logged = date.fromisoformat(log_date)
    except ValueError:
        return show_error("Please enter a valid date.")
    if logged > date.today():
        return show_error("The date cannot be in the future.")
    if not chosen and not text:
        return show_error("Please tick at least one symptom or write a note.")

    log.log_date = logged
    log.symptoms = chosen
    log.notes = text or None
    session.add(log)
    session.commit()
    return RedirectResponse(url=after_change_url(request, profile_id), status_code=303)


@app.post("/p/{profile_id}/symptoms/{symptom_id}/delete", include_in_schema=False)
def delete_symptom_form(
    profile_id: int,
    symptom_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    log = get_owned_symptom(symptom_id, profile_id, session)
    session.delete(log)
    session.commit()
    return RedirectResponse(url=after_change_url(request, profile_id), status_code=303)


# ---------- Web pages: "What's been going on?" notes ----------


@app.post("/p/{profile_id}/context/new", include_in_schema=False)
def add_context_form(
    profile_id: int,
    request: Request,
    note_date: str = Form(...),
    tags: List[str] = Form(default=[]),
    note: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)

    def show_error(message: str):
        return render_profile_page(
            request,
            session,
            user,
            profile,
            context_error=message,
            status_code=400,
        )

    try:
        noted = date.fromisoformat(note_date)
    except ValueError:
        return show_error("Please enter a valid date.")
    if noted > date.today():
        return show_error("The date cannot be in the future.")

    chosen = join_options(tags, CONTEXT_OPTIONS)
    text = note.strip()[:NOTE_MAX]
    if not chosen and not text:
        return show_error("Please tick something or write a few words.")

    entry = ContextNote(
        profile_id=profile_id,
        note_date=noted,
        tags=chosen,
        note=text or None,
    )
    session.add(entry)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}", status_code=303)


@app.get(
    "/p/{profile_id}/context/{context_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_context_page(
    profile_id: int,
    context_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)
    entry = get_owned_context(context_id, profile_id, session)
    return render_edit_context_page(request, user, profile, entry)


@app.post("/p/{profile_id}/context/{context_id}/edit", include_in_schema=False)
def edit_context_form(
    profile_id: int,
    context_id: int,
    request: Request,
    note_date: str = Form(...),
    tags: List[str] = Form(default=[]),
    note: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    require_cycle_access(session, profile)
    entry = get_owned_context(context_id, profile_id, session)

    chosen = join_options(tags, CONTEXT_OPTIONS)
    text = note.strip()[:NOTE_MAX]
    submitted = {
        "note_date": note_date,
        "tags": split_options(chosen),
        "note": text,
    }

    def show_error(message: str):
        return render_edit_context_page(
            request,
            user,
            profile,
            entry,
            values=submitted,
            error=message,
            status_code=400,
        )

    try:
        noted = date.fromisoformat(note_date)
    except ValueError:
        return show_error("Please enter a valid date.")
    if noted > date.today():
        return show_error("The date cannot be in the future.")
    if not chosen and not text:
        return show_error("Please tick something or write a few words.")

    entry.note_date = noted
    entry.tags = chosen
    entry.note = text or None
    session.add(entry)
    session.commit()
    return RedirectResponse(url=after_change_url(request, profile_id), status_code=303)


@app.post("/p/{profile_id}/context/{context_id}/delete", include_in_schema=False)
def delete_context_form(
    profile_id: int,
    context_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    entry = get_owned_context(context_id, profile_id, session)
    session.delete(entry)
    session.commit()
    return RedirectResponse(url=after_change_url(request, profile_id), status_code=303)


# ---------- API routes (login required, your own data only) ----------


@app.post("/profiles", response_model=Profile)
def create_profile(
    data: ProfileCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    values = data.model_dump()
    values["name"] = tidy_name(values["name"])
    profile = Profile(**values, user_id=user.id)
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@app.get("/profiles", response_model=List[Profile])
def list_profiles(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    return session.exec(select(Profile).where(Profile.user_id == user.id)).all()


@app.post("/profiles/{profile_id}/vaccines", response_model=VaccineRecord)
def add_vaccine(
    profile_id: int,
    vaccine: VaccineCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_owned_profile(profile_id, user, session)
    record = VaccineRecord(**vaccine.model_dump(), profile_id=profile_id)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@app.get("/profiles/{profile_id}/vaccines", response_model=List[VaccineRecord])
def list_vaccines(
    profile_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_owned_profile(profile_id, user, session)
    statement = select(VaccineRecord).where(VaccineRecord.profile_id == profile_id)
    return session.exec(statement).all()


@app.get("/profiles/{profile_id}/reminders", response_model=List[VaccineReminder])
def get_reminders(
    profile_id: int,
    days: int = 30,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    get_owned_profile(profile_id, user, session)
    return build_reminders(session, profile_id, days)