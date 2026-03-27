# Changelog

## 2.4.1

- **Fix human-in-the-loop bypass during inter-agent calls** ([audit 2026-03-27](audits/2026-03-27-security-audit-v2.4.0.md) finding #1) — `confirm_with_user()` now returns `False` (deny) when no human callback is available. Prevents auto-approval of destructive operations (downloads, overwrites, agent creation) during inter-agent calls.
- **Fix orchestrator race condition** ([audit 2026-03-27](audits/2026-03-27-security-audit-v2.4.0.md) finding #3) — `Orchestrator.run()` now accepts all callbacks (`notify`, `notify_reasoning`, `send_file`, `ask`, `agent_depth`) as parameters instead of mutable instance state. Eliminates race conditions when the same orchestrator is accessed concurrently from regular messages and inter-agent queries.
- **Fix config file write corruption** ([audit 2026-03-27](audits/2026-03-27-security-audit-v2.4.0.md) finding #4) — all config read-modify-write operations now use a shared `threading.Lock` (`CONFIG_LOCK`) to prevent concurrent writes from corrupting `config.json`.
- **Fix `_ask` callback leak to wrong room** ([audit 2026-03-27](audits/2026-03-27-security-audit-v2.4.0.md) finding #7) — resolved by the stateless orchestrator refactor. `_ask` is no longer instance state, so it cannot leak to the wrong room during inter-agent calls.
- **Fix resource leak on agent deletion** ([audit 2026-03-27](audits/2026-03-27-security-audit-v2.4.0.md) finding #8) — `delete_agent()` now cleans up per-agent locks, commands, and internal sessions in addition to threads and orchestrators.
- **Fix internal session accumulation** ([audit 2026-03-27](audits/2026-03-27-security-audit-v2.4.0.md) finding #9) — inter-agent sessions are automatically trimmed to 50 messages (preserving the system prompt) to prevent unbounded memory growth.

## 2.4.0

- **Inter-agent communication** — agents can now consult each other using the `ask_agent` tool. Main agent can ask sub-agents, sub-agents can ask the main agent or other sub-agents. Responses flow back naturally into the calling agent's reasoning.
- New tool: `ask_agent` — sends a question to another agent by name and returns their response. Available to all agents (main and sub-agents).
- **Deadlock prevention** — per-agent `threading.Lock` with non-blocking 5-second timeout. If the target agent is busy, the caller gets an immediate "busy" response instead of hanging.
- **Depth limiting** — inter-agent calls have a maximum depth of 2 to prevent infinite recursion. Depth is tracked via the orchestrator and incremented on each hop.
- **Dedicated internal sessions** — inter-agent conversations use isolated sessions (keyed by caller/target pair) so they don't pollute user-facing chat history.
- Messages between agents use `[Channel: Internal, Sender: <name>]` headers, consistent with the existing channel header system.
- AGENT.md updated with Agent Communication section explaining `ask_agent` usage.

## 2.3.0

- **Custom agent name** — onboarding now asks the user to name the main agent (default: Memtrix). The chosen name is used for the Matrix username, display name, system prompt identity, sub-agent naming conventions, and SOUL templates.
- The name is stored in `main-agent.name` in config and propagated everywhere: Matrix bot registration, channel display name, workspace `AGENT.md`, sub-agent default display names, sub-agent Matrix usernames, and sub-agent SOUL/AGENT.md templates.
- **Real names for sub-agents** — `create_agent` now requires a real human name (e.g. "Dennis", "Jenny") instead of a technical slug. The slug is derived internally for directories, Matrix usernames, and config keys. The `display_name` parameter has been removed.
- The main agent is instructed to ask the user for a name if one isn't provided when requesting a new sub-agent.
- `delete_agent` now resolves agents by display name (case-insensitive) or slug.

## 2.2.1

- Updated default `BEHAVIOR.md` template — streamlined behavioral guidelines for new agents.

## 2.2.0

- **Channel-aware messages** — every message now includes a `[Channel: <name>, Sender: <name>]` header so agents know the communication platform and who is speaking. Applies to Matrix and CLI channels.
- **Bot loop prevention** — `MatrixChannel` now accepts a shared set of known bot user IDs and silently drops messages from other Memtrix agents. Prevents infinite response loops when multiple agents share a room. The set is updated in real-time when agents are created or deleted.
- **System prompt update** — new `## Communication Channel` section in `AGENT.md` explains the header format, telling the agent to never fabricate headers in responses.
- Sender display names are sanitized (brackets stripped, 50-char limit) to prevent prompt injection via Matrix profile names.
- Slash commands (`/clear`, `/verbose`, etc.) work correctly with the header — the raw body is extracted before command matching.

## 2.1.0

- **Shared USER.md** — sub-agents now symlink to the main agent's `USER.md` instead of getting their own copy. All agents read and write the same file, so user info stays consistent everywhere.
- Sub-agents no longer receive agent management tools (`create_agent`, `list_agents`, `delete_agent`).
- Sub-agents inherit the main agent's live `BEHAVIOR.md` instead of a static template.
- The `## Sub-Agents` section is stripped from scaffolded `AGENT.md` for sub-agents.

## 2.0.1

- Fix sub-agent creation failing with "unauthorized" — registration token was missing from `.env` on existing installs.
- `onboard.sh` now ensures the Conduit registration token is written to `.env` after onboarding.
- Conduit registration is now token-protected instead of being toggled on/off — `onboard.sh` no longer disables registration after setup.

## 2.0.0

- **Sub-agents** — Memtrix can now create specialist sub-agents on the user's behalf. Each sub-agent is a fully independent agent with its own Matrix user, workspace, core files, memory, vector index, and conversation sessions.
- New tools: `create_agent`, `list_agents`, `delete_agent` — the main agent uses these to manage sub-agents. Both creation and deletion require human-in-the-loop approval.
- **Isolated workspaces** — sub-agent workspaces live in `agents/<name>/`, a separate volume mount from the main agent's `workspace/`. Path traversal protection naturally enforces isolation.
- **Per-agent memory index** — each sub-agent gets its own ChromaDB collection and vector index.
- `MemoryIndex` now supports named collections for multi-agent use.
- `AgentManager` class — handles sub-agent lifecycle: Matrix user registration, workspace scaffolding, orchestrator creation, background thread management, config persistence.
- Sub-agents auto-start on boot from the agents registry in config.
- Updated AGENT.md system prompt with sub-agent management instructions.
- Docker: added `./agents:/home/memtrix/agents` volume mount.
- Dockerfile: pre-creates `/home/memtrix/agents` directory.
- Config template: added `"agents": {}` section.
- `setup.sh`: creates `agents/` directory and sets ownership.

## 1.9.0

- **SSRF protection** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.8.6.md) findings #1, #2, #5) — `fetch_url`, `download_file`, and `git_clone` now block requests to internal Docker services (conduit, searxng) and private/reserved IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x, link-local, etc.).
- **Attachment filename collision fix** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.8.6.md) finding #3) — duplicate attachment filenames now get an auto-incremented suffix instead of silently overwriting.
- **Human-in-the-loop confirmations** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.8.6.md) findings #4, #9) — `download_file` now asks the user for approval before downloading. `create_file` asks before overwriting existing files. The user can answer yes/no; any other response re-prompts.
- **Sanitize filename in agent message** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.8.6.md) finding #6) — the `[File received: ...]` message now uses the sanitized, actually-saved filename instead of the raw `event.body`.
- **Memory sync error handling** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.8.6.md) finding #8) — the background memory sync thread now catches and logs exceptions instead of silently dying.

## 1.8.6

- **Fix session ID path traversal** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) finding #5) — validate `session_id` matches UUID v4 format before using in file paths.
- **Fix `_read_files` cross-room bypass** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) finding #6) — read-before-write tracker is now keyed by `room_id`, preventing Room B from exploiting Room A's read authorization.
- **Disable Conduit open registration** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) finding #7) — set `allow_registration = false` in `conduit.toml`.
- **Generate random SearXNG secret** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) finding #8) — `setup.sh` now generates a unique `secret_key` per installation.

