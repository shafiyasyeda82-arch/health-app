"""Share with a doctor.

The patient chooses which sections to share and for how long. The app makes a private
link plus a 6-digit access code. A doctor opens the link, types the code and sees a
read-only summary, with no account needed. The patient can turn the link off at any
time and can see when it was opened.

Safety design:
- The link token and the code are only shown once. The database keeps only hashes.
- 5 wrong codes lock the link.
- Doctor pages are marked "no-store" so browsers do not keep copies.
- Only the sections the patient ticked are ever loaded.
- Deleting a profile also deletes its share links (see the event at the bottom).
"""

import hashlib
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, event
from sqlmodel import Session, col, select

from app.database import get_session
from app.medical import KINDS, describe_entry, entry_sort_key
from app.models import (
    CycleEntry,
    MedicalHistoryEntry,
    Profile,
    ShareLink,
    SymptomLog,
    User,
    VaccineRecord,
)
from app.readings import READING_TYPES, describe_reading, format_number, load_rows
from app.security import hash_password, verify_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")

LABEL_MAX = 80
MAX_ATTEMPTS = 5  # wrong codes allowed before the link locks
MAX_ACTIVE = 10  # active links allowed per profile
UNLOCK_SECONDS = 60 * 60  # a doctor who typed the right code can reload for an hour

# What the patient can choose to share. "kind" links a section to medical history.
SECTIONS = [
    {
        "key": "basics",
        "label": "Basic details",
        "hint": "Name, age, gender, blood group and height.",
        "default": True,
    },
    {
        "key": "allergies",
        "label": "Allergies",
        "hint": "Allergies and reactions, including the allergies box on the profile.",
        "default": True,
        "kind": "allergy",
    },
    {
        "key": "conditions",
        "label": "Conditions",
        "hint": "Diagnosed conditions and their status.",
        "default": True,
        "kind": "condition",
    },
    {
        "key": "medications",
        "label": "Medications",
        "hint": "Medicines taken now or in the past.",
        "default": True,
        "kind": "medication",
    },
    {
        "key": "surgeries",
        "label": "Surgeries and hospital stays",
        "hint": "Operations, hospital stays and other procedures.",
        "default": False,
        "kind": "surgery",
    },
    {
        "key": "family",
        "label": "Family medical history",
        "hint": "Conditions in close relatives.",
        "default": False,
        "kind": "family",
    },
    {
        "key": "readings",
        "label": "Health readings",
        "hint": "The latest 5 readings of each kind (blood pressure, glucose, weight and so on).",
        "default": False,
    },
    {
        "key": "vaccines",
        "label": "Vaccines",
        "hint": "Vaccine records and dates.",
        "default": False,
    },
    {
        "key": "cycle",
        "label": "Cycle tracker summary",
        "hint": "Recent period dates, average cycle length and symptoms from the last 90 days. Private, so it is off unless you tick it.",
        "default": False,
        "female_only": True,
    },
]
SECTION_LABELS = {s["key"]: s["label"] for s in SECTIONS}
SECTION_KEYS = [s["key"] for s in SECTIONS]
KIND_FOR = {s["key"]: s["kind"] for s in SECTIONS if "kind" in s}
MEDICAL_ORDER = ["allergies", "conditions", "medications", "surgeries", "family"]

EXPIRY_CHOICES = [
    ("24h", "24 hours", timedelta(hours=24)),
    ("3d", "3 days", timedelta(days=3)),
    ("7d", "7 days", timedelta(days=7)),
]
STATUS_TEXT = {
    "active": "Active",
    "expired": "Expired",
    "revoked": "Turned off",
    "locked": "Locked (too many wrong codes)",
}


# ---------- Small helpers ----------


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local_text(moment: Optional[datetime]) -> str:
    """Times are stored in UTC; show them in the computer's own time zone."""
    if moment is None:
        return ""
    return moment.replace(tzinfo=timezone.utc).astimezone().strftime("%d %b %Y, %H:%M")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def link_status(link: ShareLink, now: datetime) -> str:
    if link.revoked_at is not None:
        return "revoked"
    if link.locked:
        return "locked"
    if link.expires_at <= now:
        return "expired"
    return "active"


