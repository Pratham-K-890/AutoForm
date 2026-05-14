"""AutoForm – FastAPI application entry point."""

import os

# Allow OAuth over plain HTTP for local development.
# In production (Railway/Render) the redirect URI is HTTPS so this has no effect.
if os.environ.get("APP_URL", "http://localhost:8000").startswith("http://"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import init_db
from .auth.routes import router as auth_router
from .google_auth.routes import router as google_router
from .forms.routes import router as forms_router
from .forms.extension_routes import router as extension_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AutoForm",
    description="Automatically answer and submit Google Forms using an LLM council.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(google_router)
app.include_router(forms_router)
app.include_router(extension_router)

# ── Static files / frontend ───────────────────────────────────────────────────

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

# Mount the static assets (CSS, JS)
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
