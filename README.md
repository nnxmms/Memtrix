<div align="center">

# 🧠 Memtrix

**A self-hosted, privacy-first personal AI agent with persistent memory and agentic tool use.**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Matrix](https://img.shields.io/badge/Matrix-Protocol-000000?logo=matrix&logoColor=white)](https://matrix.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-1A1A2E)](https://ollama.ai)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-Cloud%20LLM-6C5CE7)](https://openrouter.ai)
[![Version](https://img.shields.io/badge/version-1.8.0-brightgreen)](#)
[![License](https://img.shields.io/badge/license-Private-red)](#)

<br>

*Not a chatbot. An **agent** — it searches the web, executes commands, manages its own memory,<br>and evolves its personality over time based on your interactions.*

---

</div>

<br>

## ⚡ Quick Start

### Prerequisites

| Requirement | Purpose |
|:--|:--|
| [Docker & Docker Compose](https://docs.docker.com/get-docker/) | Runs all services |
| [Ollama](https://ollama.ai) **or** an [OpenRouter](https://openrouter.ai) API key | LLM inference |
| [Element Desktop](https://element.io/download) (or any Matrix client) | Chat interface |

### Setup

```bash
git clone https://github.com/your-user/memtrix.git && cd memtrix

./setup.sh       # Create directories, build image, start Conduit
./onboard.sh     # Interactive wizard — configure LLM, model, channel

docker compose up -d
```

Open Element → connect to `http://localhost:6167` → log in → invite `@memtrix:memtrix.local` to a room.

> [!NOTE]
> **Linux users:** If your user is not in the `docker` group, run scripts with `sudo`.
> Both scripts automatically fix file ownership so the container can read the config.

> [!NOTE]
> **First startup:** Memtrix downloads the embedding model (~100 MB) on first launch.
> This can take a couple of minutes depending on your network. Subsequent starts reuse `data/models/`.

<br>

---

<br>

## ✨ Highlights

<table>
<tr>
<td width="50%">

🏠 **Fully Self-Hosted**<br>
<sub>LLM, homeserver, search engine, vector DB — everything runs on your hardware.</sub>

</td>
<td width="50%">

🔌 **Multi-Provider**<br>
<sub>Local models via Ollama or 200+ cloud models via OpenRouter.</sub>

</td>
</tr>
<tr>
<td>

🛠️ **Agentic Tool System**<br>
<sub>Auto-discovered tools with an iterative reasoning loop. Drop in new tools as .py files.</sub>

</td>
<td>

🧠 **Persistent Memory**<br>
<sub>Daily journals with semantic search (RAG) powered by on-device embeddings.</sub>

</td>
</tr>
<tr>
<td>

👤 **Self-Aware Persona**<br>
<sub>Identity files that Memtrix reads, understands, and updates itself over time.</sub>

</td>
<td>

💬 **Per-Room Sessions**<br>
<sub>Each Matrix room maintains its own conversation context and history.</sub>

</td>
</tr>
<tr>
<td colspan="2" align="center">

🔒 **Security Hardened** — non-root, read-only filesystem, all capabilities dropped, secrets cleared from memory

</td>
</tr>
</table>

<br>

---

<br>

## 🏗️ Architecture

```
                          ┌──────────────────┐
                          │  Element Desktop │
                          │  (Matrix Client) │
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
|:--|:--|
| **Memtrix** | Python agent — orchestrates LLM calls, tool execution, memory, sessions |
| **Conduit** | Lightweight Matrix homeserver (local-only, no federation) |
| **SearXNG** | Privacy-respecting metasearch engine for web access |
| **ChromaDB** | Embedded vector database for semantic memory search |
| **Ollama** | Local LLM inference (runs separately) |
| **OpenRouter** | Cloud LLM gateway — OpenAI, Anthropic, Google, and more |

<br>

---

<br>

## 🛠️ Tools

Built-in tools are automatically discovered at startup:

| Tool | Description |
|:--|:--|
| `get_current_time` | Returns the current date and time |
| `read_core_file` | Reads a core persona file (BEHAVIOR, SOUL, USER, MEMORY) |
| `write_core_file` | Updates a core persona file (enforces read-before-write) |
| `read_memory_file` | Reads a daily memory journal (`memory/yyyy-mm-dd.md`) |
| `write_memory_file` | Updates a daily memory journal |
| `search_memory` | Semantic search across all daily memories via embeddings |
| `web_search` | Searches the web via local SearXNG instance |
| `fetch_url` | Fetches and extracts readable text from a URL |
| `read_file` | Reads a file from the workspace (text and PDF supported) |
| `create_file` | Creates or overwrites a text file in the workspace |
| `delete_file` | Permanently deletes a file from the workspace |
| `create_directory` | Creates a directory in the workspace |
| `delete_directory` | Permanently deletes a directory and its contents |
| `send_file` | Sends a file from the workspace to the user via Matrix |

> Write operations for persona and memory files are rejected unless the file was read first in the same request. This is enforced at the code level, not just in the prompt.

<details>
<summary><b>Adding a Custom Tool</b></summary>
<br>

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

</details>

<br>

---

<br>

## 🧠 Memory System

Memtrix has a two-tier memory architecture:

**Core Memory** (`MEMORY.md`) — A curated, compact summary of the most important long-term knowledge. Key facts, recurring themes, lasting context. Memtrix actively maintains and prunes this file. Think of it as the **brain**.

**Daily Journals** (`memory/yyyy-mm-dd.md`) — Chronological, append-only logs of each day's conversations:

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

Daily journals are embedded using a local model ([`nomic-embed-text-v1.5`](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) via `sentence-transformers`) and stored in ChromaDB. The model runs entirely on-device — no external API calls.

```
User: "Remember that cake recipe I told you about?"
  → search_memory("cake recipe")
  → Finds 2026-03-12.md (distance: 0.23)
  → read_memory_file("2026-03-12.md")
  → Returns the full context
```

<br>

---

<br>

## 👤 Persona System

Memtrix's identity is defined by markdown files in `workspace/`:

| File | Purpose |
|:--|:--|
| `AGENT.md` | System prompt template — wires everything together |
| `BEHAVIOR.md` | Communication style, tone, and habits |
| `SOUL.md` | Core values and personality |
| `USER.md` | Everything Memtrix knows about you |
| `MEMORY.md` | Distilled long-term memory |

These files are injected into the system prompt via placeholders (`{{BEHAVIOR}}`, `{{SOUL}}`, etc.) and are **live-editable by Memtrix itself**. When you tell it to behave differently or share personal details, it updates the appropriate file — with the system prompt rebuilt immediately after.

<br>

---

<br>

## 💬 Sessions & Commands

Each Matrix room gets its own independent conversation session. Multiple rooms = multiple contexts.

| Command | Action |
|:--|:--|
| `/clear` | Start a fresh session in the current room |
| `/verbose on\|off` | Toggle real-time tool execution notifications |
| `/reasoning on\|off` | Toggle display of model reasoning/thinking |
| `/help` | List available commands |

<br>

---

<br>

## ⚙️ Configuration

All configuration lives in `data/config.json`. Secrets are stored in `.env` and injected as environment variables — never in config.

<details>
<summary><b>Example config.json</b></summary>
<br>

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

</details>

<br>

---

<br>

## 🔒 Security

The container is hardened by default:

| Measure | Detail |
|:--|:--|
| Non-root user | Runs as `memtrix` (UID 1000) |
| Read-only filesystem | Immutable root via `read_only: true` |
| No capabilities | `cap_drop: ALL` |
| No privilege escalation | `no-new-privileges: true` |
| Minimal writable surface | Only `workspace/`, `data/`, and `/tmp` |
| No shell access | No `run_command` tool — the LLM cannot execute arbitrary commands |
| Secret management | Tokens in `.env`, resolved at startup, cleared from process env |
| File access control | Core files and memory files are protected by all file/directory tools |
| Path traversal protection | All file tools validate paths via `os.path.realpath()` |
| Web access | All traffic routes through local SearXNG — no direct outbound from the LLM |

<br>

---

<br>

## 📂 Project Structure

<details>
<summary><b>Expand file tree</b></summary>
<br>

```
Memtrix/
├── src/
│   ├── main.py                       # Entry point
│   ├── memtrix.py                    # Core — wires channels, providers, sessions
│   ├── orchestrator.py               # Agentic loop — LLM calls, tool execution
│   ├── session.py                    # Per-room conversation persistence
│   ├── commands.py                   # Slash command registry
│   ├── secrets.py                    # Secret resolution + sanitization
│   ├── memory_index.py               # ChromaDB + local embeddings (RAG)
│   ├── config.py                     # Config path constant
│   ├── onboarding.py                 # Interactive setup wizard (Rich TUI)
│   ├── channels/
│   │   ├── base.py                   # BaseChannel interface
│   │   ├── cli.py                    # CLI channel (stdin/stdout)
│   │   └── matrix.py                 # Matrix channel (nio + async bridge)
│   ├── providers/
│   │   ├── base.py                   # BaseProvider interface
│   │   ├── ollama.py                 # Ollama LLM provider
│   │   ├── openrouter.py             # OpenRouter LLM provider
│   │   └── utils.py                  # Dynamic provider discovery
│   ├── tools/
│   │   ├── base.py                   # BaseTool interface + read tracker
│   │   ├── utils.py                  # Dynamic tool discovery
│   │   ├── time_tool.py              # Current time
│   │   ├── core_file_tools.py        # Read/write persona files
│   │   ├── memory_file_tools.py      # Read/write daily journals
│   │   ├── search_memory_tool.py     # Semantic memory search
│   │   ├── web_search_tool.py        # Web search via SearXNG
│   │   ├── fetch_url_tool.py         # URL content extraction
│   │   ├── read_file_tool.py         # Read files (text + PDF extraction)
│   │   ├── create_file_tool.py       # Create/overwrite text files
│   │   ├── delete_file_tool.py       # Delete files
│   │   ├── create_directory_tool.py  # Create directories
│   │   ├── delete_directory_tool.py  # Delete directories
│   │   ├── send_file_tool.py         # Send files to user via Matrix
│   │   └── read_pdf_tool.py          # Extract text from PDF files
│   └── static/
│       ├── config.json               # Config template
│       ├── conduit.toml              # Conduit homeserver config
│       ├── searxng/                  # SearXNG settings
│       ├── AGENT.md                  # System prompt template
│       ├── BEHAVIOR.md               # Behavior defaults
│       ├── SOUL.md                   # Soul template
│       ├── USER.md                   # User profile template
│       └── MEMORY.md                 # Memory template
├── workspace/                        # Live persona files (mounted into container)
├── data/                             # Persistent data (config, sessions, vector index)
├── Dockerfile
├── docker-compose.yml
├── .env                              # Secrets (gitignored)
├── requirements.txt
├── setup.sh
├── onboard.sh
└── run.sh
```

</details>

<br>

---

<br>

## 🔌 Adding a Provider

<details>
<summary><b>Provider template</b></summary>
<br>

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

</details>

<br>

---

<div align="center">
<sub>Built with care. Runs on your hardware. Remembers what matters.</sub>
</div>

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
