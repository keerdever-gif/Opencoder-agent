# OpenCoder-Agent

OpenCoder-Agent is a modular Python assistant with Discord, CLI, and authenticated website API entry points. Its Manager routes each request to one of four specialist agents (Search, Code, Test, or Image), each with a small allow-listed toolset. The existing provider fallback, workspace file operations, web search, code verification loop, and optional E2B execution are reused.
<div align="center">
  <img src="imagenes/opencoder.gif" width="100%" alt="OpenCoder-Agent Banner">
  
  <br>
  <br>

  <a href="https://github.com/keerdever-gif/OpenCoder-Agent/releases"><img src="https://img.shields.io/badge/version-1.0.0-blue.svg" alt="Version"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-yellow.svg" alt="Python"></a>
  <a href="https://github.com/keerdever-gif/OpenCoder-Agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/status-active-brightgreen.svg" alt="Status">
</div>
## Features

- Discord bot, interactive CLI, and authenticated HTTP API for website integration
- OpenAI-compatible provider connections with fallback across configured API keys
- Workspace-scoped file tools and a 300-capability tool catalog
- DuckDuckGo search through DDGS
- Optional GitHub, Vercel, image generation, and E2B integrations
- Optional PostgreSQL conversation persistence
- Manager + four specialist sub-agents with isolated tool allow-lists
- Optional separate fast/specialist model overrides via environment variables
- Automatic code verification and bounded correction attempts
- Website integration through an authenticated `POST /api/chat` endpoint

## Requirements

- Python 3.10 or newer
- An API key for a supported LLM provider
- Discord credentials only when running in Discord mode
- PostgreSQL and E2B are optional

## Installation

```bash
git clone <repository-url>
cd OpenCoder-Agent
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `AI_API_KEY` (or `AI_API_KEY_1`). Add additional provider slots with `AI_API_KEY_2`, `AI_MODEL_2`, and optionally `AI_API_BASE_URL_2`. The numbered slots can be used for automatic provider fallback.

## Run the CLI

```bash
python main.py cli
```

Type `/exit` or `/quit` to end the session. The CLI does not require Discord credentials. PostgreSQL is optional; without `DATABASE_URL`, conversation history is not persisted between sessions.

## Run the website API

Set a long random `WEB_API_KEY` in `.env`, then start the API:

```bash
python main.py web
```

The default address is `http://localhost:8080`. The API exposes:

- `GET /health` — public liveness check.
- `POST /api/chat` — authenticated chat endpoint.

Example server-to-server request:

```bash
curl -X POST http://localhost:8080/api/chat \
  -H "Authorization: Bearer YOUR_WEB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message":"Create a small Python script","session_id":"visitor-session-1234"}'
```

The response contains `answer`, `route`, `image_url`, `files` (filenames only; server paths are never exposed), and the `session_id`. Reuse the returned `session_id` for follow-up messages in the same conversation. If omitted, the API creates a new session. Without PostgreSQL, short conversation history is held in process memory and is cleared when the API restarts; configure `DATABASE_URL` for persistent history.

**Security:** keep `WEB_API_KEY` on your website's backend. Do not put it in frontend JavaScript, HTML, mobile apps, or public source code. Your website backend should proxy browser requests to OpenCoder-Agent and attach the Bearer token server-side. For direct cross-origin access, set `WEB_CORS_ORIGINS` to a comma-separated allow-list of exact origins; this does not make it safe to expose the API key in a browser. Put the API behind HTTPS and a reverse proxy in production. The included session locks and no-PostgreSQL conversation cache are per process; use PostgreSQL for persistent shared history and a shared queue/lock if deploying multiple API workers.

Example website backend proxy using the same `aiohttp` dependency:

```python
import os
import aiohttp
from aiohttp import web

async def website_chat(request: web.Request) -> web.Response:
    payload = await request.json()
    timeout = aiohttp.ClientTimeout(total=180)
    headers = {
        "Authorization": f"Bearer {os.environ['WEB_API_KEY']}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            os.getenv("OPENCODER_API_URL", "http://127.0.0.1:8080") + "/api/chat",
            json=payload,
            headers=headers,
        ) as upstream:
            data = await upstream.json(content_type=None)
            return web.json_response(data, status=upstream.status)

# Register this handler on your existing website backend:
# app.router.add_post("/opencoder/chat", website_chat)
```

Your browser frontend calls only your own website route, never the private OpenCoder API key:

```javascript
const response = await fetch("/opencoder/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    message: "Help me build a landing page",
    session_id: crypto.randomUUID()
  })
});
const result = await response.json();
console.log(result.answer);
```

Keep and reuse `session_id` for the same website conversation. The proxy example assumes your existing website backend also validates its user/session and applies its normal CSRF and rate-limit protections.

Optional settings:

| Variable | Purpose |
|---|---|
| `WEB_API_KEY` | Required secret for `python main.py web` |
| `WEB_HOST` | Bind address (default `0.0.0.0`) |
| `WEB_PORT` | HTTP port (defaults to `PORT`, usually `8080`) |
| `WEB_CORS_ORIGINS` | Comma-separated exact browser origins; empty by default |

## Run the Discord bot

Set `DISCORD_BOT_TOKEN`. Optionally set `DISCORD_ALLOWED_USER_ID` to restrict bot access to one Discord user. If it is empty, the bot responds to users permitted by the Discord channel and bot permissions.

