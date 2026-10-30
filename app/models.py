from datetime import date
from typing import Optional

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


def calculate_age(born: date, today: Optional[date] = None) -> int:
    """Age in completed years (the international way: it goes up on the birthday)."""
    today = today or date.today()
    years = today.year - born.year
    if (today.month, today.day) < (born.month, born.day):
        years -= 1
    return max(years, 0)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str


class ProfileBase(SQLModel):
    name: str
    date_of_birth: date
    gender: str
    blood_group: Optional[str] = None
    allergies: Optional[str] = None
    height_cm: Optional[float] = None


class ProfileCreate(ProfileBase):
    pass


class Profile(ProfileBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    @property
    def age(self) -> int:
        """Worked out from the date of birth every time, so it never goes out of date."""
        return calculate_age(self.date_of_birth)


class CycleEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    start_date: date
    end_date: Optional[date] = None
    notes: Optional[str] = None


class CycleSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", unique=True, index=True)
    tracking_active: bool = True


class SymptomLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    log_date: date
    symptoms: str = ""
    notes: Optional[str] = None


class ContextNote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    note_date: date
    tags: str = ""
    note: Optional[str] = None


class HealthReading(SQLModel, table=True):
    """One recorded measurement (for example a blood pressure or glucose reading).

    measured_at is plain local time exactly as the person entered it (no time zone).
    value1 is the main number (glucose value, or systolic for blood pressure).
    value2 is only used when a measurement has two numbers (diastolic).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    reading_type: str = Field(index=True)
    measured_at: NaiveDatetime
    value1: float
    value2: Optional[float] = None
    unit: str
    context: Optional[str] = None
    notes: Optional[str] = None


class MedicalHistoryEntry(SQLModel, table=True):
    """One record in a person's medical history.

    The same table holds every section (conditions, medications, allergies, surgeries
    and family history). `kind` says which section an entry belongs to, and what each
    column means for that kind is described in app/medical.py.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    kind: str = Field(index=True)
    title: str
    date_a: Optional[date] = None
    date_b: Optional[date] = None
    status: Optional[str] = None
    detail1: Optional[str] = None
    detail2: Optional[str] = None
    notes: Optional[str] = None


class ShareLink(SQLModel, table=True):
    """A private link a patient creates so a doctor can see chosen sections.

    The link token and the access code are never stored, only hashes of them, so they
    can be shown to the patient once and never recovered. Times are UTC.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", index=True)
    token_hash: str = Field(unique=True, index=True)
    code_hash: str
    label: Optional[str] = None
    sections: str = ""
    created_at: NaiveDatetime
    expires_at: NaiveDatetime
    revoked_at: Optional[NaiveDatetime] = None
    failed_attempts: int = 0
    locked: bool = False
    view_count: int = 0
    last_viewed_at: Optional[NaiveDatetime] = None


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