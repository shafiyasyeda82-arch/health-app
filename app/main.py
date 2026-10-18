from contextlib import asynccontextmanager
from typing import List

from fastapi import Depends, FastAPI, HTTPException
from sqlmodel import Session, select

from app.database import create_db_and_tables, get_session
from app.models import Profile, VaccineCreate, VaccineRecord


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
def home():
    return {"message": "Health app is running"}


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