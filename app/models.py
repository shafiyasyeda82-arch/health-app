from datetime import date
from typing import Optional

from sqlmodel import Field, SQLModel


class Profile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    age: int
    gender: str
    blood_group: Optional[str] = None
    allergies: Optional[str] = None


class VaccineBase(SQLModel):
    vaccine_name: str
    date_taken: date
    next_due: Optional[date] = None
    notes: Optional[str] = None


class VaccineCreate(VaccineBase):
    pass


class VaccineRecord(VaccineBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id")