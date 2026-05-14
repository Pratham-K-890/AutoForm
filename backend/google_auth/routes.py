"""Google OAuth2 integration.

Flow:
  1. GET /api/google/connect  → returns the Google consent URL
  2. User visits that URL in browser and authorises the app
  3. Google redirects to GET /google/callback?code=...&state=...
     (note: no /api prefix so it matches the exact redirect URI registered in
      Google Cloud Console)
  4. We exchange the code for tokens, encrypt them, and store in DB
  5. User is redirected back to the dashboard
"""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GRequest
from sqlalchemy.orm import Session
import httpx

from ..config import settings
from ..database import get_db
from ..models import GoogleToken, User
from ..auth.utils import get_current_user

router = APIRouter(tags=["google"])

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# state → (user_id, created_timestamp)
# TTL of 10 minutes; entries older than that are pruned on next connect request.
_oauth_states: dict[str, tuple[int, float]] = {}
_STATE_TTL = 600  # seconds


def _prune_states() -> None:
    """Remove OAuth states older than TTL to prevent unbounded memory growth."""
    cutoff = time.monotonic() - _STATE_TTL
    stale = [k for k, (_, ts) in _oauth_states.items() if ts < cutoff]
    for k in stale:
        del _oauth_states[k]


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_cipher() -> Fernet:
    key = settings.ENCRYPTION_KEY
    if not key:
        # Derive a consistent key from JWT_SECRET so dev deployments work without setup
        import base64, hashlib
        raw = hashlib.sha256(settings.JWT_SECRET.encode()).digest()
        key = base64.urlsafe_b64encode(raw).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(value: str) -> str:
    return _get_cipher().encrypt(value.encode()).decode()


def decrypt_token(value: str) -> str:
    try:
        return _get_cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise HTTPException(status_code=500, detail="Token decryption failed")


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _build_auth_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)


def _exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange auth code + PKCE verifier for tokens."""
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_credentials(google_token: GoogleToken, db: Session = None) -> Credentials:
    """Reconstruct google.oauth2.credentials.Credentials from a DB row.
    If expired, refreshes the token and persists the new access token back to DB."""
    access_token = decrypt_token(google_token.access_token_enc)
    refresh_token = (
        decrypt_token(google_token.refresh_token_enc)
        if google_token.refresh_token_enc else None
    )
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=json.loads(google_token.scopes or "[]"),
    )
    if google_token.token_expiry:
        creds.expiry = google_token.token_expiry

    if creds.expired and creds.refresh_token:
        creds.refresh(GRequest())
        # Persist the new access token so the next call doesn't refresh again
        google_token.access_token_enc = encrypt_token(creds.token)
        google_token.token_expiry = creds.expiry
        if db:
            db.commit()

    return creds


# ── endpoints ────────────────────────────────────────────────────────────────

@router.get("/api/google/connect")
def google_connect(current_user: User = Depends(get_current_user)):
    """Return the Google OAuth consent URL. Frontend should redirect the user there."""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")
    _prune_states()
    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _pkce_pair()
    _oauth_states[state] = (current_user.id, time.monotonic(), code_verifier)
    auth_url = _build_auth_url(state, code_challenge)
    return {"auth_url": auth_url}


@router.get("/google/callback")
def google_callback(code: str, state: str, db: Session = Depends(get_db)):
    """OAuth callback — exchanges the code for tokens and saves them."""
    entry = _oauth_states.pop(state, None)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    user_id, ts, code_verifier = entry
    if time.monotonic() - ts > _STATE_TTL:
        raise HTTPException(status_code=400, detail="OAuth session expired – please try again")

    token_data = _exchange_code(code, code_verifier)
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")

    # Fetch the user's Google email
    google_email = None
    try:
        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            google_email = resp.json().get("email")
    except Exception:
        pass

    # Upsert the token row
    existing = db.query(GoogleToken).filter(GoogleToken.user_id == user_id).first()
    if existing:
        existing.access_token_enc = encrypt_token(access_token)
        if refresh_token:
            existing.refresh_token_enc = encrypt_token(refresh_token)
        existing.token_expiry = None
        existing.google_email = google_email
        existing.scopes = json.dumps(SCOPES)
    else:
        row = GoogleToken(
            user_id=user_id,
            access_token_enc=encrypt_token(access_token),
            refresh_token_enc=encrypt_token(refresh_token) if refresh_token else None,
            token_expiry=None,
            google_email=google_email,
            scopes=json.dumps(SCOPES),
        )
        db.add(row)

    db.commit()
    return RedirectResponse(url="/dashboard.html?google=connected")


@router.get("/api/google/status")
def google_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token = db.query(GoogleToken).filter(GoogleToken.user_id == current_user.id).first()
    if not token:
        return {"connected": False, "email": None}
    return {"connected": True, "email": token.google_email}


@router.post("/api/google/setup-session")
async def google_setup_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Open a headed Playwright browser for one-time Google sign-in.
    Blocks until the user completes sign-in (up to 3 min) or times out."""
    from ..forms.autofill import setup_google_browser_session

    token = db.query(GoogleToken).filter(GoogleToken.user_id == current_user.id).first()
    google_email = token.google_email if token else None

    result = await setup_google_browser_session(current_user.id, google_email)
    if not result["success"]:
        raise HTTPException(status_code=408, detail=result["message"])
    return result


@router.delete("/api/google/disconnect")
def google_disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(GoogleToken).filter(GoogleToken.user_id == current_user.id).delete()
    db.commit()
    return {"success": True}
