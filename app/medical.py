"""Personal medical history: conditions, medications, allergies, surgeries and
family history.

Every section uses the same table (MedicalHistoryEntry) and the same pages. What
differs between sections lives in KINDS below, so adding a section later means adding
one more entry there.

Column meanings: title is the main name, date_a and date_b are dates, status is a
choice (such as Ongoing or Severe), detail1 and detail2 are short text, and notes is
longer text. Which of them a section uses is listed in its "fields".
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import get_session
from app.models import MedicalHistoryEntry, Profile, User

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SHORT_MAX = 200
TITLE_MAX = 120
NOTES_MAX = 1000


def field(
    name: str,
    label: str,
    short: str,
    field_type: str = "text",
    required: bool = False,
    choices: Optional[List[str]] = None,
    no_future: bool = False,
    max_length: int = SHORT_MAX,
) -> dict:
    return {
        "name": name,
        "label": label,
        "short": short,
        "type": field_type,
        "required": required,
        "choices": choices or [],
        "no_future": no_future,
        "max": max_length,
    }


KINDS = {
    "condition": {
        "label": "Conditions",
        "singular": "condition",
        "add_label": "+ Add a condition",
        "intro": (
            "Illnesses a doctor has diagnosed, past or ongoing, such as diabetes, "
            "asthma, high blood pressure or a thyroid disorder."
        ),
        "empty": "No conditions recorded yet.",
        "fields": [
            field("title", "Condition", "Condition", required=True, max_length=TITLE_MAX),
            field("date_a", "Diagnosis date (optional)", "Diagnosed", "date", no_future=True),
            field(
                "status",
                "Status",
                "Status",
                "select",
                required=True,
                choices=["Ongoing", "Under treatment", "Resolved"],
            ),
            field(
                "notes",
                "Doctor's notes (optional)",
                "Notes",
                "textarea",
                max_length=NOTES_MAX,
            ),
        ],
    },
    "medication": {
        "label": "Medications",
        "singular": "medicine",
        "add_label": "+ Add a medicine",
        "intro": "Medicines you take or have taken.",
        "note": (
            "Write the dosage exactly as your doctor or pharmacist told you. This app "
            "only records medicines. It never suggests or changes a dose."
        ),
        "empty": "No medicines recorded yet.",
        "fields": [
            field("title", "Medicine name", "Medicine", required=True, max_length=TITLE_MAX),
            field("detail1", "Dosage (optional)", "Dosage"),
            field("date_a", "Start date (optional)", "Started", "date", no_future=True),
            field("date_b", "End date (optional)", "Ended", "date"),
            field("detail2", "Reason for taking it (optional)", "For"),
            field(
                "status",
                "Status",
                "Status",
                "select",
                required=True,
                choices=["Current", "Discontinued"],
            ),
            field("notes", "Notes (optional)", "Notes", "textarea", max_length=NOTES_MAX),
        ],
    },
    "allergy": {
        "label": "Allergies and reactions",
        "singular": "allergy",
        "add_label": "+ Add an allergy",
        "intro": "Allergies to medicines, food or other things, and what happened.",
        "empty": "No allergies recorded yet.",
        "fields": [
            field(
                "title",
                "Allergen (what causes the reaction)",
                "Allergen",
                required=True,
                max_length=TITLE_MAX,
            ),
            field(
                "detail2",
                "Type (optional)",
                "Type",
                "select",
                choices=["Medicine", "Food", "Other"],
            ),
            field("detail1", "Reaction experienced (optional)", "Reaction"),
            field(
                "status",
                "Severity",
                "Severity",
                "select",
                required=True,
                choices=["Mild", "Moderate", "Severe", "Not yet assessed"],
            ),
            field("notes", "Notes (optional)", "Notes", "textarea", max_length=NOTES_MAX),
        ],
    },
    "surgery": {
        "label": "Surgeries and hospital stays",
        "singular": "surgery or hospital stay",
        "add_label": "+ Add a surgery or hospital stay",
        "intro": "Operations, hospital stays and other major procedures.",
        "empty": "Nothing recorded yet.",
        "fields": [
            field(
                "title",
                "Procedure or reason for admission",
                "Procedure",
                required=True,
                max_length=TITLE_MAX,
            ),
            field(
                "detail2",
                "Type (optional)",
                "Type",
                "select",
                choices=["Surgery", "Hospital stay", "Other procedure"],
            ),
            field("date_a", "Date (optional)", "Date", "date", no_future=True),
            field("detail1", "Hospital (optional)", "Hospital"),
            field(
                "notes",
                "Complications or recovery notes (optional)",
                "Notes",
                "textarea",
                max_length=NOTES_MAX,
            ),
        ],
    },
    "family": {
        "label": "Family medical history",
        "singular": "family history entry",
        "add_label": "+ Add a family history entry",
        "intro": (
            "Conditions in close relatives, such as diabetes, heart disease or "
            "inherited conditions."
        ),
        "empty": "No family history recorded yet.",
        "fields": [
            field("title", "Condition", "Condition", required=True, max_length=TITLE_MAX),
            field(
                "detail1",
                "Relative",
                "Relative",
                "select",
                required=True,
                choices=[
                    "Mother",
                    "Father",
                    "Sibling",
                    "Grandparent",
                    "Aunt or uncle",
                    "Child",
                    "Other",
                ],
            ),
            field(
                "detail2",
                "Approximate age when it started (optional)",
                "Age when it started",
                "age",
            ),
            field("notes", "Notes (optional)", "Notes", "textarea", max_length=NOTES_MAX),
        ],
    },
}

# Lower number = shown first. Entries with no status come after, newest date first.
STATUS_RANK = {
    "Ongoing": 0,
    "Current": 0,
    "Severe": 0,
    "Under treatment": 1,
    "Moderate": 1,
    "Resolved": 2,
    "Discontinued": 2,
    "Mild": 2,
    "Not yet assessed": 3,
}
BADGE_CLASSES = {"Severe": "badge overdue", "Moderate": "badge soon"}


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


def get_owned_entry(
    entry_id: int, profile_id: int, session: Session
) -> MedicalHistoryEntry:
    entry = session.get(MedicalHistoryEntry, entry_id)
    if entry is None or entry.profile_id != profile_id or entry.kind not in KINDS:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


def match_option(value: Optional[str], options: List[str]) -> str:
    """Return the option that matches value (ignoring capitals), or '' if none does."""
    wanted = (value or "").strip().lower()
    for option in options:
        if option.lower() == wanted:
            return option
    return ""


def plain_label(f: dict) -> str:
    return f["label"].replace(" (optional)", "")


def entry_sort_key(entry: MedicalHistoryEntry):
    rank = STATUS_RANK.get(entry.status or "", 4)
    when = entry.date_a.toordinal() if entry.date_a else 0
    return (rank, -when, -(entry.id or 0))


def check_entry(kind: str, raw: dict):
    """Check one submitted entry.

    Returns (clean_values, None) when it is fine, or (None, error_message) when not.
    This runs on the server, so it protects the data even if the browser checks fail.
    """
    spec = KINDS[kind]
    today = date.today()
    clean = {
        "title": None,
        "date_a": None,
        "date_b": None,
        "status": None,
        "detail1": None,
        "detail2": None,
        "notes": None,
    }

    for f in spec["fields"]:
        name = f["name"]
        text = (raw.get(name) or "").strip()
        if not text:
            if f["required"]:
                return None, f"Please fill in: {plain_label(f)}."
            continue

        if f["type"] == "date":
            try:
                value = date.fromisoformat(text)
            except ValueError:
                return None, f"Please enter a valid date for: {plain_label(f)}."
            if value.year < 1900:
                return None, f"That date is too far in the past: {plain_label(f)}."
            if f["no_future"] and value > today:
                return None, f"The date cannot be in the future: {plain_label(f)}."
            clean[name] = value
        elif f["type"] == "select":
            choice = match_option(text, f["choices"])
            if not choice:
                return None, f"Please choose a valid option for: {plain_label(f)}."
            clean[name] = choice
        elif f["type"] == "age":
            if not text.isdigit() or int(text) > 120:
                return None, f"{plain_label(f)} must be a whole number from 0 to 120."
            clean[name] = str(int(text))
        else:
            clean[name] = text[: f["max"]]

    if clean["date_a"] and clean["date_b"] and clean["date_b"] < clean["date_a"]:
        return None, "The end date cannot be before the start date."

    return clean, None


def describe_entry(entry: MedicalHistoryEntry, spec: dict) -> dict:
    parts = []
    for f in spec["fields"]:
        name = f["name"]
        if name in ("title", "notes", "status"):
            continue
        value = getattr(entry, name)
        if value in (None, ""):
            continue
        if isinstance(value, date):
            value = value.strftime("%d %b %Y")
        parts.append(f"{f['short']}: {value}")
    return {
        "id": entry.id,
        "title": entry.title,
        "line": " · ".join(parts),
        "badge": entry.status,
        "badge_class": BADGE_CLASSES.get(entry.status or "", "tag"),
        "notes": entry.notes,
    }


def entry_form_values(entry: MedicalHistoryEntry) -> dict:
    return {
        "title": entry.title or "",
        "date_a": entry.date_a.isoformat() if entry.date_a else "",
        "date_b": entry.date_b.isoformat() if entry.date_b else "",
        "status": entry.status or "",
        "detail1": entry.detail1 or "",
        "detail2": entry.detail2 or "",
        "notes": entry.notes or "",
    }


# ---------- Page rendering ----------


def render_medical_page(
    request: Request,
    session: Session,
    user: User,
    profile: Profile,
    error_kind: Optional[str] = None,
    error: Optional[str] = None,
    form_values: Optional[dict] = None,
    status_code: int = 200,
):
    entries = session.exec(
        select(MedicalHistoryEntry).where(MedicalHistoryEntry.profile_id == profile.id)
    ).all()

    sections = []
    for key, spec in KINDS.items():
        mine = sorted((e for e in entries if e.kind == key), key=entry_sort_key)
        sections.append(
            {
                "key": key,
                "label": spec["label"],
                "intro": spec["intro"],
                "note": spec.get("note"),
                "empty": spec["empty"],
                "add_label": spec["add_label"],
                "fields": spec["fields"],
                "entries": [describe_entry(e, spec) for e in mine],
                "total": len(mine),
                "has_error": key == error_kind,
                "error": error if key == error_kind else None,
                "form_values": form_values if key == error_kind and form_values else {},
            }
        )

    return templates.TemplateResponse(
        request,
        "medical.html",
        {
            "user": user,
            "profile": profile,
            "sections": sections,
            "today": date.today().isoformat(),
            "flash": request.session.pop("flash", None),
        },
        status_code=status_code,
    )


def render_edit_medical_page(
    request: Request,
    user: User,
    profile: Profile,
    entry: MedicalHistoryEntry,
    form_values: Optional[dict] = None,
    error: Optional[str] = None,
    status_code: int = 200,
):
    spec = KINDS[entry.kind]
    if form_values is None:
        form_values = entry_form_values(entry)
    return templates.TemplateResponse(
        request,
        "edit_medical.html",
        {
            "user": user,
            "profile": profile,
            "entry": entry,
            "spec": spec,
            "form_values": form_values,
            "today": date.today().isoformat(),
            "error": error,
        },
        status_code=status_code,
    )


# ---------- Routes ----------


@router.get(
    "/p/{profile_id}/medical", response_class=HTMLResponse, include_in_schema=False
)
def medical_page(
    profile_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    return render_medical_page(request, session, user, profile)


@router.post("/p/{profile_id}/medical/{kind}/new", include_in_schema=False)
def add_entry_form(
    profile_id: int,
    kind: str,
    request: Request,
    title: str = Form(""),
    date_a: str = Form(""),
    date_b: str = Form(""),
    status: str = Form(""),
    detail1: str = Form(""),
    detail2: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="Page not found")

    raw = {
        "title": title,
        "date_a": date_a,
        "date_b": date_b,
        "status": status,
        "detail1": detail1,
        "detail2": detail2,
        "notes": notes,
    }
    clean, error = check_entry(kind, raw)
    if error:
        return render_medical_page(
            request,
            session,
            user,
            profile,
            error_kind=kind,
            error=error,
            form_values=raw,
            status_code=400,
        )

    entry = MedicalHistoryEntry(profile_id=profile_id, kind=kind, **clean)
    session.add(entry)
    session.commit()
    request.session["flash"] = "Saved."
    return RedirectResponse(url=f"/p/{profile_id}/medical#{kind}", status_code=303)


@router.get(
    "/p/{profile_id}/medical/entry/{entry_id}/edit",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def edit_entry_page(
    profile_id: int,
    entry_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    entry = get_owned_entry(entry_id, profile_id, session)
    return render_edit_medical_page(request, user, profile, entry)


@router.post("/p/{profile_id}/medical/entry/{entry_id}/edit", include_in_schema=False)
def edit_entry_form(
    profile_id: int,
    entry_id: int,
    request: Request,
    title: str = Form(""),
    date_a: str = Form(""),
    date_b: str = Form(""),
    status: str = Form(""),
    detail1: str = Form(""),
    detail2: str = Form(""),
    notes: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    entry = get_owned_entry(entry_id, profile_id, session)

    raw = {
        "title": title,
        "date_a": date_a,
        "date_b": date_b,
        "status": status,
        "detail1": detail1,
        "detail2": detail2,
        "notes": notes,
    }
    clean, error = check_entry(entry.kind, raw)
    if error:
        return render_edit_medical_page(
            request,
            user,
            profile,
            entry,
            form_values=raw,
            error=error,
            status_code=400,
        )

    entry.title = clean["title"]
    entry.date_a = clean["date_a"]
    entry.date_b = clean["date_b"]
    entry.status = clean["status"]
    entry.detail1 = clean["detail1"]
    entry.detail2 = clean["detail2"]
    entry.notes = clean["notes"]
    session.add(entry)
    session.commit()
    request.session["flash"] = "Changes saved."
    return RedirectResponse(
        url=f"/p/{profile_id}/medical#{entry.kind}", status_code=303
    )


@router.post(
    "/p/{profile_id}/medical/entry/{entry_id}/delete", include_in_schema=False
)
def delete_entry_form(
    profile_id: int,
    entry_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    entry = get_owned_entry(entry_id, profile_id, session)
    kind = entry.kind
    session.delete(entry)
    session.commit()
    request.session["flash"] = "Deleted."
    return RedirectResponse(url=f"/p/{profile_id}/medical#{kind}", status_code=303)