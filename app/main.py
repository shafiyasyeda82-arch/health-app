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
    CycleEntry,
    Profile,
    ProfileCreate,
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

GENDER_OPTIONS = ["Female", "Male", "Other", "Prefer not to say"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    same_site="lax",
    https_only=False,  # change to True when the app is online with HTTPS
    max_age=60 * 60 * 24 * 7,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- Helpers ----------


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


def tracks_cycle(profile: Profile) -> bool:
    return profile.gender.strip().lower() == "female"


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
        "next_start": next_start,
        "days_until": (next_start - date.today()).days,
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
    status_code: int = 200,
):
    vaccines = session.exec(
        select(VaccineRecord)
        .where(VaccineRecord.profile_id == profile.id)
        .order_by(col(VaccineRecord.date_taken).desc())
    ).all()
    reminders = build_reminders(session, profile.id, days=90)

    show_cycle = tracks_cycle(profile)
    cycles = []
    cycle_info = None
    if show_cycle:
        cycles = session.exec(
            select(CycleEntry)
            .where(CycleEntry.profile_id == profile.id)
            .order_by(col(CycleEntry.start_date).desc())
        ).all()
        cycle_info = build_cycle_info(cycles)

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": user,
            "profile": profile,
            "vaccines": vaccines,
            "reminders": reminders,
            "show_cycle": show_cycle,
            "cycles": cycles,
            "cycle_info": cycle_info,
            "error": error,
            "cycle_error": cycle_error,
        },
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
        {"user": user, "profiles": profiles, "gender_options": GENDER_OPTIONS},
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


# ---------- Web pages: profiles, vaccines, cycle tracker ----------


@app.post("/profiles/new", include_in_schema=False)
def create_profile_form(
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    blood_group: str = Form(""),
    allergies: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if gender not in GENDER_OPTIONS:
        gender = "Prefer not to say"
    profile = Profile(
        name=name,
        age=age,
        gender=gender,
        blood_group=blood_group or None,
        allergies=allergies or None,
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
    return render_profile_page(request, session, user, profile)


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

    try:
        taken = date.fromisoformat(date_taken)
        due = date.fromisoformat(next_due) if next_due else None
    except ValueError:
        return render_profile_page(
            request,
            session,
            user,
            profile,
            error="Please enter valid dates.",
            status_code=400,
        )

    record = VaccineRecord(
        profile_id=profile_id,
        vaccine_name=vaccine_name.strip(),
        date_taken=taken,
        next_due=due,
        notes=notes.strip() or None,
    )
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

    record = session.get(VaccineRecord, vaccine_id)
    if record is None or record.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Vaccine record not found")
    session.delete(record)
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
    if not tracks_cycle(profile):
        raise HTTPException(status_code=404, detail="Not available for this profile")

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


@app.post("/p/{profile_id}/cycles/{cycle_id}/delete", include_in_schema=False)
def delete_cycle_form(
    profile_id: int,
    cycle_id: int,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    get_owned_profile(profile_id, user, session)

    entry = session.get(CycleEntry, cycle_id)
    if entry is None or entry.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    session.delete(entry)
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

    vaccines = session.exec(
        select(VaccineRecord).where(VaccineRecord.profile_id == profile_id)
    ).all()
    for record in vaccines:
        session.delete(record)

    cycles = session.exec(
        select(CycleEntry).where(CycleEntry.profile_id == profile_id)
    ).all()
    for entry in cycles:
        session.delete(entry)

    session.delete(profile)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- API routes (login required, your own data only) ----------


@app.post("/profiles", response_model=Profile)
def create_profile(
    data: ProfileCreate,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    profile = Profile(**data.model_dump(), user_id=user.id)
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