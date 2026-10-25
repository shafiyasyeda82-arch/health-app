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


# ---------- Helpers: who is logged in? ----------


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


# ---------- Web pages ----------


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
        request, "index.html", {"user": user, "profiles": profiles}
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