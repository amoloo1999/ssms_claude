from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request
from starlette.responses import RedirectResponse, JSONResponse
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.sql import func
from app.config import (
    get_settings,
    is_revman,
    is_approver,
    is_guest,
    can_write_anywhere,
)
from app.database import get_db
from app.models import User, UserResponse

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/login")
async def login(request: Request):
    redirect_uri = str(request.base_url).rstrip("/") + "/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def auth_callback(request: Request, db: AsyncSession = Depends(get_db)):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")

    if not user_info:
        raise HTTPException(status_code=400, detail="Failed to get user info")

    email = user_info["email"]

    # Check domain restriction. Named guests (external collaborators) are the
    # one exception — see GUEST_EMAILS in config.
    if settings.allowed_domain and not is_guest(email):
        domain = email.split("@")[1]
        if domain != settings.allowed_domain:
            raise HTTPException(
                status_code=403,
                detail=f"Email domain {domain} is not allowed",
            )

    # Upsert user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        user.last_login = func.now()
        user.name = user_info.get("name", user.name)
        user.picture = user_info.get("picture", user.picture)
    else:
        user = User(
            email=email,
            name=user_info.get("name", ""),
            picture=user_info.get("picture", ""),
        )
        db.add(user)

    await db.commit()

    # Store user in session
    request.session["user"] = {
        "email": email,
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
    }

    return RedirectResponse(url=str(request.base_url))


def _decorate_user(user: dict) -> dict:
    email = user.get("email", "")
    return {
        **user,
        "role": "revman" if is_revman(email) else "user",
        "is_approver": is_approver(email),
        # Exempt from a connection's read_only policy, so the connection bar can
        # tell this user the truth about a read-only server instead of showing
        # everyone the same blanket "writes blocked".
        "can_write_anywhere": can_write_anywhere(email),
    }


@router.get("/me", response_model=UserResponse)
async def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _decorate_user(user)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return JSONResponse({"message": "Logged out"})


def require_auth(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _decorate_user(user)


def block_guests(request: Request):
    """Deny external guests. Used on the AI endpoints, which pull schema context
    (and, for SQL Server, enumerate table/column names across EVERY database on
    the server) regardless of the caller's table grants. That discovery surface
    is fine for employees but defeats the point of scoping a guest to a handful
    of tables."""
    user = require_auth(request)
    if is_guest(user.get("email", "")):
        raise HTTPException(
            status_code=403, detail="AI tools are not available to guest accounts."
        )
    return user


def require_revman(request: Request):
    user = require_auth(request)
    if user.get("role") != "revman":
        raise HTTPException(status_code=403, detail="RevMan role required")
    return user


def require_approver(request: Request):
    user = require_auth(request)
    if not user.get("is_approver"):
        raise HTTPException(status_code=403, detail="Approver role required")
    return user
