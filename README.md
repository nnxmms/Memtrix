<div align="center">

# 🧠 Memtrix

**A self-hosted, privacy-first personal AI agent with persistent memory and agentic tool use.**

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://python.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Matrix](https://img.shields.io/badge/Matrix-Protocol-000000?logo=matrix&logoColor=white)](https://matrix.org)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-1A1A2E)](https://ollama.ai)
[![OpenRouter](https://img.shields.io/badge/OpenRouter-Cloud%20LLM-6C5CE7)](https://openrouter.ai)
[![Version](https://img.shields.io/badge/version-2.25.0-brightgreen)](#)
[![License](https://img.shields.io/badge/license-Private-red)](#)

[Website](https://memtrix.me) · [Documentation](https://memtrix.me/docs.html) · [GitHub](https://github.com/nnxmms/Memtrix)

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

> [!NOTE]
> **First startup:** Memtrix downloads the embedding model (~100 MB) on first launch.
> This can take a couple of minutes depending on your network. The agent stays responsive
> while the model loads in the background, and indexing runs off the startup path.
> Subsequent starts reuse `data/models/`.

```bash
git clone https://github.com/your-user/memtrix.git && cd memtrix

./setup.sh       # Create directories, build image, start Conduit
./onboard.sh     # Interactive wizard — configure LLM, model, channel

docker compose up -d
```

Open Element → connect to `http://localhost:6167` → log in → invite `@memtrix:memtrix.local` to a room.

> [!TIP]
> **Local or external homeserver:** During `./onboard.sh` you can choose the bundled local
> Conduit homeserver (recommended — accounts are created for you) or an external/already-hosted
> Matrix server (e.g. your own Synapse or matrix.org). For external servers you provide the
> homeserver URL, the bot's user ID, and an access token. When using an external homeserver the
> bundled Conduit container stays off, and sub-agents are created by pre-registering a Matrix
> account for each and supplying its token.

> [!NOTE]
> **Linux users:** If your user is not in the `docker` group, run scripts with `sudo`.
> Both scripts automatically fix file ownership so the container can read the config.

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
<sub>Local models via Ollama, 200+ cloud models via OpenRouter, or any OpenAI-compatible endpoint.</sub>

</td>
</tr>
<tr>
<td>

🛠️ **Agentic Tool System**<br>
<sub>Auto-discovered tools with an iterative reasoning loop. Drop in new tools as .py files.</sub>

</td>
<td>

🧠 **Persistent Memory**<br>
<sub>Searchable conversation history with semantic search (RAG) powered by on-device embeddings.</sub>

</td>
</tr>
<tr>
<td>

👁️ **Vision**<br>
<sub>Flip on a model's <code>vision</code> flag and Memtrix sees the images you send — across Ollama, OpenRouter, and OpenAI-compatible endpoints.</sub>

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
<td>

🤖 **Sub-Agents**<br>
<sub>Create specialist agents with their own identity, memory, and Matrix presence.</sub>

</td>
<td>

🔒 **Security Hardened**<br>
<sub>Non-root, read-only filesystem, all capabilities dropped, isolated workspaces.</sub>

</td>
</tr>
<tr>
<td>

🖥️ **Web Control Panel**<br>
<sub>Configure everything from the browser — validated edits, live connection tests, safe restarts, secrets & full memory administration.</sub>

</td>
<td>

🧩 **Shared Vector Store**<br>
<sub>Reasoning memory runs as a dedicated ChromaDB service shared safely by the agent and the web panel.</sub>

</td>
</tr>
<tr>
<td>

📚 **Self-Documenting**<br>
<sub>Researches its own documentation — ask it how Memtrix works and it answers from the bundled docs, with sources.</sub>

</td>
<td>

🔍 **Private Web Search**<br>
<sub>Searches the web through a local SearXNG instance — no queries leave your network unfiltered.</sub>

</td>
</tr>
</table>

<br>

## 🔧 Architecture

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
│        ├───> Sub-Agents (background threads)   │        │
│        │     Each with own Matrix user         │        │
│        │                                       │        │
│        ├───────────────────────────────────────┘        │
│        │                                                │
│        ├──► ChromaDB (vector memory, per-agent)         │
│        │                                                │
└────────┼────────────────────────────────────────────────┘
         │
         ▼
    Ollama (LLM)
    OpenRouter (cloud LLMs)
    OpenAI-compatible (llama.cpp, vLLM, LM Studio, OpenAI, ...)
```

| Component | Role |
|:--|:--|
| **Memtrix** | Python agent — orchestrates LLM calls, tool execution, memory, sessions, sub-agents |
| **Conduit** | Lightweight Matrix homeserver, bundled for local use (no federation). Optional — you can point Memtrix at any external Matrix homeserver instead |
| **SearXNG** | Privacy-respecting metasearch engine for web access |
| **ChromaDB** | Embedded vector database for semantic memory search |
| **Ollama** | Local LLM inference (runs separately) |
| **OpenRouter** | Cloud LLM gateway — OpenAI, Anthropic, Google, and more |
| **OpenAI-compatible** | Any endpoint speaking the OpenAI chat-completions API — llama.cpp, vLLM, LM Studio, an OpenAI-shim, a self-hosted gateway, or OpenAI itself (optional API key) |

<br>

## 🛠️ Tools

Built-in tools are automatically discovered at startup:

| Tool | Description |
|:--|:--|
| `get_current_time` | Returns the current date and time |
| `read_core_file` | Reads a core persona file (BEHAVIOR, SOUL, USER, MEMORY) |
| `write_core_file` | Updates a writable persona file (BEHAVIOR, SOUL only; enforces read-before-write) |
| `search_memory` | Recall past conversations by meaning (`query`) and/or by date (`date`, or `start_date`+`end_date`) |
| `memory_profile` | Returns the compact profile cards for the user and the agent (no LLM) |
| `memory_search` | Semantic search over reasoned conclusions about the user and agent |
| `memory_context` | Synthesizes a natural-language answer from reasoned memory |
| `memory_conclude` | Stores a single high-signal durable fact immediately |
| `search_docs` | Searches the Memtrix documentation and returns matching sections with citations (no LLM) |
| `ask_docs` | Synthesizes a grounded answer about how Memtrix works from its own documentation |
| `web_search` | Searches the web via local SearXNG instance |
| `fetch_url` | Fetches and extracts readable text from a URL |
| `read_file` | Reads a file from the workspace (text and PDF supported) |
| `create_file` | Creates or overwrites a text file in the workspace |
| `delete_file` | Permanently deletes a file from the workspace |
| `create_directory` | Creates a directory in the workspace |
| `list_directory` | Lists the contents of a directory in the workspace |
| `delete_directory` | Permanently deletes a directory and its contents |
| `git_clone` | Clones a public git repository into the workspace |
| `git_manage` | Configures the git identity (name/email) and runs commits and pushes on workspace repos (push asks for confirmation) |
| `download_file` | Downloads a file from a URL into the workspace |
| `send_file` | Sends a file from the workspace to the user via Matrix |
| `react_to_message` | Reacts to the user's message with an emoji in Matrix |
| `create_agent` | Creates a new specialist sub-agent with its own Matrix identity and workspace |
| `list_agents` | Lists all registered sub-agents and their status |
| `delete_agent` | Permanently deletes a sub-agent and all its data |
| `ask_agent` | Asks another agent a question and returns their response |
| `ssh_gen_key` | Generates Memtrix's own ed25519 SSH key (returns the public key) |
| `ssh_get_pub_key` | Returns the SSH public key to install in a host's `authorized_keys` |
| `ssh_add_host` | Registers a remote host under a short alias |
| `ssh_remove_host` | Unregisters a remote host alias |
| `ssh_get_remote_hosts` | Lists registered hosts and their connection status |
| `ssh_connect` | Opens a persistent interactive SSH session to a host (trust-on-first-use host key) |
| `ssh_run` | Runs a command in the open session — state persists between calls; optional `sudo` |
| `ssh_scp` | Copies a single file to or from a connected host over SFTP (`upload` from / `download` into the workspace; max 100 MB) |
| `ssh_disconnect` | Closes an open SSH session |
| `skill_manage` | Creates, views, lists, edits, patches, or deletes the agent's own reusable skills |

> Write operations for persona and memory files are rejected unless the file was read first in the same request. This is enforced at the code level, not just in the prompt. `USER.md` and `MEMORY.md` are profile cards owned by the reasoning memory and cannot be written by the agent at all.

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

## 🧠 Memory System

Memtrix combines a searchable conversation history with a reasoning layer:

**Reasoning Memory** — A background **deriver** thread continuously reasons over each conversation and distills durable conclusions about both the user and the agent itself: explicit observations, certain deductions, and observed patterns. Each conclusion carries a **confidence** (high/medium/low) that ranks how it surfaces and how the profile cards are curated; when the same conclusion is independently re-derived it is promoted rather than merely re-counted, so reinforced facts rise. Conclusions are vector-indexed locally (ChromaDB, `data/representations`) and the **most relevant** ones — filtered by a similarity threshold so off-topic memories are never injected — are added to the prompt before each reply, so Memtrix recalls durable facts across sessions. Inspired by [Honcho](https://honcho.dev), but implemented entirely on-device — no external service.

**Memory Consolidation** — Once a day, a background pass distills each peer's accumulated conclusions into a smaller, higher-signal set — like memory consolidation during sleep. It merges duplicates, explicitly resolves contradictions (keeping the more-reinforced or more-recent fact), synthesizes patterns from related items, and gently **decays** weak memories — derived conclusions that are stale, never reinforced, and low confidence are pruned, while reinforced, high-confidence, and manually-saved facts persist. Conclusions you add manually are preserved untouched. The schedule persists across restarts; run `/consolidate` to trigger a pass on demand.

**Profile Cards** (`USER.md` about you, `MEMORY.md` about the agent) — Compact, always-current cards that the deriver curates automatically and keeps within a character budget. They are injected into every system prompt and are no longer hand-edited by the agent.

**Conversation Memory** — Every conversation is automatically saved as a raw session transcript and embedded into the vector store in the background. The agent writes no journals itself; instead it recalls its history with the `search_memory` tool, which works two ways and can combine them: **by meaning** (a `query` like a tool, project, decision, or name discussed weeks ago) and **by date** (`date` for one day, or `start_date`+`end_date` for a period). Because a date can't be matched semantically, date/range questions ("what did we talk about on the 15th?", "anything from last week?") filter on each chunk's day metadata instead of embedding distance — and the agent is told today's date so it can resolve "yesterday" or "last Wednesday" to an ISO date on its own. Inter-agent and internal sessions are skipped, and each sub-agent indexes only its own conversations.

> [!NOTE]
> **Incremental indexing:** session transcripts are split into windowed chunks and
> embedded into the vector store in the background. A content-hash cache
> (`.chunk-hashes.json`, stored alongside the index) persists across restarts. On
> reboot Memtrix re-embeds only chunks that are new or changed and prunes entries
> for deleted sessions, so growing conversations only embed their newest chunks and
> warm starts skip re-embedding unchanged history entirely.

### Reasoning Memory Tools

When `recall_mode` is `tools` or `hybrid`, Memtrix can query its reasoning memory directly:

- `memory_profile` — read the compact profile cards (fast, no LLM).
- `memory_search` — semantically search reasoned conclusions for ranked excerpts.
- `memory_context` — ask a natural-language question and get a synthesized answer grounded in memory.
- `memory_conclude` — permanently lock a single high-signal durable fact (high confidence, never pruned or rewritten by consolidation).

The reasoning memory is configured via the optional `memory` section in `config.json`:

| Key | Default | Description |
|:--|:--|:--|
| `backend` | `native` | Memory backend (`native` only for now) |
| `recall_mode` | `hybrid` | `hybrid` (inject + tools), `context` (inject only), `tools` (tools only), or `off` |
| `write_frequency` | `async` | When the deriver flushes: `async`, `turn`, `session`, or a token count |
| `reasoning_level` | `low` | Reasoning depth: `minimal`, `low`, `medium`, `high`, `max` |
| `reasoning_model` | `null` | Optional model override for reasoning (must share the main provider) |
| `batch_tokens` | `1000` | Approx. tokens accumulated before a background reasoning pass |
| `peer_card_max_chars` | `1500` | Hard character budget for each profile card, enforced with boundary-safe trimming (no mid-bullet cutoffs) |
| `dual_peer` | `true` | Model both the user and the agent (vs. user only) |
| `inject_top_k` | `5` | How many conclusions to inject into the prompt per turn |

The section is optional — omit it and Memtrix runs on these defaults.

### Matrix Voice Messages (Local STT)

Memtrix can transcribe Matrix voice notes (`m.audio`) locally on-device before passing them into the normal agent loop. Audio files are downloaded to `attachments/`, transcribed with a local Whisper backend, and injected as user text context.

Configure via the optional `voice` section in `config.json`:

| Key | Default | Description |
|:--|:--|:--|
| `enabled` | `false` | Enable local transcription for Matrix audio messages |
| `provider` | `local` | STT backend (`local` only for now) |
| `model` | `base` | Local model tier used by STT |
| `language` | `null` | Optional language hint; auto-detect when unset |
| `max_audio_bytes` | `25000000` | Maximum accepted audio file size |
| `timeout_seconds` | `180` | Max transcription time before graceful timeout |

When disabled, voice messages are handled as regular file attachments.

### Semantic Search (RAG)

Conversation transcripts are embedded using a local model ([`nomic-embed-text-v1.5`](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) via `sentence-transformers`) and stored in ChromaDB. The model runs entirely on-device — no external API calls. Recall can be semantic, date-scoped, or both.

```
User: "Remember that cake recipe I told you about?"
  → search_memory(query="cake recipe")
  → Finds a conversation from 2026-03-12 (distance: 0.23)
  → Returns the transcript excerpt where you discussed the recipe

User: "What did we talk about on the 15th?"
  → search_memory(date="2026-06-15")          # resolved from "the 15th" + today's date
  → Returns that day's conversation, in order — no query needed
```

<br>

## � SSH Remote Administration

Memtrix can act as a sysadmin over SSH, working on remote hosts through a **persistent interactive session** — it opens a connection, works inside it across many commands, then closes it. Because the shell stays open, state carries over between commands: `cd /etc` in one step is still in effect on the next, exactly like a human at a terminal.

```
You:     Set up the new Raspberry Pi at 192.168.1.50, user 'pi'.
Memtrix: → ssh_gen_key            (creates its ed25519 key, shows the public key)
You:     (install that key in the Pi's ~/.ssh/authorized_keys)
Memtrix: → ssh_add_host(alias="pi", hostname="192.168.1.50", username="pi")
         → ssh_connect("pi")      (asks you to trust the host key on first contact)
         → ssh_run("cd /etc/apt && ls")
         → ssh_run("apt update", sudo=true)   (asks for the sudo password once)
         → ssh_scp("pi", direction="download", remote_path="/var/log/syslog")
         → ssh_disconnect("pi")
```

**How it works**

- **Its own key** — `ssh_gen_key` creates an ed25519 keypair stored on the data volume (private key `0600`, never disclosed). Install the public key (`ssh_get_pub_key`) in each host's `authorized_keys`. Authentication is key-only; Memtrix never uses a login password.
- **Host registry** — `ssh_add_host` / `ssh_remove_host` / `ssh_get_remote_hosts` manage named hosts in `data/ssh/hosts.json`.
- **Persistent session** — `ssh_connect` opens a shell that subsequent `ssh_run` calls reuse; `ssh_disconnect` closes it. Sessions are also closed on shutdown.
- **File transfer** — `ssh_scp` copies a single file over SFTP in either direction: `upload` sends a workspace file to the host, `download` pulls a remote file into the workspace (defaulting to `downloads/`). Transfers are capped at 100 MB and confirmed with you first.
- **sudo** — pass `sudo=true` to `ssh_run`. Memtrix asks you for the sudo password, keeps it **in memory only** for the session (never written to disk), and feeds it to `sudo -S`.

**Safety**

- **Trust-on-first-use host keys** — on the first connection Memtrix shows the host-key fingerprint and asks you to confirm; the key is pinned in `data/ssh/known_hosts` and verified strictly thereafter.
- **Destructive-command confirmation** — commands like `rm`, `dd`, `mkfs`, `shutdown`/`reboot`, recursive `chmod`/`chown`, and writes to block devices require your explicit approval before running.
- **No internal targets** — SSH to Memtrix's own Docker services and to loopback/link-local addresses is refused. Private LAN hosts are allowed (that's the point).

SSH administration is enabled by default and configured via the optional `ssh` section in `config.json`:

| Key | Default | Description |
|:--|:--|:--|
| `enabled` | `true` | Load the SSH tools. Set to `false` to remove the capability entirely. |
| `connect_timeout` | `15` | Seconds to wait when opening a connection. |
| `command_timeout` | `120` | Seconds to wait for a single command to finish. |
| `max_output_chars` | `20000` | Cap on command output returned to the model. |

The SSH tools are available to the main agent only; sub-agents do not get them.

<br>

## 🧠 Skills

Memtrix can build its own **skills** — short, reusable task workflows it writes for itself so it handles recurring kinds of work better over time. A skill is a generalized set of steps for a kind of task, stored in the agent's workspace as `skills/<name>/SKILL.md`. Skills are a layer above memory: SOUL.md/BEHAVIOR.md capture *who* the agent is, memory captures *what* it knows, and skills capture *how* it gets recurring tasks done.

```
You:     Run a security audit on the Pi.
Memtrix: → (works through it across several steps)
         → skill_manage(action="create", name="security-audit",
                        description="When auditing a Linux host's security, follow these steps",
                        instructions="1. Check open ports …\n2. Review sudoers …\n3. …")

  (next week)
You:     Can you do a security check on my new server?
Memtrix: 🧠 (spots the matching skill in its catalog)
         → skill_manage(action="view", name="security-audit")   (loads the steps)
         → (follows the workflow)
```

**How it works**

- **Self-authored, no second model** — authoring happens inside the normal agent loop. After finishing a task, the agent evaluates whether it was skill-worthy (5+ tool calls, error recovery, a user correction, or a non-obvious workflow) and, if so, captures the approach silently. The same `skill_manage` tool drives `create`, `view`, `list`, `edit`, `patch`, and `delete`.
- **Progressive disclosure** — at the start of every turn the agent sees a catalog of all its skills (each as `name: description`) and decides for itself which, if any, fits the task. It then loads that skill's full instructions on demand and follows them. There is no embedding step or vector index; the model does the matching, the same way the Agent Skills standard works.
- **Instructions only** — skills contain instructions and reference files, not executable code. The agent carries out the steps with its normal tools (including SSH), preserving Memtrix's no-local-shell security model.
- **Per-agent isolation** — the main agent and every sub-agent keep their own separate skill store under their workspace.

Skills are enabled by default and configured via the optional `skills` section in `config.json`:

| Key | Default | Description |
|:--|:--|:--|
| `enabled` | `true` | Load the `skill_manage` tool and inject the skill catalog. Set to `false` to remove the capability entirely. |

<br>

## ⚙️ Agent Loop

Each user request is handled by an iterative tool-calling loop: the agent calls the model, runs any requested tools, feeds the results back, and repeats until the model returns a final answer with no further tool calls. A safety cap limits how many of these rounds a single request may take; if it's reached, the agent is asked for a final answer with tools disabled. Raising the cap lets the agent work through longer, multi-step tasks without being cut off.

The loop is configured via the optional `agent` section in `config.json`:

| Key | Default | Description |
|:--|:--|:--|
| `max_iterations` | `25` | Maximum tool-call rounds per request before the agent is forced to produce a final answer. |
| `max_history` | `60` | Maximum messages kept in a session before the oldest turns are trimmed (the system prompt is always preserved). |

The loop is built for reliability: provider calls retry with exponential backoff on transient errors, malformed tool-call arguments are tolerated and surfaced to the model as a correctable error instead of crashing the request, tool arguments are validated against each tool's schema before execution, and independent read-only tool calls in a batch run concurrently. Sessions are bounded by `max_history` so long conversations cannot overflow the context window, and the system prompt is rebuilt mid-session when the background memory re-curates `USER.md` / `MEMORY.md` so card updates take effect immediately.

<br>

## �🖥️ Web Control Panel

A production-ready browser UI for configuring everything Memtrix offers, served by a dedicated, hardened FastAPI container with a React/TypeScript single-page app. It runs alongside the agent and shares the same `config.json` and memory store.

```bash
docker compose up -d            # starts the agent, the web panel, and the chroma service
open http://127.0.0.1:8800      # the control panel (localhost only by default)
```

| Capability | What you can do |
|:--|:--|
| **Configuration** | Edit the main agent, providers, models, channels, sub-agents, and memory settings. Every change is validated server-side before it touches `config.json` — malformed configs are rejected with field-level errors and never saved. |
| **Connection tests** | Live-test provider and channel credentials before saving. `$PLACEHOLDER` secrets are resolved automatically for the test. |
| **Apply & Restart** | Validate and restart the agent with one click. The restart is requested via a sentinel file watched by a supervisor entrypoint (no Docker socket), and progress streams to the UI over Server-Sent Events. |
| **Secrets** | View (decrypted, masked with reveal) and change secrets for both the local `.env` backend and Bitwarden Secrets Manager. |
| **Memory admin** | Browse per-peer conclusions with semantic search, edit/delete records, add manual ones, wipe a peer, edit & freeze peer cards, pause/resume background reasoning, and export/import the whole store as JSON. |

### Architecture

The reasoning-memory store runs as a separate **`chroma`** service that both the agent and the web panel connect to via `chromadb.HttpClient` (configured by `CHROMA_URL`). This eliminates SQLite single-writer corruption when both processes read and write concurrently; writes are additionally coordinated with file locks.

### Security

The web container drops all Linux capabilities, runs read-only and non-root with `no-new-privileges`, and binds only to `127.0.0.1`. Place it behind a reverse proxy for authentication and TLS. An optional shared-secret header gates the API directly when you set `MEMTRIX_WEB_TOKEN` (enter the same value under **Panel Settings** in the UI).

| Variable | Default | Purpose |
|:--|:--|:--|
| `MEMTRIX_WEB_HOST` | `0.0.0.0` | Bind address inside the container (published only to localhost) |
| `MEMTRIX_WEB_PORT` | `8800` | Port the panel listens on |
| `MEMTRIX_WEB_TOKEN` | _(unset)_ | Optional shared-secret required in the `X-Memtrix-Token` header |
| `CHROMA_URL` | `http://chroma:8000` | Shared ChromaDB endpoint for the reasoning store |

<br>

| `MEMORY.md` | Compact profile card about the agent (auto-maintained by reasoning memory) |

These files are injected into the system prompt via placeholders (`{{BEHAVIOR}}`, `{{SOUL}}`, etc.). `BEHAVIOR.md` and `SOUL.md` are **live-editable by Memtrix itself** — when you tell it to behave differently or reshape who it is, it updates the appropriate file and the system prompt is rebuilt immediately. `USER.md` and `MEMORY.md` are curated automatically by the reasoning memory and are **write-protected** — `write_core_file` rejects edits to them at the code level.

<br>

## 🤖 Sub-Agents

Memtrix can create specialist sub-agents — fully independent agents with their own Matrix identity, workspace, memory, and persona. Each sub-agent runs as a background thread with its own orchestrator and conversation sessions.

### What Sub-Agents Get

| Feature | Details |
|:--|:--|
| **Matrix user** | A separate bot account (e.g. `@dennis:memtrix.local`) the user can invite to any room |
| **Isolated workspace** | Own directory under `agents/<name>/` with core files, memory, attachments, downloads |
| **Own memory** | Separate searchable conversation history and ChromaDB vector index for semantic search |
| **Inherited behavior** | Copies the main agent's `BEHAVIOR.md` and symlinks `USER.md` (shared across all agents) |
| **Customized persona** | `SOUL.md` and `AGENT.md` are tailored to the sub-agent's name and expertise |
| **Full tool access** | All tools except agent management (`create_agent`, `list_agents`, `delete_agent`) |

### Inter-Agent Communication

Agents can consult each other using the `ask_agent` tool. The main agent can ask sub-agents, sub-agents can ask the main agent, and sub-agents can ask other sub-agents.

- Messages use `[Channel: Internal, Sender: <name>]` headers
- Each agent pair gets a dedicated internal session (separate from user-facing history)
- Depth is limited to 2 hops to prevent infinite recursion
- Target agents are locked during queries (5s timeout) to prevent deadlocks
- Notifications and human-in-the-loop confirmations are disabled during internal calls

### Example Workflow

**1. Ask Memtrix to create a specialist:**

> **You:** Create me a cooking expert. Call him Dennis.
>
> **Memtrix:** ⚠️ Create a new sub-agent?
>
> &nbsp;&nbsp;Name: Dennis
> &nbsp;&nbsp;Expertise: Cooking and recipe specialist — recipes, techniques, ingredient substitutions, meal planning
>
> &nbsp;&nbsp;Allow? (yes/no)
>
> **You:** yes
>
> **Memtrix:** Dennis is ready! His Matrix user is `@dennis:memtrix.local`. Invite him to a room to start chatting.

**2. Chat with the sub-agent directly:**

Invite `@dennis:memtrix.local` to a Matrix room and chat like you would with Memtrix:

> **You:** What's a good substitute for buttermilk?
>
> **Dennis:** Mix 1 tablespoon of lemon juice or white vinegar into 1 cup of regular milk. Let it sit for 5 minutes. Works in any baking recipe that calls for buttermilk.

Dennis has his own memory — he'll remember your preferences and past conversations.

**3. Let agents consult each other:**

Back in your room with Memtrix:

> **You:** I'm planning a dinner party for Saturday. Ask Dennis for a menu suggestion.
>
> **Memtrix:** Dennis suggests a three-course menu: roasted tomato soup to start, herb-crusted salmon with roasted vegetables as the main, and a lemon tart for dessert. He says the salmon pairs well with a Sauvignon Blanc if you want a wine recommendation.

Memtrix used `ask_agent` behind the scenes to consult Dennis, then wove the answer into its own response.

**4. Sub-agents can ask the main agent too:**

In your room with Dennis:

> **You:** What did I have planned for this weekend again?
>
> **Dennis:** *(uses `ask_agent` to check with Memtrix, who has the shared memory)* You mentioned a dinner party on Saturday. Want me to help plan the cooking timeline?

<br>

## 💬 Sessions & Commands

Each Matrix room gets its own independent conversation session. Multiple rooms = multiple contexts.

| Command | Action |
|:--|:--|
| `/clear` | Start a fresh session in the current room |
| `/new` | Alias for `/clear` |
| `/verbose on\|off` | Toggle real-time tool execution notifications |
| `/reasoning on\|off` | Toggle display of model reasoning/thinking |
| `/costs` | Show OpenRouter credit usage (today/week/month/all-time). OpenRouter only |
| `/help` | List available commands |

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
        },
        "my-openai-compatible": {
            "type": "openai_compatible",
            "base_url": "http://host.docker.internal:8000/v1",
            "api_key": "$OPENAI_API_KEY"
        }
    },
    "models": {
        "my-model": {
            "provider": "my-ollama",
            "model": "llama3",
            "think": true,
            "vision": false
        }
    },
    "channels": {
        "matrix": {
            "type": "matrix",
            "homeserver": "http://conduit:6167",
            "user_id": "@memtrix:memtrix.local",
            "access_token": "$MATRIX_ACCESS_TOKEN"
        }
    },
    "voice": {
        "enabled": false,
        "provider": "local",
        "model": "base",
        "language": null,
        "max_audio_bytes": 25000000,
        "timeout_seconds": 180
    }
}
```

Values starting with `$` are resolved at startup. By default they read from environment variables (prefixed with `MEMTRIX_SECRET_`) — for example, `$MATRIX_ACCESS_TOKEN` reads from `MEMTRIX_SECRET_MATRIX_ACCESS_TOKEN` in `.env`. If the optional Bitwarden backend is enabled, placeholders resolve from Bitwarden Secrets Manager first (by the placeholder name, e.g. `MATRIX_ACCESS_TOKEN`), falling back to the environment.

The `openai_compatible` provider points at any endpoint that speaks the OpenAI chat-completions API (llama.cpp, vLLM, LM Studio, an OpenAI-shim, a self-hosted gateway, or OpenAI itself). Its `api_key` is optional — leave it out for key-less local servers. In the web control panel, the Models page can **Discover** the model identifiers a provider exposes so you can pick one instead of typing it by hand.

Set `"vision": true` on a model (or tick the **Vision** checkbox on the Models page) if it can see images. Pictures the user sends in chat — PNG, JPG, GIF, WebP — are then delivered to the model directly instead of as a file path, so it can describe and reason over them. The image is expanded into each backend's native multimodal format at send time (Ollama's `images` field, or OpenAI-style `image_url` data URLs for OpenRouter and OpenAI-compatible endpoints) and kept in context across turns, bounded to the most recent few (up to 4 images, 10 MB each).

When `voice.enabled` is `true`, Matrix voice notes are transcribed locally with the configured model and passed into the normal agent flow as text.

</details>

<br>

## 🔒 Security

Memtrix is designed with defense-in-depth — multiple independent layers that each limit what the system (and the LLM) can do, even if one layer is bypassed.

### Container Isolation

The Docker container runs locked down by default:

- **Non-root user** — runs as `memtrix` (UID 1000), never root
- **Read-only filesystem** — immutable root via `read_only: true`, only `workspace/`, `data/`, and `/tmp` are writable
- **All capabilities dropped** — `cap_drop: ALL` with `no-new-privileges: true`
- **No shell tools in image** — `curl`, `wget`, and other network utilities are not installed
- **Internal-only networking** — Memtrix, Conduit, and SearXNG communicate on a private Docker network with no published ports for the bot itself

### No Arbitrary Code Execution

The LLM has **no shell access**. There is no `run_command` tool — every action the agent can take is through a purpose-built tool with its own validation. Tools are auto-discovered at startup but each one enforces its own constraints at the code level.

### SSRF Protection

All outbound tools (`fetch_url`, `download_file`, `git_clone`) validate URLs against:

- A **hostname blocklist** of internal Docker service names (`conduit`, `searxng`, `localhost`, etc.)
- **DNS resolution** — hostnames are resolved and the resulting IPs are checked against private, loopback, link-local, and reserved ranges

This prevents the LLM from using tools to reach internal services or the host network.

### Human-in-the-Loop Confirmation

Sensitive operations require explicit user approval before executing:

- **File downloads** — the user sees the URL and destination path and must confirm with yes/no
- **File overwrites** — overwriting an existing file requires user approval

The confirmation prompt is delivered through the same channel (Matrix or CLI) and blocks until the user responds.

### File System Protection

All file and directory tools enforce:

- **Path traversal prevention** — every path is validated with `os.path.realpath()` to stay within the workspace
- **Core file protection** — system files (`AGENT.md`, `SOUL.md`, etc.) are only accessible through dedicated core file tools with a strict allowlist
- **Memory directory protection** — `memory/` is off-limits to general file tools; only the memory tools can access it
- **Read-before-write enforcement** — per-room tracking ensures the LLM reads a file before it can modify it
- **Profile-card write protection** — `USER.md` and `MEMORY.md` are owned by the reasoning memory; `write_core_file` refuses to edit them

### Prompt Injection Mitigation

Everything that does not come from the user is treated as untrusted data, and it is both marked and actively screened:

- **Web search results**, **fetched URLs**, **remote SSH command output**, **downloaded files**, and **user-uploaded attachments** are all prefixed with an untrusted-content disclaimer
- **Active screening with [ProtectAI's DeBERTa prompt-injection detector](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2)** — the output of the web-fetching tools (`web_search` and `fetch_url`) is run through a local prompt-injection classifier before it reaches the model (no data leaves the container, and the model is openly licensed so no HuggingFace token is needed). If the content is flagged as an injection or jailbreak attempt, the tool result is replaced with a tool-error so the malicious text never enters the conversation, and the model is told the source is untrusted
- Screening is configured under the `prompt_guard` block: `enabled` (default `true`), `model` (a short name like `deberta`, or any full HuggingFace repo id of a prompt-injection sequence classifier), `threshold` (default `0.5`), `max_chars`, and `fail_closed` (default `false` — fails open if the classifier cannot load). The model downloads once to `data/models/` and is reused across restarts
- Attachment filenames are sanitized with `os.path.basename()` and auto-incremented on collision to prevent overwrites

### Secret Management

- Secrets (access tokens, API keys) live in `.env` and are injected at container startup
- Optionally, secrets can be stored in **Bitwarden Secrets Manager** instead — then the only secret on the host is a single Bitwarden access token (`BWS_ACCESS_TOKEN`), and everything else is fetched at startup
- Secrets are resolved once at boot and cleared from the process environment (including the Bitwarden token)
- SearXNG gets a randomly generated secret key during setup — no hardcoded defaults in production

<br>

## 📂 Project Structure

```
Memtrix/
├── src/
│   ├── app/                          # Entry points & top-level orchestration
│   │   ├── main.py                   # Agent entry point (python -m src.app.main)
│   │   ├── memtrix.py                # Core — wires channels, providers, sessions
│   │   └── onboarding.py             # Interactive setup wizard (Rich TUI)
│   ├── core/                         # Core primitives
│   │   ├── config.py                 # Config I/O + subsystem resolvers
│   │   ├── session.py                # Per-room conversation persistence
│   │   ├── lifecycle.py              # Heartbeat, restart & deriver signals
│   │   ├── commands.py               # Slash command registry
│   │   ├── usage.py                  # Provider cost reporting
│   │   └── verification.py           # Config validation + live tests
│   ├── agents/                       # Agent orchestration
│   │   ├── orchestrator.py           # Agentic loop — LLM calls, tool execution
│   │   └── manager.py                # Sub-agent lifecycle management
│   ├── memory/                       # Long-term memory subsystem
│   │   ├── index.py                  # ChromaDB + local embeddings (RAG)
│   │   ├── store.py                  # Reasoning-memory store (conclusions + cards)
│   │   └── deriver.py                # Background reasoning thread
│   ├── indexing/                     # Documentation & skill indexes
│   │   ├── docs.py                   # Bundled docs vector index
│   │   └── skills.py                 # Per-agent skill store + vector retrieval
│   ├── integrations/                 # External integrations
│   │   ├── bitwarden.py              # Bitwarden Secrets Manager backend
│   │   ├── secrets.py                # Secret resolution + sanitization
│   │   ├── transcription.py          # Local speech-to-text
│   │   └── ssh/                      # Persistent SSH sessions + key/host registry
│   │       ├── manager.py            # Connection registry, keys, hosts
│   │       ├── connection.py         # Persistent interactive shell wrapper
│   │       └── exceptions.py         # SSH error types
│   ├── channels/
│   │   ├── base.py                   # BaseChannel interface
│   │   ├── cli.py                    # CLI channel (stdin/stdout)
│   │   └── matrix.py                 # Matrix channel (nio + async bridge)
│   ├── providers/
│   │   ├── base.py                   # BaseProvider interface
│   │   ├── ollama.py                 # Ollama LLM provider
│   │   ├── openrouter.py             # OpenRouter LLM provider
│   │   └── utils.py                  # Dynamic provider discovery
│   ├── tools/                        # Tools, grouped by category (auto-discovered)
│   │   ├── base.py                   # BaseTool interface + read tracker
│   │   ├── utils.py                  # Recursive tool discovery
│   │   ├── agents/                   # create/list/delete/ask sub-agent tools
│   │   ├── docs/                     # ask/search bundled documentation
│   │   ├── files/                    # read/write/create/delete files & dirs, git, downloads
│   │   ├── memory/                   # search/profile/conclude reasoning memory + conversation search
│   │   ├── ssh/                      # gen-key, host registry, connect/run/disconnect
│   │   ├── web/                      # web search + URL fetch
│   │   └── misc/                     # time, react, skill management
│   ├── web/                          # FastAPI control panel (python -m src.web)
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
├── agents/                           # Sub-agent workspaces (isolated per agent)
├── data/                             # Persistent data (config, sessions, vector index)
├── Dockerfile
├── docker-compose.yml
├── .env                              # Secrets (gitignored)
├── requirements.txt
├── setup.sh
├── onboard.sh
└── run.sh
```

<br>

## 🔌 Adding a Provider

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

<br>

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
