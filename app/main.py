from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import List

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, col, select

from app.database import create_db_and_tables, get_session
from app.models import Profile, VaccineCreate, VaccineRecord, VaccineReminder


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- Web pages ----------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home_page(request: Request, session: Session = Depends(get_session)):
    profiles = session.exec(select(Profile)).all()
    return templates.TemplateResponse(request, "index.html", {"profiles": profiles})


@app.post("/profiles/new", include_in_schema=False)
def create_profile_form(
    name: str = Form(...),
    age: int = Form(...),
    gender: str = Form(...),
    blood_group: str = Form(""),
    allergies: str = Form(""),
    session: Session = Depends(get_session),
):
    profile = Profile(
        name=name,
        age=age,
        gender=gender,
        blood_group=blood_group or None,
        allergies=allergies or None,
    )
    session.add(profile)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


# ---------- API routes ----------


@app.post("/profiles", response_model=Profile)
def create_profile(profile: Profile, session: Session = Depends(get_session)):
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


@app.get("/profiles", response_model=List[Profile])
def list_profiles(session: Session = Depends(get_session)):
    return session.exec(select(Profile)).all()


@app.post("/profiles/{profile_id}/vaccines", response_model=VaccineRecord)
def add_vaccine(
    profile_id: int,
    vaccine: VaccineCreate,
    session: Session = Depends(get_session),
):
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    record = VaccineRecord(**vaccine.model_dump(), profile_id=profile_id)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


@app.get("/profiles/{profile_id}/vaccines", response_model=List[VaccineRecord])
def list_vaccines(profile_id: int, session: Session = Depends(get_session)):
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    statement = select(VaccineRecord).where(VaccineRecord.profile_id == profile_id)
    return session.exec(statement).all()


@app.get("/profiles/{profile_id}/reminders", response_model=List[VaccineReminder])
def get_reminders(
    profile_id: int,
    days: int = 30,
    session: Session = Depends(get_session),
):
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

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