# AI-Empowered Career Development and Interview Coaching Platform

A decoupled frontend/backend career-growth platform for universities and for students in artificial intelligence and related majors. It covers job-market insight, capability evidence, growth tasks, person–job matching, AI resumes, mock interviews and teaching analytics.

> This is the English edition of [README.md](README.md). Where the two disagree, the Chinese original is authoritative.

**What this repository runs today is a usable MVP, not just a design.** After signing up / signing in there are 7 modules (Workspace, Resume Workspace, Job Library, Organization Management, Mock Interview, Skills Assessment, Learning Dashboard), and the resume main line — "fill in → generate A4 → preview and edit → save" — is closed. The sentence above describes the coverage of the product vision; the current status of each item (delivered / implemented but not wired / design only) is recorded in [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md), scope decisions live in `docs/adr/`, and the order of work lives in [docs/ROADMAP.md](docs/ROADMAP.md).

## Documentation Index

Ordered by "who has the final say", not alphabetically:

| Document | What it governs |
|---|---|
| [AGENTS.md](AGENTS.md) | Working protocol and hard constraints; takes precedence over every other document in the repository |
| [docs/MVP_SCOPE.md](docs/MVP_SCOPE.md) | Phase 1 scope baseline (superseded from Phase 2 onward by ADRs and ROADMAP batches — the file says so itself) |
| [docs/adr/](docs/adr/) | 32 ADRs (ADR-001 … ADR-032): **the single source of truth for scope wording and decisions** |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Batch numbering (frontend track F1a–F7, backend track B1–B11) and the only source for the order of work |
| [docs/FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md) | The criteria for anything user-visible: copy C / visual V / motion M / layout L / forms F / states S / tokens T / testability R, plus the six gates G |
| [Product Requirements Document](docs/PRD.md) | Users, scope, features, differentiators, metrics and acceptance criteria |
| [Development and Architecture Document](docs/DEVELOPMENT.md) | Stack, directory layout, AI, security, testing and deployment |
| [API Document](docs/API.md) | REST endpoints, data formats, error codes and permissions. **SSE and WebRTC are design targets — there is not a single line of code for them** |
| [Database Design](docs/DATABASE.md) | MySQL logical model and constraint principles; **the truth about table structure lives in `alembic/versions/`**, and `docs/design/schema-draft.sql` is not a migration source |
| [Testing Document](docs/TESTING.md) | Test strategy, error numbering, cases, expected output and release gates |
| [Current Project Status](docs/PROJECT_STATUS.md) | Delivered scope, verification results, open items and the AI update protocol |
| [Phase 2 Backlog](docs/PHASE2_BACKLOG.md) | Item-by-item Phase 2 review record; subsequent delivery has been taken over by ROADMAP batches |
| [Conventions Summary](docs/AGENT_CONVENTIONS.md) / [Handoff Snapshot](docs/AGENT_HANDOFF.md) | The latter is a **historical snapshot dated 2026-10-23** — a point-in-time record only, not the current state |
| [.env.example](.env.example) | Configuration template containing no real secrets |
| [Product Vision Document](产品设想文档.md) | Original requirements and extension ideas |

## Technical Approach

The table below is deliberately split into two columns: **what is actually running today** and **what exists only in the design with no code yet**. Reading the right-hand column as current state is the easiest way to be misled by this project — `AGENTS.md` §1 was written for exactly that reason.

| Aspect | Current state (verifiable in code) | Design target (not wired, zero implementation) |
|---|---|---|
| Backend | `uv` + Python 3.13 + FastAPI + SQLAlchemy + MySQL 8; heavy work goes through Redis + ARQ (in-memory fallback locally) | Celery, S3, WebRTC |
| Frontend | Vue 3 + TypeScript + Vite + Element Plus, responsive web (`ADR-001`) | Mini program / native app |
| AI | **BYOK**: the user enters their own DeepSeek key in the UI and the backend carries it when calling DeepSeek (`ADR-009` polish / `ADR-011` JD skill extraction / `ADR-013` matching / `ADR-028` profile summary / `ADR-031` external PDF resume structuring), **non-streaming** | A server-held-key calling model (deprecated; `DEEPSEEK_API_KEY` in `.env` is no longer read by any code) |
| Voice | Browser `MediaRecorder` capture + **direct** calls to DeepSeek voice tools for transcription (`ADR-030`, turn-based interviews) | WebRTC audio/video rooms (the `media_rooms`-style tables were ruled out) |
| Files | No object storage: batch download is a synchronously generated ZIP (`ADR-029`) and PDF is rendered by the host browser; importing an external PDF reads the text layer first (`pypdf`) and only falls back to local OCR when that fails (`pypdfium2` rendering + `rapidocr`/`onnxruntime`, models shipped inside the wheel, no network, `ADR-031` v2) | S3-compatible private object storage, cloud OCR (student names and phone numbers are not handed to a second provider) |
| Deployment | One-click local start via `start.bat` / `start-local.ps1`; CI runs on **PRs** in GitHub Actions; **the production stack has been deployed on a real Alibaba Cloud ECS instance and accepted** (2026-11-17, see [ADR-008](docs/adr/ADR-008-docker-configs-unverified.md) revision 34) | The development `docker-compose.yml` has still never been brought up on any machine |

