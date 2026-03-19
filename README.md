<div align="center">

# Memtrix

**A self-hosted, privacy-first personal AI agent with persistent memory and agentic tool use.**

Built with Python · Powered by Ollama & OpenRouter · Communicates over Matrix · v1.5.0

---

</div>

Memtrix is a lightweight personal AI assistant that runs entirely on your own infrastructure. It connects to any Ollama-compatible LLM or cloud models via [OpenRouter](https://openrouter.ai), communicates through the [Matrix](https://matrix.org) protocol, and maintains long-term memory across conversations.

It's not a chatbot. It's an **agent** — it can search the web, browse pages, execute shell commands, manage its own memory, and evolve its personality over time based on your interactions.

## Highlights

- **Fully self-hosted**: LLM, homeserver, search engine, vector database — everything runs locally
- **Multi-provider**: Local models via Ollama or cloud models via OpenRouter
- **Agentic tool system**: auto-discovered tools with an iterative reasoning loop
- **Persistent memory**: daily journals with semantic search (RAG) powered by local embeddings
- **Self-aware persona**: core identity files that Memtrix reads, understands, and updates itself
- **Per-room sessions**: each Matrix room maintains its own conversation context
- **Security hardened**: non-root, read-only filesystem, all capabilities dropped

## Architecture

```
                          ┌──────────────────┐
                          │ Element Desktop  │
                          │ (Matrix Client)  │
                          └────────┬─────────┘
                                   │
┌──────────────────────────────────┼──────────────────────┐
│  Docker Compose                  │                      │
│                                  │                      │
│  ┌───────────┐    ┌──────────────┴──┐    ┌───────────┐  │
│  │  Memtrix  │◄──►│    Conduit      │    │  SearXNG  │  │
│  │  (Agent)  │    │ (Matrix Server) │    │ (Search)  │  │
│  └─────┬─────┘    └─────────────────┘    └─────▲─────┘  │
│        │                                       │        │
│        ├───────────────────────────────────────┘        │
│        │                                                │
│        ├──► ChromaDB (vector memory, embedded)          │
│        │                                                │
└────────┼────────────────────────────────────────────────┘
         │
         ▼
    Ollama (LLM)
    OpenRouter (cloud LLMs)
```

| Component | Role |
|-----------|------|
| Memtrix | Python agent — orchestrates LLM calls, tool execution, memory, sessions |
| Conduit | Lightweight Matrix homeserver (local-only, no federation) |
| SearXNG | Privacy-respecting metasearch engine for web access |
| ChromaDB | Embedded vector database for semantic memory search |
| Ollama | Local LLM inference (runs separately) |
| OpenRouter | Cloud LLM gateway — access models from OpenAI, Anthropic, Google, etc. |

## Tools

Memtrix ships with a set of built-in tools, automatically discovered at startup:

| Tool | Description |
|------|-------------|
| `get_current_time` | Returns the current date and time |
| `read_core_file` | Reads a core persona file (BEHAVIOR, SOUL, USER, MEMORY) |
| `write_core_file` | Updates a core persona file (enforces read-before-write) |
| `read_memory_file` | Reads a daily memory journal (`memory/yyyy-mm-dd.md`) |
| `write_memory_file` | Updates a daily memory journal (auto-indexes for RAG) |
| `search_memory` | Semantic search across all daily memories via embeddings |
| `web_search` | Searches the web via local SearXNG instance |
| `fetch_url` | Fetches and extracts readable text from a URL |
| `run_command` | Executes shell commands inside the sandboxed container |
| `send_file` | Sends a file from the workspace to the user via Matrix |

Tools follow a read-before-write pattern — write operations for persona and memory files are rejected unless the file was read first in the same request. This is enforced at the code level, not just in the prompt.

### Adding a Tool

Drop a new `.py` file in `src/tools/` that subclasses `BaseTool`:

```python
from src.tools.base import BaseTool

class MyTool(BaseTool):

    def __init__(self, workspace_dir: str) -> None:
        super().__init__(
            name="my_tool",
            description="Does something useful.",
            parameters={
                "type": "object",
                "properties": {
                    "input": {"type": "string", "description": "The input."}
                },
                "required": ["input"]
            }
        )

    def execute(self, **kwargs) -> str:
        return "result"
```

It's automatically discovered and available to the LLM on the next restart.

## Memory System

Memtrix has a two-tier memory architecture:

### Core Memory (`MEMORY.md`)

A curated, compact summary of the most important long-term knowledge — key facts, recurring themes, lasting context. Memtrix actively maintains and prunes this file. Think of it as the **brain**.

### Daily Journals (`memory/yyyy-mm-dd.md`)

Chronological, append-only logs of each day's conversations. Every journal follows a strict structure:

```markdown
# 2026-03-18

## Conversations
- Brief summaries of what was discussed.

## Learned
- New facts about the user.

## Decisions
- Agreements or directions decided.

## Tasks
- Things requested, completed, or pending.

## Notes
- Anything else worth remembering.
```

### Semantic Search (RAG)

Daily journals are automatically embedded using a local embedding model (`nomic-embed-text-v1.5` via `sentence-transformers`) and stored in ChromaDB. The model downloads once on first startup and runs entirely on-device. When Memtrix needs to recall something from the past, it performs a semantic search over all journals and retrieves the most relevant entries.

```
User: "Remember that cake recipe I told you about?"
  → search_memory("cake recipe")
  → Finds 2026-03-12.md (distance: 0.23)
  → read_memory_file("2026-03-12.md")
  → Returns the full context
```

## Persona System

Memtrix's identity is defined by markdown files in the `workspace/` directory:

| File | Purpose |
|------|---------|
| `AGENT.md` | System prompt template — wires everything together |
| `BEHAVIOR.md` | Communication style, tone, and habits |
| `SOUL.md` | Core values and personality |
| `USER.md` | Everything Memtrix knows about you |
| `MEMORY.md` | Distilled long-term memory |

These files are injected into the system prompt via placeholders (`{{BEHAVIOR}}`, `{{SOUL}}`, etc.) and are **live-editable by Memtrix itself**. When you tell Memtrix to behave differently or share personal details, it updates the appropriate file — with the system prompt rebuilt immediately after.

## Sessions

Each Matrix room gets its own independent conversation session, stored as a JSON file in `data/sessions/`. This means you can have multiple ongoing conversations with different contexts — like separate chat windows.

Slash commands:
- `/clear` — Start a fresh session in the current room
- `/verbose on|off` — Toggle real-time tool execution notifications
- `/reasoning on|off` — Toggle display of model reasoning/thinking
- `/help` — List available commands

## Quick Start

### Prerequisites

- Docker & Docker Compose
- An [Ollama](https://ollama.ai) instance with a chat model pulled (or an OpenRouter API key)
- [Element Desktop](https://element.io/download) (or any Matrix client)

### Setup

```bash
# Clone the repo
git clone https://github.com/your-user/memtrix.git && cd memtrix

# Run first-time setup (creates directories, builds image, starts Conduit)
./setup.sh

# Run the interactive onboarding wizard (configures LLM, model, channel)
./onboard.sh

# Start everything
docker compose up -d
```

Open Element, connect to `http://localhost:6167`, log in with the credentials from onboarding, and invite `@memtrix:memtrix.local` to a room.

## Configuration

All configuration lives in `data/config.json`. Secrets (access tokens, API keys) are stored in a `.env` file at the project root and injected as environment variables — never in config.json.

```json
{
    "main-agent": {
        "provider": "my-ollama",
        "model": "my-model",
        "channel": "matrix",
        "sessions": {},
        "verbose": false,
        "reasoning": false
    },
    "workspace-directory": "/home/memtrix/workspace",
    "providers": {
        "my-ollama": {
            "type": "ollama",
            "base_url": "http://your-ollama-host:11434"
        },
        "my-openrouter": {
            "type": "openrouter",
            "api_key": "$OPENROUTER_API_KEY"
        }
    },
    "models": {
        "my-model": {
            "provider": "my-ollama",
            "model": "llama3",
            "think": true
        }
    },
    "channels": {
        "matrix": {
            "type": "matrix",
            "homeserver": "http://conduit:6167",
            "user_id": "@memtrix:memtrix.local",
            "access_token": "$MATRIX_ACCESS_TOKEN"
        }
    }
}
```

Values starting with `$` are resolved from environment variables at startup (prefixed with `MEMTRIX_SECRET_`). For example, `$MATRIX_ACCESS_TOKEN` reads from `MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN` in `.env`.

## Project Structure

```
Memtrix/
├── src/
│   ├── main.py                    # Entry point
│   ├── memtrix.py                 # Core — wires channels, providers, sessions
│   ├── orchestrator.py            # Agentic loop — LLM calls, tool execution
│   ├── session.py                 # Per-room conversation persistence
│   ├── commands.py                # Slash command registry (/clear, /verbose, /reasoning, /help)
│   ├── secrets.py                 # Secret resolution from env vars + sanitization
│   ├── memory_index.py            # ChromaDB + local embeddings (RAG)
│   ├── config.py                  # Config path constant
│   ├── onboarding.py              # Interactive setup wizard (Rich TUI)
│   ├── channels/
│   │   ├── base.py                # BaseChannel interface
│   │   ├── cli.py                 # CLI channel (stdin/stdout)
│   │   └── matrix.py              # Matrix channel (nio + async bridge)
│   ├── providers/
│   │   ├── base.py                # BaseProvider interface
│   │   ├── ollama.py              # Ollama LLM provider
│   │   ├── openrouter.py          # OpenRouter LLM provider
│   │   └── utils.py               # Dynamic provider discovery
│   ├── tools/
│   │   ├── base.py                # BaseTool interface + read tracker
│   │   ├── utils.py               # Dynamic tool discovery
│   │   ├── time_tool.py           # Current time
│   │   ├── core_file_tools.py     # Read/write persona files
│   │   ├── memory_file_tools.py   # Read/write daily journals
│   │   ├── search_memory_tool.py  # Semantic memory search
│   │   ├── web_search_tool.py     # Web search via SearXNG
│   │   ├── fetch_url_tool.py      # URL content extraction
│   │   ├── run_command_tool.py    # Shell command execution
│   │   └── send_file_tool.py      # Send files to user via Matrix
│   └── static/
│       ├── config.json            # Config template
│       ├── conduit.toml           # Conduit homeserver config
│       ├── searxng/               # SearXNG settings
│       ├── AGENT.md               # System prompt template
│       ├── BEHAVIOR.md            # Behavior defaults
│       ├── SOUL.md                # Soul template
│       ├── USER.md                # User profile template
│       └── MEMORY.md              # Memory template
├── workspace/                     # Live persona files (mounted into container)
├── data/                          # Persistent data (config, sessions, vector index)
├── Dockerfile
├── docker-compose.yml
├── .env                           # Secrets (access tokens, API keys — gitignored)
├── requirements.txt
├── setup.sh
├── onboard.sh
└── run.sh
```

## Security

The Memtrix container is hardened by default:

| Measure | Detail |
|---------|--------|
| Non-root user | Runs as `memtrix` (UID 1000) |
| Read-only filesystem | Immutable root via `read_only: true` |
| No capabilities | `cap_drop: ALL` |
| No privilege escalation | `no-new-privileges: true` |
| Minimal writable surface | Only `workspace/`, `data/`, and `/tmp` |
| Tool sandboxing | `run_command` is confined to the container's restrictions |
| Secret management | Tokens stored in `.env`, resolved at startup, cleared from process environment |
| Subprocess isolation | `run_command` passes a sanitized env with all secrets stripped |
| File access control | Core file and memory tools are limited to whitelisted paths |
| Web access | All web traffic routes through local SearXNG — no direct outbound from the LLM |

## Adding a Provider

Drop a new `.py` file in `src/providers/` that subclasses `BaseProvider`:

```python
from src.providers.base import BaseProvider

class MyProvider(BaseProvider):

    def __init__(self, api_key: str) -> None:
        super().__init__(name="myprovider")
        self._api_key = api_key

    def completions(self, model, history, tools=None):
        # Call your LLM API and return a message object
        ...
```

The onboarding wizard automatically discovers new providers and prompts for their constructor parameters.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| LLM Backend | Ollama, OpenRouter |
| Embeddings | nomic-embed-text-v1.5 (local, via sentence-transformers) |
| Vector Store | ChromaDB (embedded, persistent) |
| Communication | Matrix protocol (matrix-nio) |
| Homeserver | Conduit |
| Web Search | SearXNG |
| HTML Parsing | BeautifulSoup4 |
| Container | Docker (security-hardened) |
| TUI | Rich (onboarding wizard) |

## Versioning

Memtrix follows [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`.

| Bump | When | Example |
|------|------|---------|
| **PATCH** | Bug fixes, small tweaks | Fixing a tool error, adjusting prompt wording |
| **MINOR** | New features, backward compatible | Adding a new tool, new channel, new slash command |
| **MAJOR** | Breaking changes | Config format redesign, architecture overhaul |

The version lives in `src/__init__.py` and is printed on startup.

## License

MIT
