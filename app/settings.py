"""Settings page: appearance (themes, light/dark/system mode, custom colours).

The choices are saved per user in the appearance_setting table, so they follow the
person across sessions. load_appearance() runs on every page and tells the page
which CSS variables to use.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, event
from sqlmodel import Field, Session, SQLModel, select

from app.database import get_session
from app.models import User
from app.themes import (
    DEFAULT_MODE,
    DEFAULT_THEME,
    MODES,
    THEMES,
    resolve_appearance,
    theme_previews,
    valid_hex,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

MODE_CHOICES = [
    ("light", "Light", "Bright backgrounds"),
    ("dark", "Dark", "Dark backgrounds, easier on the eyes at night"),
    ("system", "System", "Follows your device's light or dark setting"),
]


class AppearanceSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)
    theme: str = DEFAULT_THEME
    mode: str = DEFAULT_MODE
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None


def get_row(session: Session, user_id: int) -> Optional[AppearanceSetting]:
    return session.exec(
        select(AppearanceSetting).where(AppearanceSetting.user_id == user_id)
    ).first()


def load_appearance(
    request: Request, session: Session = Depends(get_session)
) -> None:
    """Runs before every page. Pages read the result from request.state.appearance."""
    row = None
    user_id = request.session.get("user_id")
    if user_id is not None:
        row = get_row(session, user_id)
    if row is None:
        request.state.appearance = resolve_appearance()
    else:
        request.state.appearance = resolve_appearance(
            row.theme, row.mode, row.primary_color, row.accent_color
        )


def get_current_user_or_none(
    request: Request, session: Session = Depends(get_session)
) -> Optional[User]:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return session.get(User, user_id)


def render_settings(
    request: Request,
    session: Session,
    user: User,
    error: Optional[str] = None,
    status_code: int = 200,
):
    row = get_row(session, user.id)
    theme = row.theme if row and row.theme in THEMES else DEFAULT_THEME
    mode = row.mode if row and row.mode in MODES else DEFAULT_MODE
    custom_primary = valid_hex(row.primary_color) if row else None
    custom_accent = valid_hex(row.accent_color) if row else None
    use_custom = bool(custom_primary or custom_accent)

    base = THEMES[theme]["light"]
    current = {
        "theme": theme,
        "mode": mode,
        "use_custom": use_custom,
        "primary": custom_primary or base["primary"],
        "accent": custom_accent or base["accent"],
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "themes": theme_previews(),
            "modes": MODE_CHOICES,
            "current": current,
            "flash": request.session.pop("flash", None),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/settings", response_class=HTMLResponse, include_in_schema=False)
def settings_page(
    request: Request,
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return render_settings(request, session, user)


@router.post("/settings/appearance", include_in_schema=False)
def save_appearance(
    request: Request,
    action: str = Form("save"),
    theme: str = Form(DEFAULT_THEME),
    mode: str = Form(DEFAULT_MODE),
    use_custom: str = Form(""),
    primary_color: str = Form(""),
    accent_color: str = Form(""),
    user: Optional[User] = Depends(get_current_user_or_none),
    session: Session = Depends(get_session),
):
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    row = get_row(session, user.id) or AppearanceSetting(user_id=user.id)

    if action == "reset":
        row.theme = DEFAULT_THEME
        row.mode = DEFAULT_MODE
        row.primary_color = None
        row.accent_color = None
        message = "Appearance reset to Eevee Light."
    else:
        if theme not in THEMES or mode not in MODES:
            return render_settings(
                request, session, user, error="Please choose a theme and a mode.", status_code=400
            )
        primary = valid_hex(primary_color) if use_custom else None
        accent = valid_hex(accent_color) if use_custom else None
        if use_custom and (primary is None or accent is None):
            return render_settings(
                request, session, user, error="Please choose both colours.", status_code=400
            )
        row.theme = theme
        row.mode = mode
        row.primary_color = primary
        row.accent_color = accent
        message = "Appearance saved."

    session.add(row)
    session.commit()
    request.session["flash"] = message
    return RedirectResponse(url="/settings", status_code=303)


# Deleting an account also deletes its appearance settings.
@event.listens_for(User, "before_delete")
def remove_appearance_with_user(mapper, connection, target):
    table = AppearanceSetting.__table__
    connection.execute(delete(table).where(table.c.user_id == target.id))