The database currently holds **23 tables / 28 Alembic revisions** (the 5 Phase 1 baseline tables were expanded long ago, and every new table must have an ADR first — `AGENTS.md` §4.2).

The local and demo environments use the MySQL database `resume_mvp_dev`; the integration tests use `resume_mvp_test` (the same name as the MySQL service in CI). Use `.env.example` as the template for local configuration and point `DATABASE_URL` at the same database. Both databases only need to be created once:

```sql
CREATE DATABASE IF NOT EXISTS resume_mvp_dev  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS resume_mvp_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> `MYSQL_DATABASE` is only a human-readable record field; no code reads it. What actually decides which database is used is `DATABASE_URL` (`app/core/config.py:22`, `alembic/env.py:12`). Keeping the two consistent is only about not misleading a reader.

## Configuration Security

```powershell
Copy-Item .env.example .env
```

Development may use local configuration; in production, **only two settings are actually validated at startup** (`app/core/config.py::validate_production_database_url` and `::validate_jwt_secret_key` — if either is missing, startup is refused). The list:

- `JWT_SECRET_KEY`: a strong random string of at least 32 characters (missing or shorter than 32 in production = startup fails)
- `DATABASE_URL` (including `MYSQL_PASSWORD`): missing in production = startup fails
- `FRONTEND_URL`, `ALLOWED_ORIGINS`: CORS and callback addresses, adjusted per deployment domain

The following items are **no longer production must-change values** — stop rotating them out of habit:

- `DEEPSEEK_API_KEY`: after BYOK, **no code reads it** (`app/integrations/copy_writer.py:143` says as much). Leaving it in place only makes people believe that changing it changes the AI credential.
- `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`: not a single line of object storage is wired (`AGENTS.md` §1.5, outside `ADR-008`). Require them only when that is actually integrated.

Real `.env` files, database backups and JWT secrets must never be committed to the repository.

**The AI key goes through BYOK (ADR-009 + ADR-010 + ADR-011 + ADR-013 + ADR-028 + ADR-030)**: the user enters their own DeepSeek key under "AI Settings" inside the **Account drawer on the right of the top bar**. It lives only in browser `sessionStorage` (cleared when the tab closes, never written to `localStorage`); the server holds it in memory for the duration of that request only and **never persists it or writes it to logs** — which is why `DEEPSEEK_API_KEY` in `.env` is no longer read by any code. "Generate resume" is a pure template path and neither needs nor sends this key.

Where a key may appear is decided by **two fail-closed allowlists** in `frontend/src/api/client.ts`; a request that is not on either list does not send a single byte:

| Table | Endpoints | Which header carries the key | Sent to |
|---|---|---|---|
| `BYOK_KEY_PATHS` | `POST /resumes/polish`, `POST /jobs/extract-skills`, `POST /resumes/import-pdf` (the last one added in round 28 by `ADR-031` 2.6; all three are **exact matches**, not prefixes) | `X-DeepSeek-Key` | Our own domain |
| `BYOK_KEY_PATTERNS` | `POST /jobs/{uuid}/match`, `POST /interviews/{uuid}/messages`, `POST /assessments/sessions/{uuid}/summary` | `X-DeepSeek-Key` | Our own domain |
| `DEEPSEEK_DIRECT_PATHS` | `/v1/tools/asr`, `/v1/tools/tts` (called directly by the browser; audio is never uploaded and transcriptions are deleted immediately, `ADR-030`) | `Authorization` | `api.deepseek.com` |

**Adding one URL means adding one injection surface and requires written approval from the owner, one URL at a time** (`AGENTS.md` §3.2, last line; this is not covered by the 2026-11-08 global unfreeze).

### Test accounts: kept for the LAN, must be cleaned up or password-hardened before going public

| Account | Role | Password | Origin |
|---|---|---|---|
| `cjlu_admin` | `school_admin` | `12345678` | `scripts/seed_cjlu.py` (organization "China Jiliang University", code `CJLU2026`) |
| `cjlu_teacher` | `teacher` | `12345678` | Same as above |
| `role_nav_probe_23` | `student` | `Probe#2026a` | A probe account registered during the narrow-screen role-routing test on 2026-11-15 |

