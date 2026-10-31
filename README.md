# OpenCoder-Agent

OpenCoder-Agent is a modular Python AI agent with two entry points: a Discord bot and an interactive terminal CLI. It keeps the original agent, provider fallback, tool catalog, file operations, web search, code verification loop, and optional E2B execution while keeping the project structure small.

## Features

- Discord bot and interactive CLI
- OpenAI-compatible provider connections with fallback across configured API keys
- Workspace-scoped file tools and a 300-capability tool catalog
- DuckDuckGo search through DDGS
- Optional GitHub, Vercel, image generation, and E2B integrations
- Optional PostgreSQL conversation persistence
- Automatic code verification and bounded correction attempts

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

The included Docker command starts Discord mode. To run the CLI in a one-off container, use `docker compose run --rm opencoder-agent python main.py cli`.

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
| `OPENCODER_WORKSPACE` | No | Workspace directory (default `./workspace`) |
| `VERIFY_CODE` | No | Enable code verification (default `1`) |
| `MAX_FIX_ATTEMPTS` | No | Maximum correction attempts (default `8`) |

See `.env.example` for the complete set of supported options.

## Project layout

```text
OpenCoder-Agent/
├── bot/
│   ├── __init__.py
│   └── bot.py
├── loop_agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── connector.py
│   ├── code_loop.py
│   └── motor.py
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
│   └── test_file_tools.py
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
python -m compileall -q .
```

External services are not contacted by the local unit tests. Real Discord, GitHub, PostgreSQL, and E2B usage requires valid credentials and network access.
