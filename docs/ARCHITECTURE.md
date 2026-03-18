# Koto Architecture

> Last updated: 2025

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Browser / pywebview                │
│                   (HTML/CSS/JS Frontend)                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP/REST + SSE
┌──────────────────────────▼──────────────────────────────────┐
│                     Flask Application (web/app.py)          │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐  │
│  │ Auth     │  │ CORS     │  │ CSRF      │  │ Rate      │  │
│  │ Middleware│  │          │  │ Protection│  │ Limiting  │  │
│  └──────────┘  └──────────┘  └───────────┘  └───────────┘  │
├─────────────────────────────────────────────────────────────┤
│                     API Routes Layer                         │
│  /api/chat  /api/auth  /api/files  /api/goals  /api/jobs   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                  Unified Agent Framework                     │
│                  (app/core/agent/)                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Plugin System (AgentPlugin)             │    │
│  │  ┌──────────┐ ┌───────────┐ ┌───────────────────┐   │    │
│  │  │ Basic    │ │ System    │ │ Data Process      │   │    │
│  │  │ Tools    │ │ Tools     │ │ Plugin            │   │    │
│  │  └──────────┘ └───────────┘ └───────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌──────────────────┐  ┌──────────────────────────────┐     │
│  │ LangGraph        │  │ Gemini / LLM Provider       │     │
│  │ State Machine    │  │ (google-genai)               │     │
│  └──────────────────┘  └──────────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                    Services Layer                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐   │
│  │ File     │  │ Document │  │ Goal     │  │ Job       │   │
│  │ Registry │  │ Processing│  │ Manager  │  │ Runner    │   │
│  └──────────┘  └──────────┘  └──────────┘  └───────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Web Layer (`web/`)

| File | Responsibility |
|------|----------------|
| `app.py` | Flask app factory, route registration, middleware |
| `auth.py` | JWT authentication, rate limiting, user management |

### 2. Agent Framework (`app/core/agent/`)

The agent system uses a **plugin architecture**:

- **`base.py`** — `AgentPlugin` abstract base class
- **Plugins** register tools that the LLM can invoke
- **Sandboxing** — All code execution plugins use AST validation to block
  dangerous operations (imports, builtins, dunder access)

### 3. Plugin Security Model

```
User prompt → LLM → Tool call → AST validation → Sandboxed exec/eval
                                      │
                                      ├─ Blocked AST nodes (Import, ImportFrom)
                                      ├─ Blocked names (os, sys, subprocess...)
                                      ├─ Blocked attributes (__builtins__, __globals__...)
                                      └─ Safe builtins (no exec, eval, open...)
```

### 4. Authentication & Authorization

- **JWT-based** authentication with configurable expiry
- **3-tier rate limiting**: strict (10/min), standard (30/min), relaxed (120/min)
- **CSRF protection** via Flask-WTF on form-based routes
- Auth is **enabled by default**; set `KOTO_AUTH_ENABLED=false` for local dev

### 5. Data Flow

```
1. Client sends request → Flask middleware (auth, rate limit, CSRF)
2. Route handler invokes UnifiedAgent
3. Agent selects tool via LLM reasoning
4. Plugin validates input (AST check) → executes in sandbox
5. Result returned through SSE stream or JSON response
```

## Architecture Decision Records (ADRs)

### ADR-001: Plugin-based Agent Architecture

**Decision**: Use a plugin system where each capability is an independent
`AgentPlugin` subclass.

**Rationale**: Allows adding new tools without modifying the core agent loop.
Plugins are self-contained and testable in isolation.

### ADR-002: AST-based Sandbox for Code Execution

**Decision**: Validate all user-supplied code via `ast.parse()` + node
whitelist/blocklist before `exec()`/`eval()`.

**Rationale**: String-based blocklists are easy to bypass. AST walking catches
obfuscated attacks (e.g., `getattr(os, 'system')`) at the structural level.

### ADR-003: Auth Enabled by Default

**Decision**: `KOTO_AUTH_ENABLED` defaults to `"true"`.

**Rationale**: Secure-by-default prevents accidental exposure when deploying.
Local developers can opt out with `KOTO_AUTH_ENABLED=false`.

### ADR-004: Sliding-Window Rate Limiting

**Decision**: Replace daily counter with per-minute sliding window buckets.

**Rationale**: Daily counters don't prevent burst abuse. Sliding windows
provide smoother traffic shaping and allow tiered limits per endpoint
sensitivity.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KOTO_AUTH_ENABLED` | `true` | Enable/disable authentication |
| `KOTO_DEPLOY_MODE` | `local` | `local` or `cloud` |
| `KOTO_JWT_SECRET` | (ephemeral) | JWT signing secret |
| `KOTO_JWT_EXPIRY_HOURS` | `72` | Token lifetime |
| `KOTO_MAX_DAILY_REQUESTS` | `100` | Legacy daily request cap |
| `KOTO_CORS_ORIGINS` | `*` | Allowed CORS origins |
| `KOTO_SECRET_KEY` | (random) | Flask secret key for CSRF |
| `SENTRY_DSN` | (none) | Sentry error tracking DSN |