def is_female(profile: Profile) -> bool:
    return profile.gender.strip().lower() == "female"


def allowed_sections(profile: Profile) -> List[dict]:
    return [
        s for s in SECTIONS if not s.get("female_only") or is_female(profile)
    ]


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


def get_owned_link(link_id: int, profile_id: int, session: Session) -> ShareLink:
    link = session.get(ShareLink, link_id)
    if link is None or link.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Link not found")
    return link


def find_link(session: Session, token: str) -> Optional[ShareLink]:
    token = (token or "").strip()
    if not token or len(token) > 100:
        return None
    return session.exec(
        select(ShareLink).where(ShareLink.token_hash == hash_token(token))
    ).first()


def private(response):
    """Headers for pages that show health data to a doctor."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def unlock_key(link: ShareLink) -> str:
    # Keyed by the link's secret hash, not its id, so a reused id can never unlock.
    return link.token_hash[:24]


def is_unlocked(request: Request, link: ShareLink) -> bool:
    unlocked = request.session.get("share_unlocked")
    if not isinstance(unlocked, dict):
        return False
    stamp = unlocked.get(unlock_key(link))
    return isinstance(stamp, (int, float)) and time.time() - stamp < UNLOCK_SECONDS


def describe_link(link: ShareLink, now: datetime) -> dict:
    status = link_status(link, now)
    return {
        "id": link.id,
        "label": link.label or "Link without a name",
        "status": status,
        "status_text": STATUS_TEXT[status],
        "active": status == "active",
        "created": to_local_text(link.created_at),
        "expires": to_local_text(link.expires_at),
        "sections": [
            SECTION_LABELS[k] for k in link.sections.split(",") if k in SECTION_LABELS
        ],
        "views": link.view_count,
        "last_viewed": to_local_text(link.last_viewed_at),
        "attempts_left": max(MAX_ATTEMPTS - link.failed_attempts, 0),
    }


# ---------- What the doctor sees ----------


def build_summary(session: Session, profile: Profile, link: ShareLink) -> dict:
    """Load only the sections the patient ticked."""
    keys = [k for k in link.sections.split(",") if k in SECTION_KEYS]
    summary = {
        "basics": None,
        "medical": [],
        "readings": None,
        "vaccines": None,
        "cycle": None,
    }

    if "basics" in keys:
        summary["basics"] = {
            "name": profile.name,
            "age": profile.age,
            "gender": profile.gender,
            "blood_group": profile.blood_group or "",
            "height": format_number(profile.height_cm) if profile.height_cm else "",
        }

    wanted = [(key, KIND_FOR[key]) for key in MEDICAL_ORDER if key in keys]
    if wanted:
        entries = session.exec(
            select(MedicalHistoryEntry).where(
                MedicalHistoryEntry.profile_id == profile.id
            )
        ).all()
        for key, kind in wanted:
            mine = sorted((e for e in entries if e.kind == kind), key=entry_sort_key)
            block = {
                "label": SECTION_LABELS[key],
                "entries": [describe_entry(e, KINDS[kind]) for e in mine],
                "profile_note": None,
            }
            if key == "allergies" and profile.allergies:
                block["profile_note"] = profile.allergies
            summary["medical"].append(block)

    if "readings" in keys:
        groups = []
        for reading_type, config in READING_TYPES.items():
            rows = load_rows(session, profile.id, reading_type)[:5]
            if rows:
                groups.append(
                    {
                        "label": config["label"],
                        "has_bmi": bool(config.get("bmi")) and bool(profile.height_cm),
                        "rows": [
                            describe_reading(r, config, profile.height_cm)
                            for r in rows
                        ],
                    }
                )
        summary["readings"] = groups

    if "vaccines" in keys:
        records = session.exec(
            select(VaccineRecord)
            .where(VaccineRecord.profile_id == profile.id)
            .order_by(col(VaccineRecord.date_taken).desc())
        ).all()
        summary["vaccines"] = [
            {
                "name": r.vaccine_name,
                "taken": r.date_taken.strftime("%d %b %Y"),
                "next_due": r.next_due.strftime("%d %b %Y") if r.next_due else "",
                "notes": r.notes or "",
            }
            for r in records
        ]

    if "cycle" in keys and is_female(profile):
        entries = session.exec(
            select(CycleEntry)
            .where(CycleEntry.profile_id == profile.id)
            .order_by(col(CycleEntry.start_date).desc())
        ).all()
        starts = sorted(e.start_date for e in entries)
        gaps = [(later - earlier).days for earlier, later in zip(starts, starts[1:])][-6:]
        average = round(sum(gaps) / len(gaps)) if gaps else None

        cutoff = date.today() - timedelta(days=90)
        logs = session.exec(
            select(SymptomLog).where(
                SymptomLog.profile_id == profile.id, SymptomLog.log_date >= cutoff
            )
        ).all()
        symptoms: List[str] = []
        for log in logs:
            for symptom in log.symptoms.split(","):
                if symptom and symptom not in symptoms:
                    symptoms.append(symptom)

        recent = []
        for entry in entries[:6]:
            text = entry.start_date.strftime("%d %b %Y")
            if entry.end_date:
                text += f" to {entry.end_date.strftime('%d %b %Y')}"
            recent.append(text)
        summary["cycle"] = {
            "recent": recent,
            "average": average,
            "cycles_used": len(gaps),
            "symptoms": symptoms,
        }

    return summary


def gate_page(
    request: Request,
    mode: str,
    token: str = "",
    error: Optional[str] = None,
    status_code: int = 200,
):
    return private(
        templates.TemplateResponse(
            request,
            "share_gate.html",
            {"mode": mode, "token": token, "error": error},
            status_code=status_code,
        )
    )


def summary_page(
    request: Request, session: Session, profile: Profile, link: ShareLink
):
    return private(
        templates.TemplateResponse(
            request,
            "share_view.html",
            {
                "summary": build_summary(session, profile, link),
                "generated": to_local_text(utc_now()),
                "expires": to_local_text(link.expires_at),
            },
        )
    )


# ---------- Doctor side (no account; link plus code) ----------


@router.get("/share/{token}", response_class=HTMLResponse, include_in_schema=False)
def share_entry(
    token: str, request: Request, session: Session = Depends(get_session)
):
    link = find_link(session, token)
    if link is None or link_status(link, utc_now()) != "active":
        return gate_page(request, "unavailable", status_code=404)
    if is_unlocked(request, link):
        profile = session.get(Profile, link.profile_id)
        if profile is None:
            return gate_page(request, "unavailable", status_code=404)
        return summary_page(request, session, profile, link)
    return gate_page(request, "code", token=token)


@router.post("/share/{token}", include_in_schema=False)
def share_unlock(
    token: str,
    request: Request,
    code: str = Form(""),
    session: Session = Depends(get_session),
):
    now = utc_now()
    link = find_link(session, token)
    if link is None or link_status(link, now) != "active":
        return gate_page(request, "unavailable", status_code=404)

    cleaned = code.strip().replace(" ", "")
    if not (len(cleaned) == 6 and cleaned.isdigit()):
        return gate_page(
            request,
            "code",
            token=token,
            error="Please enter the 6-digit code.",
            status_code=400,
        )

    if not verify_password(cleaned, link.code_hash):
        link.failed_attempts += 1
        if link.failed_attempts >= MAX_ATTEMPTS:
            link.locked = True
        session.add(link)
        session.commit()
        if link.locked:
            return gate_page(request, "unavailable", status_code=403)
        left = MAX_ATTEMPTS - link.failed_attempts
        return gate_page(
            request,
            "code",
            token=token,
            error=f"That code is not right. {left} attempt{'s' if left != 1 else ''} left.",
            status_code=400,
        )

    link.failed_attempts = 0
    link.view_count += 1
    link.last_viewed_at = now
    session.add(link)
    session.commit()

    unlocked = dict(request.session.get("share_unlocked") or {})
    cutoff = time.time() - UNLOCK_SECONDS
    unlocked = {k: v for k, v in unlocked.items() if isinstance(v, (int, float)) and v > cutoff}
    unlocked[unlock_key(link)] = time.time()
    request.session["share_unlocked"] = unlocked
    return RedirectResponse(url=f"/share/{token}", status_code=303)


# ---------- Patient side (login required, your own profiles only) ----------


def render_share_page(
    request: Request,
    session: Session,
    user: User,
    profile: Profile,
    error: Optional[str] = None,
    values: Optional[dict] = None,
    status_code: int = 200,
):
    now = utc_now()
    links = session.exec(
        select(ShareLink)
        .where(ShareLink.profile_id == profile.id)
        .order_by(col(ShareLink.created_at).desc())
        .limit(20)
    ).all()

    allowed = allowed_sections(profile)
    if values is None:
        values = {
            "label": "",
            "expires": "24h",
            "sections": [s["key"] for s in allowed if s["default"]],
        }

    return templates.TemplateResponse(
        request,
        "share.html",
        {
            "user": user,
            "profile": profile,
            "sections": allowed,
            "expiry_choices": [(key, label) for key, label, _ in EXPIRY_CHOICES],
            "values": values,
            "links": [describe_link(link, now) for link in links],
            "created": request.session.pop("share_created", None),
            "max_attempts": MAX_ATTEMPTS,
            "error": error,
        },
        status_code=status_code,
    )


@router.get(
    "/p/{profile_id}/share", response_class=HTMLResponse, include_in_schema=False
)
def share_page(
    profile_id: int,
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)
    return render_share_page(request, session, user, profile)


@router.post("/p/{profile_id}/share/new", include_in_schema=False)
def create_share(
    profile_id: int,
    request: Request,
    label: str = Form(""),
    expires: str = Form("24h"),
    sections: List[str] = Form(default=[]),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    profile = get_owned_profile(profile_id, user, session)

    allowed_keys = [s["key"] for s in allowed_sections(profile)]
    chosen = [key for key in SECTION_KEYS if key in sections and key in allowed_keys]
    clean_label = " ".join(label.split())[:LABEL_MAX]
    submitted = {"label": clean_label, "expires": expires, "sections": chosen}

    def show_error(message: str):
        return render_share_page(
            request,
            session,
            user,
            profile,
            error=message,
            values=submitted,
            status_code=400,
        )

    if not chosen:
        return show_error("Please tick at least one section to share.")
    expiry = next((delta for key, _, delta in EXPIRY_CHOICES if key == expires), None)
    if expiry is None:
        return show_error("Please choose how long the link should work.")

    now = utc_now()
    existing = session.exec(
        select(ShareLink).where(ShareLink.profile_id == profile.id)
    ).all()
    if sum(1 for link in existing if link_status(link, now) == "active") >= MAX_ACTIVE:
        return show_error(
            f"You already have {MAX_ACTIVE} active links. Turn some off first."
        )

    token = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(1_000_000):06d}"
    link = ShareLink(
        profile_id=profile.id,
        token_hash=hash_token(token),
        code_hash=hash_password(code),
        label=clean_label or None,
        sections=",".join(chosen),
        created_at=now,
        expires_at=now + expiry,
    )
    session.add(link)
    session.commit()

    base = str(request.base_url).rstrip("/")
    request.session["share_created"] = {
        "url": f"{base}/share/{token}",
        "code": code,
        "label": clean_label,
    }
    return RedirectResponse(url=f"/p/{profile_id}/share", status_code=303)


@router.post("/p/{profile_id}/share/{link_id}/revoke", include_in_schema=False)
def revoke_share(
    profile_id: int,
    link_id: int,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    link = get_owned_link(link_id, profile_id, session)
    if link.revoked_at is None:
        link.revoked_at = utc_now()
        session.add(link)
        session.commit()
    return RedirectResponse(url=f"/p/{profile_id}/share", status_code=303)


@router.post("/p/{profile_id}/share/{link_id}/delete", include_in_schema=False)
def delete_share(
    profile_id: int,
    link_id: int,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)
    link = get_owned_link(link_id, profile_id, session)
    session.delete(link)
    session.commit()
    return RedirectResponse(url=f"/p/{profile_id}/share", status_code=303)


# ---------- Clean-up: deleting a profile also deletes its share links ----------


@event.listens_for(Profile, "before_delete")
def remove_share_links_with_profile(mapper, connection, target):
    table = ShareLink.__table__
    connection.execute(delete(table).where(table.c.profile_id == target.id))