**Owner's ruling on 2026-11-15: all three are kept for now**, so that LAN testing can log in and compare the three roles directly. They use weak passwords, so **each must be deleted or given a strong password before public deployment** — this is a precondition for launch, not post-launch cleanup. The same debt is recorded in the organization section of `docs/API.md` and in §4 of `docs/PROJECT_STATUS.md` (the batch of UI probe accounts).

## Design Principles

1. Frontend and backend are separated by responsibility; every sensitive business check is enforced on the backend.
2. AI only offers traceable, editable suggestions; it never makes hiring decisions on behalf of a teacher or an employer.
3. Matching results must be explainable, and student data is isolated by organization, role and authorization.
4. Components, colors, forms, empty states and error states are uniform; white is the base and mobile is supported.
5. When external AI/media services are unavailable, core profile and job data stay accessible and tasks can be retried.

## Research and Governance Baseline

The design references the NIST AI RMF, the OWASP API Security Top 10, W3C WebRTC, ISO/IEC 25010:2023, and papers on RAG and FActScore. Concrete requirements and citations are in the [Product Requirements Document](docs/PRD.md), the [Development and Architecture Document](docs/DEVELOPMENT.md) and the [Testing Document](docs/TESTING.md).

## 🚀 One-Click Local Start (Windows)

This is the **only start-up path currently promised to work**. It needs no Docker and **no administrator privileges**.

Prerequisites (install once):

