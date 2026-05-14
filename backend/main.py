"""AutoForm – FastAPI application entry point."""

import io
import os
import zipfile

# Allow OAuth over plain HTTP for local development.
# In production (Render) the redirect URI is HTTPS so this has no effect.
if os.environ.get("APP_URL", "http://localhost:8000").startswith("http://"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import settings
from .database import init_db
from .auth.routes import router as auth_router
from .google_auth.routes import router as google_router
from .forms.extension_routes import router as extension_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.APP_URL.startswith("http://localhost"):
        settings.validate_production()
    init_db()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="AutoForm",
    description="Automatically answer and submit Google Forms using an LLM council.",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — restrict to the configured APP_URL in production; allow localhost in dev
_origins = [settings.APP_URL]
if settings.APP_URL.startswith("http://localhost"):
    _origins.append("http://localhost:5173")  # Vite dev server

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if not settings.APP_URL.startswith("http://localhost"):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(google_router)
app.include_router(extension_router)

# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# ── Static files / frontend ───────────────────────────────────────────────────

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(FRONTEND_DIR, "static")),
    name="static",
)


@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/dashboard.html", include_in_schema=False)
def serve_dashboard():
    return FileResponse(os.path.join(FRONTEND_DIR, "dashboard.html"))


@app.get("/extension.zip", include_in_schema=False)
def download_extension():
    extension_dir = os.path.join(os.path.dirname(__file__), "..", "extension")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(extension_dir):
            for fname in files:
                full_path = os.path.join(root, fname)
                arcname = os.path.relpath(full_path, extension_dir)
                zf.write(full_path, arcname)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=autoform-extension.zip"},
    )
