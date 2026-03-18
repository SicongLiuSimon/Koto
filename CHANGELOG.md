# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [1.3.0] — 2026-03-18

### Added
- **Playwright E2E Browser Tests** (63 tests): Full UI testing suite covering page loads, session management, chat interface, skill marketplace, settings, button sweep, mobile responsive, and accessibility checks
- **API Smoke Tests** (35 tests): Comprehensive endpoint coverage for memory, macro, setup, voice, document, notebook, ops, shadow, and utility APIs
- **Mobile Responsive Tests**: Verify pages render correctly at phone (375×667, 414×896) and tablet (768×1024) viewports with overflow and clipping detection
- **Accessibility Tests**: WCAG checks for alt text, form labels, button names, heading hierarchy, tabindex, lang attribute, and landmark roles
- **Server-Only Mode** (`KOTO_SERVER_ONLY=1`): New env var to start Flask without GUI/pywebview — enables full health check testing in CI
- **Installer E2E Improvements**: File size validation, Start Menu shortcut check, reinstall/upgrade cycle test, registry cleanup verification, `/api/ping` endpoint check
- **E2E CI Pipeline Job**: Playwright tests now run automatically on push (Windows runner, informational)

### Fixed
- **deleteSession null reference bug**: Fixed `TypeError: Cannot read properties of null (reading 'outerHTML')` when deleting the current session and `welcomeScreen` element was already removed from DOM (`web/static/js/app.js`)

### Changed
- Installer E2E tests now use `RequireHealth:$true` (was `$false`) — health endpoint is actually verified in CI builds

### Added
- **Modular Blueprint Architecture**: Extracted ~206 routes from monolithic `web/app.py` into 14 Flask blueprints (sessions, analytics, proactive, execution, knowledge, file_editor, dev, voice, document, file_organize, workspace, settings, misc_api, pages)
- **Skill Pipeline**: New `skill_pipeline.py` for structured skill execution with validation, routing, and fallback
- **Skill Tool Adapter**: New `skill_tool_adapter.py` bridging skills with the agent tool registry
- **Task Classifier**: ML-based task classification for intelligent request routing
- **Smart Dispatcher**: Enhanced model dispatching with intent analysis and local planner integration
- **Model Fallback Executor**: Automatic LLM failover with circuit breaker pattern
- **Conversation Tracker**: Long-running conversation context management
- **PersonalityMatrix**: 4-layer context injection for personalized responses
- **File Converter Engine**: Multi-format document conversion endpoint
- **Annotation & Chart Vision Plugins**: New agent plugins for image annotation and chart analysis
- **Output Validator**: Security-focused output sanitization for agent responses
- **Document Planner & Feedback Loop**: Iterative document generation with quality feedback
- **Swagger/OpenAPI docs** via flasgger at `/apidocs`
- **SQLite Migration Manager**: Lightweight schema versioning
- **Custom Exception Hierarchy**: Structured error types for all Koto subsystems
- **Landing Page**: Updated marketing site with download button, feature showcase, setup tabs
- **Bilingual Support**: EN/中文 marketing page
- **3,900+ tests** (up from 467): security, concurrency, circuit breaker, caching, XSS, path traversal, integration
- Structured JSON logging via `KOTO_LOG_FORMAT=json`
- Request ID tracing: `X-Request-ID` header for log correlation
- Global Flask error handlers returning JSON `{error, status, request_id}`
- `/api/info` endpoint exposing `{version, deploy_mode, auth_enabled}`
- Dependabot config for weekly pip + GitHub Actions dependency updates
- `.pre-commit-config.yaml` with black, isort, flake8, bandit hooks
- `docker-compose.yml` for local development with volume mounts
- `Makefile` with `dev`, `test`, `lint`, `format`, `build`, `audit` targets
- `pip-audit` CVE scanning step in CI (non-blocking)
- Dependency lock file for reproducible builds

### Changed
- **web/app.py reduced from ~20,800 to ~16,100 lines** via blueprint extraction
- Default model upgraded to `gemini-3.1-pro-preview`
- AIRouter refactored: removed `set_router_model`, uses internal `_ROUTER_MODEL_CHAIN`
- `print()` replaced with `logging` across 80+ web modules
- Proactive agent persists cooldown state across restarts
- RAG service upgraded with hybrid search improvements
- Training data builder and training database updates
- CI pipeline hardened: black, isort, bandit, pytest with coverage artifacts, Docker build

### Fixed
- Thread-safe singletons for shared services
- Bounded caches preventing unbounded memory growth
- Graceful shutdown with proper resource cleanup
- Deadlock in `TrainingDB.correct_label()`
- Path traversal in `file_converter` output directory
- XSS in `showNotification` — uses `escapeHtml` on message
- XSS in `md_to_html` fallback renderer
- Module whitelist for `importlib` entry_point loading
- Sandbox path validation in annotation plugin
- Platform-specific tests properly skipped on Linux CI (9 Windows-only tests)
- isort/black formatting compliance across all source files

### Security
- JWT secret startup validation: raises `RuntimeError` in cloud mode if `KOTO_JWT_SECRET` not set
- `werkzeug.secure_filename()` applied to all file upload filenames
- CODEOWNERS, PR template, issue templates, SECURITY.md added
- Branch protection ruleset configured

---

## [1.1.0] — 2025-01-XX

### Added
- Web UI improvements: dark/light theme toggle, improved chat layout
- Skills system: auto-builder and dynamic skill loading
- Knowledge Base routing with multi-source hybrid search
- LLM provider abstraction (Gemini, OpenAI, Claude, Ollama)
- Long-term memory module with FAISS vector index
- Learning module: training data builder and DB
- Document generation endpoint
- Unit and integration test suite (467 tests, 40% coverage)
- Agent core: ToolRegistry, datetime injection
- CI pipeline: lint (flake8/black/isort/bandit), pytest with coverage artifact, Docker build check

### Changed
- Centralized logging via `app/core/logging_setup.py` (RotatingFileHandler, `KOTO_LOG_LEVEL` env)
- `DEFAULT_MODEL` extracted to `app/core/config_defaults.py` (single source of truth)
- SQLite connection pooling via `threading.local()` (eliminates cross-thread conflicts)
- AIRouter and SmartDispatcher upgraded to LRU caches (256/128 entries)
- Skill manager upgraded to O(1) builtin prompt index
- Settings write-coalescing: 2s dirty timer reduces disk I/O
- Docker: non-root `koto` user, HEALTHCHECK start-period extended to 30s
- CI coverage threshold raised to 40%

### Fixed
- `PyPDF2` duplicate removed from `requirements.txt`
- `google-generativeai` → `google-genai>=1.0.0` in `requirements_voice.txt`
- Bare `except Exception` replaced with specific error handler in `agent_routes.py`

### Security
- Bandit security scan added to CI (non-blocking, surfaces issues)
- Docker image runs as non-root user

---

## [1.0.9] — 2025-01-XX

### Added
- Initial release pipeline with PyInstaller + Inno Setup installer
- E2E installer tests

---

## [1.0.0] — 2024-XX-XX

### Added
- Initial Koto AI assistant release
- Chat interface with Gemini integration
- File upload and processing
- Voice input support
- Local model support via Ollama