- [Node.js](https://nodejs.org/en) 22 or newer (npm included)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- A local MySQL 8 instance running (default port 3306)
- A `.env` in the project root whose `DATABASE_URL` points at your MySQL
- **Redis is optional.** From round 31 onward (`ADR-034`), when `REDIS_URL` is absent from `.env`,
  `development` falls back to an **in-memory runtime**: "import PDF resume" and "batch export"
  keep working, tasks execute inside the web process, and the frontend experience matches
  production (202 + polling + progress bar).
  **The cost is stated openly**: it only works with a **single web process**
  (`uvicorn --workers >1` returns 404), and the `WORKER_*_MAX_JOBS` concurrency limits do not apply.
  → To test **multi-process concurrency or the full async chain** (three queues, a real worker,
  observing a stalled task), start Redis and the ARQ workers as described in
  [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) §11.2; then set `REDIS_URL` in `.env` — once it is
  set, no fallback happens.

Once the prerequisites are met:

> **Double-click `start.bat` in the project root (Windows one-click start).**

`start.bat` is a safe launcher shell around `start-local.ps1` and does exactly one extra thing — it starts the script with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-local.ps1"`.

- `Bypass` applies **only to that one PowerShell process** (Process scope): it writes no registry keys and changes neither machine-level `LocalMachine` nor user-level `CurrentUser` policy; it disappears when the window closes and cannot override restrictions pushed by Group Policy (GPO).
- Everything runs with **normal privileges**: the script only probes TCP ports and opens new windows. No command needs an administrator, and nothing on the system drive is read or written.
- On error, `pause` keeps the console window open so it can be screenshotted.

The script does four things in order, with colored messages throughout:

1. Check that `uv` / `npm` are installed, that `.env` exists, and that the MySQL port is reachable;
2. Open a new window for the backend: first `uv run alembic upgrade head` to sync the schema, then `uv run uvicorn app.main:app --reload`;
3. Open a new window for the frontend: enter `frontend`, run `npm install` on first run, then `npm run dev`;
   **(async tasks) nothing needs to be started by default** — without `REDIS_URL` in `.env` the
   in-memory fallback is used. Only after `REDIS_URL` is set in `.env` do you need another window
   for the workers (`uv run arq app.worker.OcrWorkerSettings` / `PdfWorkerSettings`);
   **setting the address without starting the workers shows up as "the task stays pending forever"**,
   not as an error. The three queues and their concurrency settings are in
   [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) §11.2;
4. Poll `http://127.0.0.1:8000/api/v1/health` and `http://localhost:5173`, and open the page in the default browser once both services actually respond.

After start-up:

| Entry point | Address |
|---|---|
| Web UI | http://localhost:5173 |
| Backend health check | http://127.0.0.1:8000/api/v1/health |
| API docs | http://127.0.0.1:8000/docs |

To stop: close the "backend" and "frontend" windows (and the worker window, if you started one).

Common issues:

- **Running `start-local.ps1` manually in PowerShell is blocked by the execution policy**: use the
  `start.bat` entry point instead; or bypass it for this process only, this once:
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\start-local.ps1`.
  Neither needs administrator privileges, and neither requires changing the global `ExecutionPolicy`.
- **It reports that MySQL is not running on 127.0.0.1:3306**: press `Win+R`, type `services.msc`, find
  the MySQL service and choose "Start"; or run `net start MySQL80` in an administrator PowerShell.
- **The backend window shows "database migration failed"**: nine times out of ten the account, password
  or database name in `DATABASE_URL` is wrong. Fix it and re-run the script.
- **The page loads but API calls fail**: read the error in the backend window first; do not change
  frontend code.
- **Why does the script "export `DATABASE_URL`"?** Because `alembic/env.py` reads only process
  environment variables, not `.env`; without the injection it throws `RuntimeError` at step one
  (see "Local Development Conventions" below).

## UI Structure (current state of the frontend track)

The criteria themselves belong to [docs/FRONTEND_DESIGN.md](docs/FRONTEND_DESIGN.md); this section only describes what you see when the page opens:

- **The outer frame is defined in exactly one place** (convention L1): `frontend/src/components/AppLayout.vue`. A second hand-placed `<AppTopBar />` in any view turns the test red.
- **The top is a single sticky frame** `.app-chrome`: the brand bar plus the secondary navigation `ModuleNav.vue`. On wide screens the secondary navigation **does not disappear on scroll**, and a "collapse / expand navigation" control on the right is driven by the user; after collapsing it still says which module you are in. **On narrow screens (≤760px) it becomes a single "current module ▾" row plus a vertical menu**, and the whole horizontally-scrolling row is retired — the seven tabs needed 242px of scrolling at 375px, and the ones scrolled out of view could be neither seen nor clicked (2026-11-15, round 24).
- **Module entries exist in exactly one table** (convention L4): `APP_MODULES` in `frontend/src/config/modules.ts` — 7 modules, each with `visibleToRoles`, so **navigation and the workspace filter entries by role** (5 for students, 7 for the teacher side), and the filter exists only as `modulesVisibleTo(role)`. The top bar contains no module names at all — duplicated entries are a defect this project has fixed twice. **This only removes entries; it does not change boundaries**: permissions for `/teacher/*` and `/organization/*` are still enforced by the backend (403 / 400).
- **When a student has no class, a "Join a class" banner sits at the top of the workspace**: the signal is a 404 from `GET /organizations/me`. Clicking it opens a panel with only an invitation-code input, which uses the existing `/invitations/redeem`; on success the page is re-read (the banner disappears immediately and a "Joined '××'" receipt remains). It repairs the path that role filtering had cut off (the "join with an invitation code" screen originally lived in `/organization`).
- **Where the resume list lives depends on width**: at ≥1440 it is a permanently docked left rail (240px, **collapsed by default**; the button on the left edge both pulls it out and puts it back); between 1100 and 1439, and on narrow screens, it becomes a right-hand drawer. The same content component `ResumeList.vue` is shared by both shells.
- **Account matters live on the right**: the "Account" button on the right of the top bar opens `UserDrawer.vue`, which holds three rows — "Profile", "Settings", "Sign out" (2026-11-15, round 25; the old "AI Settings" shortcut has been merged into the settings dialog). "Profile" is a dialog with a read-only identity block of three fields plus a four-field editable header; on save it **sends back the previous value for any field that had a value and was cleared this time** — PUT replaces the whole object, and clearing must not mean deleting. The "Settings" dialog has two tabs, "AI key" and "Other settings".
- **Type scale**: brand name 20px > user name 17px > body and helper text. Page titles are 22px, always above the brand name.
- **Every page shares one shell**: `min(900px, 100%)` (1180px for the job library), centered, with `32px 48px 72px` padding and `.page-heading` (`.eyebrow` / `h1` / `.intro`, three lines).
- **Forms share one arrangement**: inputs/selects take `flex: 1 1 180~220px` and absorb the remaining width of the row; buttons are right-aligned and match the control height of 34px.
- **When AI is not configured there is a direct route to settings** (2026-11-15, round 25): in polish, job-skill extraction, person–job matching and mock interview, whenever the failure or degradation reason is "credential not filled in / credential invalid", an extra line "👉 Configure the AI key here" appears under the message, and clicking it opens the settings dialog from any page (open/close state is kept in `stores/ui.ts`).
- **Explanatory microcopy stays off the screen** (convention C5, extended 2026-11-14 in round 22): 32 places of pale-grey text explaining "what this thing is / how to use it" have been deleted; the four exemptions kept are validation and error messages, data and state, notices that consume the user's credential and quota, and empty states plus irreversible consequences.

## Verification and Gates

Run these after a change; all of them are part of the PR check in CI (`.github/workflows/ci.yml`; the push trigger has been removed, so **they only run when a PR is opened**):

```bash
# Backend (824 collected)
uv run ruff format --check app tests alembic scripts
uv run ruff check app tests scripts
uv run mypy app scripts
uv run lint-imports          # Layering gate: api → services → repositories → integrations
uv run pytest -q             # Includes the "empty DB upgrade head → downgrade base" migration check

# Frontend (559 tests / 47 specs)
cd frontend
npm run lint
npm run typecheck            # vue-tsc --noEmit
npm run test                 # vitest run
npm run build

# Documentation and copy gates (five, all in scripts/)
uv run python scripts/check_ui_copy.py            # G1 copy: emoji / jargon / exemption markers
uv run python scripts/check_error_copy.py         # G3 error copy table
uv run python scripts/check_design_tokens.py      # G4 tokens: bare hex / bare font sizes
uv run python scripts/check_route_reachability.py # G5 route reachability (orphan views)
uv run python scripts/check_docs_citations.py     # file:line / path::symbol pointers in docs
```

Layout and visual changes **must be verified in a real browser at both 375px and 1280px** (convention R2): this machine has neither Docker nor Playwright, so the recipe is headless Edge + CDP, documented in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) §10.2. jsdom does not evaluate media queries, so a CSS breakpoint passing in unit tests is not verification.

## Local Development Conventions

> **⚠️ After pulling the latest code, you must run `uv run alembic upgrade head` before starting the backend, to sync the database.**
>
> The table structure in the code is defined by the migration scripts under `alembic/versions/`; **tables are not created automatically at startup**.
> The symptom of forgetting it: the service starts fine, but the first request that touches a new table
> (for example `refresh_tokens`) fails with `Table 'xxx' doesn't exist` / HTTP 500. To surface this
> early, the backend compares the database's Alembic revision with the latest revision in the code at
> startup: development/test print a prominent warning, and **production refuses to start**.

`alembic/env.py` reads only environment variables, not `.env`, so export `DATABASE_URL` into the current shell before running migrations:

```bash
# Git Bash / macOS / Linux
export DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | cut -d= -f2-)"
uv run alembic upgrade head
uv run alembic current   # confirm head has been reached
```

```powershell
# PowerShell
$env:DATABASE_URL = (Get-Content .env | Where-Object { $_ -match '^DATABASE_URL=' }) -replace '^DATABASE_URL=', ''
uv run alembic upgrade head
uv run alembic current
```

After backend code is in place:

```powershell
uv venv --python 3.13
uv sync
Copy-Item .env.example .env
uv run alembic upgrade head   # sync the schema (mandatory after pulling new code)
uv run uvicorn app.main:app --reload
```

After frontend code is in place:

```powershell
cd frontend
npm install
npm run dev
```

Requirements for deployment, migrations, testing, monitoring and API integration follow the [Development and Architecture Document](docs/DEVELOPMENT.md) and the [API Document](docs/API.md).

## 🐳 Docker Start (optional)

> ⚠️ Verification boundary (updated in round 34): the development machine has no Docker, so **this
> development compose stack has still never been brought up on any machine**. The shared
> `Dockerfile.backend` is verified on a real box: on 2026-11-17, on the owner's Alibaba Cloud ECS
> (Ubuntu 22.04, a three-month trial free instance), the first bring-up of the production stack
> exposed three "wrong coordinate"-level defects (task volume ownership, no print CSS in the image,
> no browser installed in `web`); after PRs #52–#54 fixed them and the stack was rebuilt, the
> acceptance commands below and the import/export chains were verified (see the production section
> and [ADR-008](docs/adr/ADR-008-docker-configs-unverified.md) revisions 32–34).
> For day-to-day development use `start-local.ps1` from the previous section.

Prerequisites: Docker Desktop installed, and a `.env` in the project root. `docker compose` reads `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` and `JWT_SECRET_KEY` from that root `.env` automatically; when one is missing, compose fails immediately with a `:?` message instead of starting a half-configured service.

```powershell
# Parse and check variable interpolation only, without building images
docker compose config

# Build and start all seven services: db + redis + backend + worker-ocr + worker-pdf + worker-default + frontend
docker compose up --build
```

Then open **http://localhost:8080** (the frontend serves static files through nginx and reverse-proxies `/api/` to `backend:8000`, same-origin with the browser, so no extra CORS configuration is needed). The backend container's start command is `alembic upgrade head && uvicorn ...`, so an empty database volume gets its tables created automatically.

Three notes that apply only to this stack:

- **All three workers start**, including `worker-default` (which the production stack deliberately
  does not). **Not starting a worker shows up as "the task stays pending forever"**: the 202 for
  PDF import and batch export still comes back and the job really is in the queue — nobody just
  consumes it. When debugging other endpoints with only `backend`, you can run
  `docker compose up -d db redis backend frontend`.
- Redis port 6379 **is published to the host** in this stack, only so that a local `redis-cli` can
  inspect queue depth (`zcard arq:queue:ocr` / `arq:queue:pdf`); the production stack does not
  publish it.
- **Both export chains really work inside containers now** (added in round 33): `backend` and
  `worker-pdf` both receive `INSTALL_PDF_BROWSER: "1"` and install the same Chromium plus
  `fonts-noto-cjk`. Neither had it before, and the symptom was "batch packaging fails a second after
  the 202, single export fails with 503 consistently" while health checks were all green. The cost is
  a much slower first `build` and roughly this much extra per image (by the Debian index closure: 230
  packages / 256 MB download / 820 MB `Installed-Size`). The `start-local.ps1` path does not need it —
  Edge on the host is found by `pdf_renderer._resolve_browser`.

Clock and permissions follow the same policy as production (synced in round 32 — no double standard): application containers use `TZ: Asia/Shanghai` (while `db` stays on UTC), workers disable the image's HTTP health check, and the shutdown grace period is 10 seconds. Ownership of `/app/temp` and `/app/downloads` is handled by `Dockerfile.backend` before dropping privileges, so new volumes need no manual `chown`; for old volumes see "Volume permissions" in the production section below.

```powershell
docker compose down      # stop services, keep the MySQL data volume
docker compose down -v   # also delete the data volume — data will be lost
```

This development stack has still never been brought up (the production one was rebuilt and accepted on Alibaba Cloud ECS on 2026-11-17 — see the next section) — for day-to-day development use `start-local.ps1` above.

## 🚢 One-Click Production Deployment (`docker-compose.prod.yml`)

> ✅ Verification status (updated in round 34): on 2026-11-17 this stack was **deployed on a real box
> and accepted** on the owner's Alibaba Cloud ECS (Ubuntu 22.04, a three-month trial free instance,
> 2 vCPU / 4 GiB). The first bring-up caught three configuration defects (volume ownership, print
> CSS, no browser installed in `web` — see the upgrade table below "Upgrading from images older than
> round 32"); after PRs #52–#54 fixed them and the stack was rebuilt with `down -v`, this section's
> acceptance commands plus the import-PDF / single-export / batch-packaging chains were verified
> with no problems (confirmed by the owner). The development machine still has no Docker;
> **when deploying to a new machine, run this section's acceptance steps once first**.

One command brings up six containers: `db` (MySQL), `redis`, `web` (Uvicorn), `worker-ocr`, `worker-pdf` and `frontend` (nginx). The first five are **mandatory**; `frontend` is the UI itself, so you may delete that block entirely if you put your own reverse proxy in front. `worker-default` is deliberately not started — no task is registered on that queue today.

### 1. Prepare `.env` (a missing item fails on the spot — no half-configured containers)

| Variable | Required | Notes |
|---|---|---|
| `MYSQL_PASSWORD` | ✅ | Database password — **do not use the example value** |
| `MYSQL_DATABASE` | ✅ | Database name (an empty database gets its tables created) |
| `MYSQL_USER` |  | Defaults to `root` |
| `JWT_SECRET_KEY` | ✅ | **≥ 32 characters**; under `production` a shorter value fails at startup. Generate with `openssl rand -hex 32` (or `python -c "import secrets;print(secrets.token_hex(32))"`) |
| `PUBLIC_ORIGIN` | ✅ | Public address, e.g. `https://career.example.com`; written into `FRONTEND_URL` / `ALLOWED_ORIGINS` |
| `WORKER_PDF_MAX_JOBS` |  | Defaults to `2`. **This is the main memory budget**: each task starts a headless-browser subprocess, so 2 means at most two Chromium instances. Set `1` on a 2 vCPU / 4 GiB machine |
| `WORKER_OCR_MAX_JOBS` |  | Defaults to `4`. **Raising it does not multiply OCR model memory** — `app/integrations/pdf_ocr.py` serializes recognition behind one global `_CALL_LOCK` with a single engine shared by the whole process; extra concurrency just queues on the lock while each holds its decoded page bitmaps. On 2 vCPU / 4 GiB use `2` (so the DeepSeek step can still overlap) |
| How to pick the two numbers above |  | Do not copy the comments — measure once: `docker stats --no-stream` (once at idle, once during a batch export), `free -m`, `sudo dmesg -T \| grep -i 'killed process'`. The one killed by the kernel is usually `worker-pdf` or `mysqld` |
| `ENABLE_API_DOCS` |  | Defaults to `false` (production does not expose `/docs` or `/openapi.json`) |
| `APT_MIRROR` |  | Affects `build` only, not runtime. Defaults to the official `deb.debian.org`, which builds anywhere. Set it only when a machine is slow: on **Alibaba Cloud ECS** use `mirrors.cloud.aliyuncs.com` (an internal mirror that does not consume public bandwidth); on other machines in China use `mirrors.aliyun.com`. Note that the internal mirror is reachable only inside Alibaba Cloud — do not carry it to another server |

`production` is **mandatory**: missing `DATABASE_URL` / `REDIS_URL` / a strong JWT key all fail at container startup, and when the schema is behind the code, `check_migration_state` refuses to start. It does **not** fall back to an in-memory queue the way local mode does (`ADR-034` §2.1) — production concurrency limits only exist in a real queue.

### 2. Start the services

```bash
# Build and start in the background (first run builds images; both `web` and `worker-pdf` install Chromium, a few extra minutes each)
docker compose -f docker-compose.prod.yml up -d --build

# Check status: `web` waits for `db` / `redis` to be healthy; `frontend` waits for `web`
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f web worker-pdf
```

`web`'s start command is `alembic upgrade head && uvicorn ...`, so the first bring-up against an empty database creates the tables.

If the build is slow, check two things first (learned on a real box on 2026-11-17): one is the instance's **public bandwidth tier** — a fixed 1 Mbps link gives only 125 KB/s, and no public mirror changes that; temporarily switching to "pay-by-traffic 100 Mbps" for the build and back afterwards is the least painful option. The other is the apt source — on Alibaba Cloud ECS put `APT_MIRROR=mirrors.cloud.aliyuncs.com` in `.env` to use the internal mirror. `uv sync` (PyPI) and `npm ci` still go over the public internet; the internal mirror only saves the apt layers.

### 3. Volume permissions: `task-temp` / `task-downloads`

Imported PDFs land in the `task-temp` volume first (`/app/temp/imports`, written by `web`, read by workers), and exported ZIPs land in `task-downloads` (`/app/downloads`, written by `worker-pdf`, read by `web` when the user downloads). All three containers mount **the same volumes**, so the user that actually writes them (`appuser`, uid 10001) must have permission.

**New volumes need nothing**: `Dockerfile.backend` creates both directories and `chown`s them to that user before switching to `appuser`, and when Docker creates an empty named volume for the first time it carries the directory's owner from the image into the volume.

**Existing volumes must be chowned once by hand** — as soon as a volume is non-empty, Docker does not replay the image's ownership. Volumes left by pre-round-32 images are still `root:root`, and the symptom is both chains failing in a hard-to-guess way:

- Import PDF: **the endpoint returns 500 directly**, not even a 202 goes out. The staging file is written by `web` before enqueueing (`app/services/task_service.py` calls `save_import_file`); if that write fails, the job never reaches the queue and `logs web` shows `PermissionError: /app/temp/imports/...`;
- Batch export: the task ends in failure and the progress row in the UI reports it — the ZIP is written by `worker-pdf` (`save_download_file`), so the artifact never lands in `task-downloads`.

```bash
# Enter the web container once as root to change ownership (the default appuser cannot modify root-owned directories)
# `-R` is required: an old volume may already contain root-created partial subdirectories (e.g. /app/temp/imports)
docker compose -f docker-compose.prod.yml run --rm --no-deps --user root web \
  sh -c "chown -R 10001:10001 /app/temp /app/downloads"

# Confirm both directories belong to 10001
docker compose -f docker-compose.prod.yml exec web ls -ld /app/temp /app/downloads
```

If you prefer to wipe and start over (losing in-flight tasks and undownloaded artifacts, but **not touching the database**):

```bash
docker compose -f docker-compose.prod.yml down
docker volume rm ai-career-platform_task-temp ai-career-platform_task-downloads
docker compose -f docker-compose.prod.yml up -d
```

Do not use `down -v` to clear these two volumes: it deletes `mysql-data` as well and the database is gone.

### 4. Acceptance (all three must pass before it counts as up)

```bash
curl -fsS http://127.0.0.1/api/v1/health                 # 200 + {"status":"ok",...}
docker compose -f docker-compose.prod.yml exec redis redis-cli ping   # PONG
docker compose -f docker-compose.prod.yml exec redis redis-cli zcard arq:queue:pdf  # 0
```

Then walk the two heavy chains in the browser: **import a PDF** (watch the progress bar advance to "organize into a profile") and **batch export on the teacher side** (watch the progress row plus the automatic ZIP download). If the export fails and shows "export service temporarily unavailable", Chromium is missing from `worker-pdf` — look for `PDF 渲染调用失败` in `logs worker-pdf`.

The first `curl` goes through nginx's `/api` reverse proxy to the backend, so it also verifies the `backend` network alias that `web` must be reachable by (`docker/nginx.conf`'s `proxy_pass` uses exactly that name). If it returns 502 while `curl http://127.0.0.1:8000/api/v1/health` directly returns 200, the alias is not taking effect.

Three more checks look at **whether things really exist inside the image** — they do not check whether a process is alive, which is exactly what catches "all containers green but the feature unavailable":

```bash
# Both places that render PDFs need a browser: web (single export is a synchronous endpoint) and worker-pdf (batch packaging)
docker compose -f docker-compose.prod.yml exec web sh -c 'command -v chromium'
docker compose -f docker-compose.prod.yml exec worker-pdf sh -c 'command -v chromium'
# Print CSS: without it both export chains fail instantly with PDF_RENDERER_UNAVAILABLE
docker compose -f docker-compose.prod.yml exec worker-pdf ls -l /app/frontend/src/styles.css
# Timezone: must print +08 rather than UTC, otherwise the TZ line did not land
docker compose -f docker-compose.prod.yml exec web date
```

### 5. Upgrading from images older than round 32 (the path taken on the cloud server)

Image contents changed (print CSS, task directory ownership, tzdata, the browser in `web`), so **editing the compose file or `restart` is not an upgrade**: `build` uses the code in the git working tree, and `up -d` only recreates containers when the image changed. Old images behave like this:

| Symptom | What is really missing | One check |
|---|---|---|
| Import PDF returns 500, `logs web` shows `PermissionError: '/app/temp/imports'` | The volume was created by an old image and is still owned by root | `exec web ls -ld /app/temp` |
| Batch packaging accepts the task then fails with `PDF_RENDERER_UNAVAILABLE` (`stage=rendering`) **0.1 s later** | The image has no `/app/frontend/src/*` | `exec worker-pdf ls -l /app/frontend/src/styles.css` |
| Single export consistently returns 503 "cannot generate PDF temporarily" | The `web` image has no Chromium | `exec web sh -c 'command -v chromium'` |
| Job expiry takes effect 8 hours late, container log timestamps are UTC | Still running a pre-#52 compose file (no `TZ`) or tzdata missing | `exec web date` |

Do it all in one go:

```bash
cd ~/AI-Innovation-Practice
git pull --ff-only origin master                    # get the working tree up to date first, so `build` sees the new code
docker compose -f docker-compose.prod.yml build     # image contents changed — a rebuild is mandatory
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml run --rm --no-deps --user root web \
  sh -c "chown -R 10001:10001 /app/temp /app/downloads"   # old volumes still need this once (new ones do not)
```

### 6. Day-to-day operations

```bash
docker compose -f docker-compose.prod.yml restart worker-pdf   # restart a single queue
docker compose -f docker-compose.prod.yml pull && up -d        # upgrade images
docker compose -f docker-compose.prod.yml down                 # stop services, keep data volumes
docker compose -f docker-compose.prod.yml down -v              # also delete MySQL / Redis data
```

- **Migrations**: with a single replica, `web` runs them at startup. With **multiple replicas**, first
  run `docker compose -f docker-compose.prod.yml run --rm web alembic upgrade head` separately,
  otherwise several containers race for the same DDL.
- **Queue backlog**: `redis-cli zcard arq:queue:ocr` / `arq:queue:pdf` (a `arq:queue:pdf` that never
  drains means browser rendering is slow or `worker-pdf` is down). There is no metrics system — this is
  today's only observation method.
- **Redis has AOF enabled** (`appendonly yes`): a restart does not lose in-flight tasks. Task records
  themselves have a TTL (`WORKER_TASK_TTL_SECONDS`, default 30 minutes), so they do not grow forever.
- **Changing `JWT_SECRET_KEY` makes in-flight tasks' BYOK ciphertext undecryptable** (the encryption
  key is derived from it) — pick a window with no tasks running.
- **Do not deploy on multiple machines**: task artifacts live on the local disk (the
  `task-downloads` volume), as recorded in `ADR-033` §7 item 5.
- **Workers get only 10 seconds on restart** (`stop_grace_period`): whatever is still running after
  SIGTERM is killed hard, and the next poll settles it as `TASK_LOST` — the UI shows "the task was
  interrupted halfway, please upload again (or start the export again)." It never pretends to have
  succeeded. To "finish running work on shutdown", set `worker-ocr` / `worker-pdf` back to 60s / 120s.
- **The two workers have no HTTP health check** (`healthcheck: disable: true`): they are background
  processes that do not listen on 8000, and reusing the image's `/api/v1/health` would leave them
  permanently unhealthy in `ps`. That is why `docker compose ps` only shows health for `db` / `redis` /
  `web` / `frontend`; whether a worker is alive must be read from `logs` or from whether running tasks
  make progress.
- **Application containers are pinned to UTC+8** (`TZ: Asia/Shanghai`, in `x-app-env`): `db` stays on
  UTC, matching the naive-UTC write policy in the database (`app/core/datetime_display.py`). Without
  the TZ line, a UTC machine judges "today" as yesterday between 00:00 and 08:00 Beijing time, and job
  expiry only takes effect 8 hours late. This TZ line lands via glibc reading
  `/usr/share/zoneinfo`, which is why `Dockerfile.backend` explicitly installs a `tzdata` layer on the
  unconditional path — `INSTALL_PDF_BROWSER` is passed only to `worker-pdf`, so moving it inside the
  `if` would leave `web` and `worker-ocr` without that safety net.

## Disclaimer

> This project is generated by artificial intelligence and supervised by humans.