```bash
python main.py discord
```

For compatibility, running `python main.py` without a subcommand starts Discord mode.

## Docker

Create `.env`, configure the required API key and Discord token, then run:

```bash
docker compose up --build -d
```

The included Docker command starts Discord mode. To run the CLI in a one-off container, use `docker compose run --rm opencoder-agent python main.py cli`. To run the website API instead, use `docker compose run --rm --service-ports opencoder-agent python main.py web` after setting `WEB_API_KEY`.

## Configuration

| Variable | Required | Purpose |
|---|---:|---|
| `AI_API_KEY` or `AI_API_KEY_1` | Yes | First LLM provider key |
| `AI_MODEL` | No | Default model name |
| `AI_API_BASE_URL` | No | Custom OpenAI-compatible API URL |
| `DISCORD_BOT_TOKEN` | Discord only | Discord bot token |
| `DISCORD_ALLOWED_USER_ID` | No | Optional Discord user allowlist |
| `DATABASE_URL` | No | PostgreSQL persistence |
| `GITHUB_TOKEN` | No | GitHub tools |
| `VERCEL_TOKEN` | No | Vercel tools |
| `E2B_API_KEY` | No | Remote code sandbox |
| `AI_ROUTER_MODEL` | No | Optional fast model for route classification (defaults to `AI_MODEL`) |
| `AI_SEARCH_MODEL`, `AI_CODE_MODEL`, `AI_TEST_MODEL`, `AI_IMAGE_MODEL` | No | Optional model override for an individual specialist |
| `SUBAGENT_MAX_TOOL_ROUNDS` | No | Per-agent tool-call round limit (default `16`) |
| `WEB_API_KEY` | Web mode | Bearer-token secret for the website API |
| `WEB_CORS_ORIGINS` | No | Exact comma-separated browser origins allowed by the API |
| `WEB_HOST` / `WEB_PORT` | No | Website API bind host/port |
| `OPENCODER_WORKSPACE` | No | Workspace directory (default `./workspace`) |
| `VERIFY_CODE` | No | Enable code verification (default `1`) |
| `MAX_FIX_ATTEMPTS` | No | Maximum correction attempts (default `8`) |

See `.env.example` for the complete set of supported options. Start the website API with `python main.py web`.

## Multi-Agent Architecture

The Manager performs a short intent-classification call and routes the request to one specialist. The agents do not receive the full 300-capability catalog: each receives only its own tool schemas, reducing tool-definition tokens and keeping its context focused.

```text
User
  |
  v
Manager (fast intent router)
  |
  +--> Search Agent  (web_search, web_fetch)
  |
  +--> Code Agent    (file_read, file_write, file_append, file_create_zip)
  |
  +--> Test Agent    (sandbox_run_python, sandbox_run_shell, code_loop)
  |
  +--> Image Agent   (generate_image)
  |
  +--> General       (Manager answers directly, no tools)
  |
  v
Discord / CLI / Website API
```

For code-generation requests, the Manager can run a Code -> Test -> Repair cycle. The Code Agent writes to the workspace, the Test Agent invokes the existing `CodeLoop`, and verification feedback is sent back to the Code Agent for bounded repair attempts. A standalone test request is routed to the Test Agent; when the user explicitly asks to fix failures, the Manager forwards actionable test errors to the Code Agent and then verifies the repair. The Test Agent itself stays read/execute-only, matching its strict tool boundary.

### Tool boundaries

| Agent | Available tools |
|---|---|
| Search | `web_search`, `web_fetch` |
| Code | `file_read`, `file_write`, `file_append`, `file_create_zip` |
| Test | `sandbox_run_python`, `sandbox_run_shell`, `code_loop` |
| Image | `generate_image` |
| General | No tools |

The existing `loop_agent/agent.py` remains the shared execution backend for registered tools, workspace safety, attachments, and persistence. Entry points instantiate and call `Manager`; they no longer use the legacy monolithic `Agent.ask()` loop.

## Project layout

```text
OpenCoder-Agent/
├── bot/
│   ├── __init__.py
│   └── bot.py
├── loop_agent/
│   ├── __init__.py
│   ├── agent.py              # Shared tool/backend implementation
│   ├── manager.py            # Intent router and orchestration
│   ├── web_api.py            # Authenticated website HTTP API
│   ├── connector.py
│   ├── code_loop.py
│   ├── motor.py
│   └── subagents/
│       ├── __init__.py
│       ├── base.py
│       ├── search_agent.py
│       ├── code_agent.py
│       ├── test_agent.py
│       └── image_agent.py
├── tools/
│   ├── __init__.py
│   ├── file_tools.py
│   └── github_tools.py
├── search/
│   ├── __init__.py
│   └── search.py
├── sandbox/
│   ├── __init__.py
│   └── sandbox.py
├── tests/
│   ├── __init__.py
│   ├── test_code_loop.py
│   ├── test_file_tools.py
│   ├── test_multi_agent.py
│   └── test_web_api.py
├── main.py
├── config.py
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Tests

```bash
python -m tests.test_code_loop
python -m tests.test_file_tools
python -m unittest tests.test_multi_agent tests.test_web_api
python -m compileall -q .
```

External services are not contacted by the local unit tests. Real Discord, GitHub, PostgreSQL, and E2B usage requires valid credentials and network access.
