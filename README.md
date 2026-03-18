# Memtrix

A lightweight, self-hosted personal AI agent that lives in a Docker container and talks to you over [Matrix](https://matrix.org).

Memtrix connects to any Ollama-compatible LLM backend and runs alongside a local [Conduit](https://conduit.rs) homeserver — no cloud services required.

## Architecture

```
┌──────────────────────────────────────────────┐
│  Docker Compose                              │
│                                              │
│  ┌──────────┐         ┌──────────────────┐   │
│  │ Memtrix  │ ◄─────► │ Conduit (Matrix) │ ◄─── Element Desktop
│  └────┬─────┘         └──────────────────┘   │
│       │                                      │
└───────┼──────────────────────────────────────┘
        │
        ▼
   Ollama / LLM
```

**Memtrix** — Python bot that receives messages, queries an LLM, and replies.
**Conduit** — Lightweight Matrix homeserver (local-only, no federation).
**Ollama** — LLM inference server providing the model (runs separately).

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/your-user/memtrix.git
cd memtrix

# 2. Run the setup script (creates dirs, builds image, starts Conduit)
./setup.sh

# 3. Run the onboarding wizard (configures provider, model, channel)
./onboard.sh

# 4. Start Memtrix
docker compose up -d
```

Then open [Element Desktop](https://element.io/download), log in to `http://localhost:6167` with the credentials the wizard gave you, and start a DM with `@memtrix:memtrix.local`.

## Project Structure

```
Memtrix/
├── src/
│   ├── main.py              # Entry point — validates config, starts Memtrix
│   ├── memtrix.py           # Core class — wires channel + provider
│   ├── config.py            # Config path constant
│   ├── onboarding.py        # Interactive setup wizard (Rich TUI)
│   ├── channels/
│   │   ├── base.py          # BaseChannel interface
│   │   ├── cli.py           # CLI channel (stdin/stdout)
│   │   └── matrix.py        # Matrix channel (nio + Conduit)
│   ├── providers/
│   │   ├── base.py          # BaseProvider interface
│   │   ├── ollama.py        # Ollama LLM provider
│   │   └── utils.py         # Dynamic provider discovery
│   └── static/
│       ├── config.json      # Config template
│       ├── conduit.toml     # Conduit homeserver config
│       ├── AGENT.md         # Agent persona template
│       ├── MEMORY.md        # Long-term memory template
│       ├── SOUL.md          # Core values template
│       └── USER.md          # User profile template
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── setup.sh                 # First-time setup
├── onboard.sh               # Onboarding wizard launcher
└── run.sh                   # Interactive run helper
```

## Configuration

All configuration lives in `data/config.json`, created by the onboarding wizard:

```json
{
    "main-agent": {
        "provider": "my-ollama",
        "model": "my-model",
        "channel": "matrix"
    },
    "providers": {
        "my-ollama": {
            "type": "ollama",
            "base_url": "http://your-ollama-host:11434"
        }
    },
    "models": {
        "my-model": {
            "provider": "my-ollama",
            "model": "llama3"
        }
    },
    "channels": {
        "matrix": {
            "type": "matrix",
            "homeserver": "http://conduit:6167",
            "user_id": "@memtrix:memtrix.local",
            "access_token": "..."
        }
    }
}
```

## Workspace

The `workspace/` directory is mounted into the container and holds markdown files that shape the agent's personality:

| File | Purpose |
|------|---------|
| `AGENT.md` | Agent persona and instructions |
| `SOUL.md` | Core values and personality |
| `USER.md` | Information about the user |
| `MEMORY.md` | Summarized long-term memory |
| `memory/` | Granular memory entries |

## Adding a Provider

Drop a new `.py` file in `src/providers/` that subclasses `BaseProvider`:

```python
from src.providers.base import BaseProvider

class MyProvider(BaseProvider):

    def __init__(self, api_key: str) -> None:
        super().__init__(name="myprovider")
        self._api_key: str = api_key

    def completions(self, model: str, history: list[dict[str, str]]) -> str:
        # Call your LLM and return the response text
        ...
```

The onboarding wizard will automatically discover it and prompt for its `__init__` parameters.

## Security

The Memtrix container runs with:

- **Non-root user** (`memtrix`, UID 1000)
- **Read-only root filesystem**
- **All capabilities dropped** (`cap_drop: ALL`)
- **No privilege escalation** (`no-new-privileges`)
- **Minimal writable surface** (only `/tmp`, `workspace/`, `data/`)
