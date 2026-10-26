from datetime import date
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str


class ProfileBase(SQLModel):
    name: str
    age: int
    gender: str
    blood_group: Optional[str] = None
    allergies: Optional[str] = None


class ProfileCreate(ProfileBase):
    pass


class Profile(ProfileBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)


class CycleEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    start_date: date
    end_date: Optional[date] = None
    notes: Optional[str] = None


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


class VaccineReminder(SQLModel):
    vaccine_name: str
    next_due: date
    days_left: int
    status: str