## 1.8.5

- **Fix attachment filename path traversal** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) finding #4) — sanitize Matrix attachment filenames to basename-only via `os.path.basename()`, preventing `../../` path traversal in attacker-controlled `event.body`.

## 1.8.4

- Add `download_file` tool — download files from URLs and save them to `downloads/` in the workspace. Supports any file type, streams with 50 MB size limit, URL validation.
- Mark `downloads/` as untrusted in `read_file` — downloaded files are prefixed with an untrusted-content disclaimer (same as attachments).
- Protect `downloads/` from deletion in `delete_directory`.

## 1.8.3

- **Mitigate indirect prompt injection** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) findings #2, #3) — `fetch_url`, `web_search`, and `read_file` (for attachments) now prefix results with an untrusted-content disclaimer instructing the LLM to ignore any embedded instructions.

## 1.8.2

- Add `git_clone` tool — clone public git repositories (GitHub, GitLab, etc.) into the workspace via HTTPS.

## 1.8.1

- Add `list_directory` tool — list the contents of a directory in the workspace.

## 1.8.0

- **Remove `run_command` tool** ([audit 2026-03-22](audits/2026-03-22-security-audit-v1.7.0.md) finding #1) — eliminates arbitrary shell execution, closing the primary prompt injection → RCE attack vector.
- Remove `curl` and `wget` from the Docker image — no outbound network tools available inside the container.
- New `read_file` tool — read files from the workspace (text and PDF supported via automatic extraction; core files and memory files are blocked).
- New `create_file` tool — create or overwrite text files (core files and memory files are blocked).
- New `delete_file` tool — permanently delete files (core files and memory files are protected).
- New `create_directory` tool — create directories in the workspace.
- New `delete_directory` tool — permanently delete directories (memory/ and attachments/ are protected).
- Merge `read_pdf` into `read_file` — PDF extraction happens automatically based on file extension.
- All new tools enforce path traversal protection via `os.path.realpath()`.
- Update AGENT.md with comprehensive file management instructions.

## 1.7.0

- Add Context Enrichment workflow — Memtrix now silently searches memory (and the web as fallback) when it encounters unfamiliar names, topics, or terms, then weaves the context into natural conversation.
- Redesign README with badges, feature grid, collapsible sections, and modern layout.

## 1.6.3

- Add first-startup note to README about embedding model download.
- Increase conduit startup windows to 60s in `setup.sh`

## 1.6.2

- Fix `sudo` setup on Linux — `setup.sh` and `onboard.sh` now `chown` data and workspace directories to UID/GID 1000 so the container's non-root user can read the configuration.
- Secure `.env` file permissions (`chmod 600`) after onboarding.
- Add Linux Docker permissions note to README setup section.

## 1.6.1

- Fix `fetch_url` tool — increase timeout from 15s to 30s and use realistic browser headers (User-Agent, Accept, Accept-Language) to avoid being blocked by sites that reject non-browser requests.

## 1.6.0

- New `read_pdf` tool — extract text from PDF files in the workspace.
- Add `curl` and `wget` to the Docker container.

## 1.5.1

- Enable reasoning/thinking for OpenRouter models via `include_reasoning` parameter.
- Extract reasoning content from OpenRouter responses (`reasoning` / `reasoning_content`).

## 1.5.0

- Replace Ollama-based embeddings with local `nomic-embed-text-v1.5` via `sentence-transformers`.
- Embedding model downloads once to `data/models/` and runs on-device (no external API dependency).
- Remove `embedding_model` config key — the model is now built-in.
- Use Matryoshka truncation (768 → 256 dimensions) for faster embedding inference.
- Reindex all memory files on startup; sync changed files every 5 minutes via background thread.
- Remove inline embedding from `write_memory_file` tool for faster tool execution.
- Mount `data/cache/` as writable cache volume for ChromaDB and HuggingFace.
- Add `einops` dependency required by nomic-embed-text.

## 1.4.4

- Fix OpenRouter tool-calling: sanitize message history (`type`, `id`, JSON-string arguments) and tool schemas (strip empty parameters) for strict OpenAI-compatible providers.

## 1.4.3

- Write `.env` file to `data/.env.generated` during onboarding; `onboard.sh` auto-moves it to the project root.
- Add default reasoing to `true` in config.json

## 1.4.2

- Print a single copy-friendly `.env` template with descriptions at the end of onboarding instead of inline secret messages.

## 1.4.1

- Auto-detect secret fields during provider onboarding and store as `$PLACEHOLDER` references instead of plaintext.

## 1.4.0

- Add OpenRouter provider for accessing cloud LLMs via the OpenRouter API.

## 1.3.1

- Fix secret placeholders in `config.json` being overwritten with plaintext tokens on first session creation.

## 1.3.0

- Move secrets from `config.json` to `.env` file with `$PLACEHOLDER` references.
- Resolve secrets from `MEMTRIX_SECRET_*` environment variables at startup.
- Clear secrets from process environment after reading to prevent leakage via `env`.
- Sanitize subprocess environment in `run_command` tool to strip secret variables.
- Onboarding prints bot token for manual `.env` setup instead of writing to config.

## 1.2.0

- Receive files sent via Element — saved to `workspace/attachments/`.
- New `send_file` tool — Memtrix can send files back to the user.
- Use Conduit's authenticated media endpoint (`/_matrix/client/v1/media/download/`) for file downloads.

## 1.1.2

- System Prompt structure update
- Update static core files

## 1.1.1

- Store sessions in date-based subdirectories (`data/sessions/yyyy-mm-dd/`).

## 1.1.0

- Enable extended thinking (`think: true`) for Ollama models via per-model config.
- Add `/reasoning on|off` command to display model reasoning in Matrix.
- Strip leaked `<think>` tags from model responses.
- Simplify self-learning prompt to improve smaller model performance.

## 1.0.3

- Fix onboarding issue

## 1.0.2

- Install `git` in container environment

## 1.0.1

- Set Matrix display names during onboarding and on bot startup.

## 1.0.0

Initial release.

- **Channels**: Matrix (via Conduit) and CLI.
- **Providers**: Ollama with dynamic discovery.
- **Orchestrator**: Agentic tool-calling loop with configurable max iterations.
- **Tools**: `get_current_time`, `read_core_file`, `write_core_file`, `read_memory_file`, `write_memory_file`, `search_memory`, `web_search`, `fetch_url`, `run_command`.
- **Persona system**: AGENT.md, BEHAVIOR.md, SOUL.md, USER.md, MEMORY.md with placeholder injection.
- **Self-learning**: Automatic updates to core files and daily memory journals.
- **Memory RAG**: Semantic search over daily journals via ChromaDB + Ollama embeddings.
- **Per-room sessions**: Each Matrix room gets its own conversation context.
- **Slash commands**: `/clear`, `/verbose`, `/help`.
- **Web access**: SearXNG for search, BeautifulSoup for URL fetching.
- **Shell access**: Sandboxed `run_command` tool.
- **Security**: Non-root, read-only filesystem, all capabilities dropped, no privilege escalation.
- **Onboarding**: Interactive Rich TUI wizard for setup.