# AutoForm

AI-powered Google Forms filler. Open a form, click **Analyze**, review the answers, click **Fill** — done in seconds.

**Live demo:** https://autoform-7lmx.onrender.com

## How it works

A Chrome extension scrapes the visible questions from the form DOM and sends them to the FastAPI backend. A council of three LLMs (LLaMA 3.3 70B, Mixtral 8×7B, Gemini 2.0 Flash) votes on answers by majority. Questions detected as personal info (name, email, phone, etc.) are surfaced for you to fill in manually. The extension then injects the answers directly into the form using React's internal event system.

```
Chrome extension
  └─ content.js     scrapes questions from DOM
  └─ popup.js       calls POST /api/extension/analyze
                          └─ personal_info detector
                          └─ LLM council (3-model vote)
  └─ content.js     fills answers into the form
```

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + Uvicorn |
| Database | SQLite via SQLAlchemy |
| Auth | JWT (email/password) + Google OAuth2 |
| LLMs | Groq (LLaMA 3.3 70B, Mixtral 8×7B) + Gemini 2.0 Flash |
| Extension | Chrome MV3 (content script + popup) |
| Deploy | Docker + Render |

## LLM council logic

| Question type | Rule |
|---|---|
| MCQ / Dropdown | Majority vote; LLaMA tiebreak if all three disagree |
| Checkbox | Include option if ≥ 2/3 models chose it |
| Text / Paragraph | Longest, most detailed answer wins |

If any model hits a rate limit it is skipped; the remaining models still vote.

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd Auto_Form
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
playwright install chromium
```

### 2. Environment variables

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `JWT_SECRET` | Long random string for signing JWTs |
| `ENCRYPTION_KEY` | Fernet key for encrypting Google tokens — generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `GOOGLE_CLIENT_ID` | From Google Cloud Console → OAuth 2.0 credentials |
| `GOOGLE_CLIENT_SECRET` | Same |
| `GOOGLE_REDIRECT_URI` | Must match exactly what's registered, e.g. `http://localhost:8000/google/callback` |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) |
| `APP_URL` | Base URL of the backend, e.g. `http://localhost:8000` |

### 3. Google Cloud Console setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → Enable **People API**
3. OAuth consent screen → External → add your email as a test user
4. Credentials → Create → OAuth 2.0 Client ID → Web Application
5. Authorised redirect URI: `http://localhost:8000/google/callback`
6. Copy Client ID + Secret into `.env`

### 4. Run the backend

```bash
uvicorn backend.main:app --reload
```

The web UI is available at `http://localhost:8000`.

### 5. Install the Chrome extension

1. Open Chrome and go to `chrome://extensions`
2. Enable **Developer mode** (top right)
3. Click **Load unpacked** and select the `extension/` folder
4. Open any Google Form — the AutoForm icon will appear in your toolbar

## Usage

1. Register / log in at `http://localhost:8000`
2. Open a Google Form in Chrome
3. Click the AutoForm extension icon
4. Click **Analyze** — the extension reads the questions and gets AI answers
5. Fill in any personal info fields that appear
6. Click **Fill** — answers are injected into the form

## Deployment (Render)

The project includes a `Dockerfile` and `render.yaml` for one-click deployment on [Render](https://render.com).

1. Push this repo to GitHub
2. Create a new **Web Service** on Render and connect the repo (or use the **Blueprint** option with `render.yaml`)
3. Set all environment variables in the Render dashboard
4. Update `GOOGLE_REDIRECT_URI` and `APP_URL` to your Render service URL
5. Update `host_permissions` in `extension/manifest.json` to include your Render URL, then reload the extension

## Project structure

```
backend/
  main.py              FastAPI app entry point
  models.py            SQLAlchemy models
  config.py            Settings loaded from .env
  database.py          SQLAlchemy session + init_db
  queue_manager.py     Per-user asyncio semaphore + event
  auth/                JWT register / login / me
  google_auth/         Google OAuth2 flow + Fernet token encryption
  forms/
    extension_routes.py  POST /api/extension/analyze (used by extension)
    personal_info.py     Keyword-based personal question detector
    routes.py            Full pipeline API (web dashboard)
    scraper.py           Playwright question extractor
    autofill.py          Playwright stealth form filler
    http_submit.py       Direct HTTP form submitter (no browser)
    validator.py         Pre-flight form checks
  llm/
    council.py           3-model async voting council

extension/
  manifest.json        Chrome MV3 manifest
  content.js           DOM scraper + answer injector (runs in the form page)
  popup.js/html/css    Extension popup UI
  background.js        Service worker (minimal)

frontend/
  index.html           Login / register page
  dashboard.html       Web dashboard
  static/              CSS + JS
```
