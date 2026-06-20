# Changelog

## 2.20.0

- Startup is now non-blocking: the agent connects and starts responding within seconds instead of waiting 40-90s for the embedding model and indexes. The local embedding model is loaded lazily on first use (on a background thread), and the initial memory and documentation indexing now runs on the existing periodic-sync thread rather than blocking process start.
- Made the embedding model a thread-safe lazy singleton so concurrent callers share a single load, and guarded the periodic-sync threads against double-start.
- Memory reindexing now embeds every file in a single batched upsert instead of one call per file, and an unreadable memory file is skipped with a warning instead of aborting the whole pass (files are also read as UTF-8).
- Fixed three path regressions from the v2.19.1 package restructure where bundled resources were resolved relative to the module's new, deeper location: the documentation index (`docs.html`), the sub-agent system-prompt template (`AGENT.md`), and the onboarding registration-token lookup (`conduit.toml`) now resolve correctly against `src/static/` again. The docs index in particular had been silently empty since the restructure.

## 2.19.1

- Reorganized the Python backend into clear, domain-oriented packages for long-term maintainability. The previously flat `src/` module layout is now grouped into `app/` (entry points and top-level orchestration), `core/` (config, session, lifecycle, commands, usage, verification), `agents/` (the agentic loop and sub-agent manager), `memory/` (vector index, conclusion store, background deriver), `indexing/` (docs and skills catalogs), and `integrations/` (Bitwarden, secrets, SSH, transcription).
- Tool implementations are now organized into category subpackages (`agents/`, `docs/`, `files/`, `memory/`, `ssh/`, `web/`, `misc/`) and discovered recursively, replacing the single flat tool directory. Tool discovery now only instantiates classes defined in each module, avoiding accidental duplicates.
- Split the SSH integration into a focused package: connection handling, error types, and the manager now live in separate modules behind a stable public interface.
- Removed dead code left over from an abandoned earlier refactor (orphaned modules with broken imports and stale package directories).
- Updated container and script entry points to the new module paths (`src.app.main`, `src.app.onboarding`). No runtime behavior changes.

## 2.19.0

- Matrix voice messages can now be processed locally on-device: incoming `m.audio` events are downloaded to `attachments/`, transcribed with local speech-to-text, and forwarded to the normal agent loop as user text context.
- New optional `voice` config block controls Matrix transcription behavior: `enabled`, `provider` (currently `local`), `model`, `language`, `max_audio_bytes`, and `timeout_seconds`.
- Added a local STT module with lazy model initialization so voice transcription does not increase startup time; failures and timeouts degrade gracefully instead of breaking message handling.
- Added config validation for the new voice settings and web config API support for editing the `voice` section.

## 2.18.4

- Improved peer-card curation quality so `USER.md` and `MEMORY.md` no longer end with abrupt mid-bullet cutoffs when the character cap is hit. Card writes now enforce the budget with boundary-aware truncation (line/sentence/word) instead of naive raw slicing, preserving readable Markdown structure.
- Strengthened deriver card-generation prompts to prefer fewer, higher-signal complete bullets and added a bounded one-shot compression retry when a draft significantly exceeds `peer_card_max_chars`, reducing fallback truncation frequency.
- Curation input is now shaped more defensively by shortening overlong source conclusions before prompt assembly, preventing single verbose records from dominating the peer-card budget.

## 2.18.3

- Added `/stop` slash command to interrupt the current run immediately without affecting the session. Useful for canceling long-running operations or tool calls. The session history is preserved and the user can send the next message normally. The command is now checked at each iteration of the agentic loop, ensuring instant interruption even during LLM reasoning. Documented in the agent's system prompt and listed in `/help`.
- SSH tool now enforces correct sudo usage. The `ssh_run` tool description has been clarified to state that `sudo` must be passed as a parameter, not embedded in the command string. If the LLM does embed `sudo` in the command, the tool detects it, auto-corrects by stripping the prefix and setting `sudo=true`, and logs a warning. The system prompt (AGENT.md) now includes explicit SSH usage guidance with correct and incorrect examples.
- SSH sudo commands no longer hang waiting for password input when the user has passwordless sudo (NOPASSWD) configured. When `sudo=true` is set on `ssh_run`, the tool now first attempts a non-interactive `sudo -n` call; if it succeeds, the command output is returned immediately without prompting. Only if non-interactive sudo fails due to a password requirement does the tool ask the user, avoiding unnecessary hangs on systems with passwordless sudo for certain commands.
- Added `/stop` slash command to interrupt the current run immediately without affecting the session. Useful for canceling long-running operations or tool calls. The session history is preserved and the user can send the next message normally. The command is listed in `/help` and documented in the agent's system prompt.

## 2.18.2

- SSH tool now enforces correct sudo usage. The `ssh_run` tool description has been clarified to state that `sudo` must be passed as a parameter, not embedded in the command string. If the LLM does embed `sudo` in the command, the tool detects it, auto-corrects by stripping the prefix and setting `sudo=true`, and logs a warning. The system prompt (AGENT.md) now includes explicit SSH usage guidance with correct and incorrect examples.

## 2.18.1

- SSH sudo commands no longer hang waiting for password input when the user has passwordless sudo (NOPASSWD) configured. When `sudo=true` is set on `ssh_run`, the tool now first attempts a non-interactive `sudo -n` call; if it succeeds, the command output is returned immediately without prompting. Only if non-interactive sudo fails due to a password requirement does the tool ask the user, avoiding unnecessary hangs on systems with passwordless sudo for certain commands.

## 2.18.0

- The agent's tool-call loop limit is now configurable and its default has been raised from 10 to 25 rounds per request. Each user request runs an iterative loop (call the model, run tools, feed results back, repeat) until a final answer is produced; previously a hard-coded cap of 10 rounds could cut off longer multi-step tasks and force an early final answer. A new optional `agent` config block exposes `max_iterations` (default 25), applied to both the main agent and every sub-agent. Installs without an `agent` section automatically use the new default.

## 2.17.2

- The skill self-authoring guidance in the agent system prompt is now mandatory after any larger task. Previously the agent was told to "briefly evaluate" whether a finished task was skill-worthy; it is now required to perform that evaluation as the explicit last step of completing a larger task (5+ tool calls, error recovery, a user correction, or a non-obvious workflow) and to capture or improve a skill unless an equally good one already exists. Skipping the check is no longer permitted — declining to save must be a deliberate judgement rather than an omission.

## 2.17.1

- Skills now use the Agent Skills progressive-disclosure model instead of embedding-based matching. At the start of every turn the agent sees a catalog of all its skills (each as `name: description`) and decides for itself which, if any, fits the current task — then loads that skill's full instructions on demand with `skill_manage action: view`. This removes the ChromaDB vector index, the local embedding step, and the `suggest_top_k` / `suggestion_max_distance` tuning knobs, whose distance threshold could wrongly reject relevant skills. The `skills` config block now has a single option, `enabled` (default true). Behaviour is otherwise unchanged: each agent still manages its own isolated `skills/<name>/SKILL.md` files, and the `skill_manage` tool keeps its `create`, `view`, `list`, `edit`, `patch`, and `delete` actions. The old `data/skills_index/` ChromaDB directory is no longer used.

## 2.17.0

- Skills - Memtrix can now create and reuse its own skills: short, reusable task workflows it writes for itself so it handles recurring kinds of work better over time. A skill is a generalized set of steps (e.g. "when performing a security audit of a server, do these steps") stored in the agent's workspace as `skills/<name>/SKILL.md`. Skills are a distinct layer from SOUL.md/BEHAVIOR.md (character) and memory (facts/journal) - they capture *how* the agent works.
- Authoring happens inside the normal agent loop with no second model: after finishing a task, the agent evaluates whether it was skill-worthy (took 5+ tool calls, required error recovery, involved a user correction, or followed a non-obvious workflow) and, if so, silently captures the approach. If an existing skill proved suboptimal, it improves it on the spot. One new tool, `skill_manage`, drives this with actions `create`, `view`, `list`, `edit`, `patch`, and `delete`.
- Discovery is retrieval-based: each incoming message is embedded and matched against the agent's own skills, and any sufficiently relevant skill is surfaced to the agent as a transient suggestion (reusing the local embedding model and ChromaDB). The agent then loads the full instructions on demand and follows them. Skills contain instructions and reference files only - there is no separate code execution, preserving Memtrix's no-local-shell security model.
- Each agent (main and every sub-agent) manages its own isolated skill store; the index rebuilds only when skills change and is kept current by a background sync. Gated behind a new optional `skills` config block: `enabled` (default true), `suggest_top_k` (2), `suggestion_max_distance` (0.55, lower = stricter matching). When disabled, the tool is not loaded and no suggestions are injected.

## 2.16.0

- SSH remote administration - Memtrix can now act as a sysadmin on remote hosts over SSH. It opens a persistent interactive session and works inside it across many commands, so the working directory and environment persist between calls just like a human at a terminal. Eight new tools: `ssh_gen_key` (generate the agent's own ed25519 key), `ssh_get_pub_key` (return the public key to install in a host's authorized_keys), `ssh_add_host` / `ssh_remove_host` / `ssh_get_remote_hosts` (manage a registry of named hosts), `ssh_connect` / `ssh_disconnect` (open and close persistent sessions), and `ssh_run` (run a command in the open session, with optional `sudo`).
- Security: authentication is key-only (install Memtrix's public key on each host); host keys are pinned trust-on-first-use with an explicit fingerprint confirmation; potentially destructive commands (`rm`, `dd`, `mkfs`, `shutdown`/`reboot`, recursive `chmod`/`chown`, block-device writes, etc.) require confirmation; `sudo` passwords are requested via the human-in-the-loop prompt and kept in memory only, never written to disk; SSH to Memtrix's own internal services and to loopback/link-local addresses is refused, while private LAN hosts are allowed. The private key is written `0600` and never disclosed.
- The SSH tools are available to the main agent only (excluded from sub-agents) and are gated behind a new optional `ssh` config block: `enabled` (default true), `connect_timeout` (15), `command_timeout` (120), `max_output_chars` (20000). Open sessions are closed on shutdown.

## 2.15.0

- Typing indicator - Memtrix now shows the native Matrix "typing" indicator while it is working on a reply, so you can tell it received your message and is composing a response. The indicator is refreshed periodically for long-running replies and cleared as soon as the answer is sent. Applies to both text and file/attachment messages.

## 2.14.0

- Daily memory consolidation - a background pass now distills accumulated reasoning conclusions into a smaller, cleaner set, like memory consolidation during sleep. For each peer it merges duplicates and near-duplicates, drops anything outdated, contradicted, trivial, or ephemeral, and synthesizes higher-order patterns from related items, then re-curates the peer card from the distilled set. Conclusions you added manually are preserved untouched; only derived ones are consolidated. The schedule is persisted to disk so it survives restarts, runs roughly every `consolidation_interval_hours` (default 24), and skips peers below `consolidation_min_items` (default 12). It honors the deriver pause toggle.
- New `/consolidate` slash command triggers a consolidation pass on demand for the main agent, reporting how many conclusions were distilled per peer. Available whenever reasoning memory is enabled.
- New `memory` config keys (all optional, safe defaults merged for existing installs): `consolidation`, `consolidation_interval_hours`, `consolidation_min_items`.

## 2.13.1

- Fix the agent crashing on startup with `chromadb.errors.DuplicateIDError: Expected IDs to be unique` when building the documentation index. Pages with intro prose before their first heading (and headings without an `id` attribute) both fell back to the page id, producing colliding chunk ids such as `agents::agents`. Each documentation chunk now gets a unique id via a per-page sequence counter, so the docs index builds cleanly. Affected fresh starts and migrations of existing instances.

## 2.13.0

- Memtrix can now research its own documentation. Two new always-on tools let the agent (and every sub-agent) answer "how does Memtrix work?" questions from the bundled docs: `search_docs` returns ranked documentation sections with citations and makes no LLM call, while `ask_docs` retrieves the most relevant sections and synthesizes a direct, grounded answer with sources. Both draw from the same documentation that powers the website.
- The documentation site (`website/docs.html`) is parsed into searchable sections and embedded into a shared ChromaDB `documentation` collection at startup, reusing the local embedding model and the shared `chroma` service. The index is content-hashed so it only rebuilds when the docs actually change, and a background sync keeps it current. The docs file is baked into the agent image at build time so it is available at runtime without mounting the website.

## 2.12.1

- Fix the background deriver crashing with `AttributeError: 'list' object has no attribute 'tolist'` when storing reasoning conclusions against the shared ChromaDB service. The local embedding function returned plain Python lists, but ChromaDB's `HttpClient` query path serializes embeddings via `convert_np_embeddings_to_list()`, which calls `.tolist()` on each embedding and expects NumPy arrays. The embedding function now returns NumPy row vectors, so both the `HttpClient` (shared `chroma` service) and `PersistentClient` (local) code paths work. Reasoning-memory de-duplication and recall no longer error out.

## 2.12.0

- New `/costs` slash command — reports OpenRouter credit usage for every configured OpenRouter provider (deduplicated by API key), including **credits used today** (current UTC day), this week, this month, and all-time, plus any configured credit limit and remaining balance. Credits are US dollars. The command queries `GET https://openrouter.ai/api/v1/key` with the provider's API key and is only available when at least one OpenRouter provider is configured. Network and authorization errors are reported gracefully.
- New `/new` slash command — an alias for `/clear` that starts a fresh session for the current room. Works for the main agent and sub-agents.

## 2.11.1

- Fix "Test connection" in the Web Control Panel failing with an authorization error for Matrix (and other secret-bearing) channels/providers — the connectivity test resolved `$PLACEHOLDER` secrets only from the local `data/secrets.env` file and `MEMTRIX_SECRET_*` environment variables, never from Bitwarden. When Bitwarden was the active backend (or the value wasn't in the managed file), the literal string `$MATRIX_ACCESS_TOKEN` was sent to the homeserver and rejected as unauthorized. Test resolution is now backend-aware and reuses the same logic as the secrets list (Bitwarden `fetch_all()` when active, otherwise managed file + environment), so pressing Test connection without editing anything uses the real stored token.

## 2.11.0

- Web Control Panel — a production-ready web UI for configuring everything Memtrix offers, served by a dedicated, hardened FastAPI container and built as a React/TypeScript single-page app. The panel runs alongside the agent (bound to `127.0.0.1:8800` by default) and shares the same config and memory store.
- Safe, validated config editing — every section (main agent, providers, models, channels, sub-agents, memory settings) is editable from the browser. Changes are validated server-side before they touch `config.json`; malformed configurations are rejected with field-level errors and never saved. Provider and channel connectivity can be live-tested ("Test connection") before saving, with `$PLACEHOLDER` secrets resolved automatically for the test.
- One-click safe restart — an "Apply & Restart" action validates the config, then requests a restart via a sentinel file watched by a supervisor entrypoint (no Docker socket access required). Restart progress streams live to the UI over Server-Sent Events using the agent's heartbeat, reporting stopping → starting → ready (or timeout).
- Secrets management — view (decrypted, masked by default with reveal) and change secrets from the UI, for both the local `.env` backend (`data/secrets.env`) and Bitwarden Secrets Manager. New values are written atomically with `0600` permissions, or upserted into Bitwarden.
- Full memory administration — browse per-peer reasoning conclusions with semantic search, edit/delete individual records, add manual conclusions (kept verbatim, skipping de-duplication), wipe a peer, and export/import the whole store as JSON. Peer cards (`USER.md`/`MEMORY.md`) can be edited directly and frozen to stop the deriver re-curating them. Background reasoning can be paused/resumed from the UI.
- Shared ChromaDB service — the reasoning-memory store now runs as a separate `chroma` service that both the agent and the web panel connect to via `chromadb.HttpClient` (set by `CHROMA_URL`), eliminating SQLite single-writer corruption when both processes read and write concurrently. Writes are additionally coordinated with file locks.
- Hardened by default — the web container drops all capabilities, runs read-only and non-root with `no-new-privileges`, and binds only to localhost. An optional shared-secret header (`MEMTRIX_WEB_TOKEN`) gates the API when set; authentication is otherwise expected to be handled by a reverse proxy.

## 2.10.3

- Fix onboarding crash when enabling Bitwarden — `BitwardenSecrets.__init__` still declared `organization_id` as a required positional argument, so the reworked wizard (which constructs the client before the org ID is known) failed with `TypeError: __init__() missing 1 required positional argument: 'organization_id'`. The argument is now optional and defaults to `None`.

## 2.10.2

- Fix Bitwarden secret storage failing with `404 Resource not found` — two causes. First, `create_secret` passed arguments to the SDK in the wrong order (`note` and `value` were swapped); the correct signature is `create(organization_id, key, value, note, project_ids)`. Second, the onboarding wizard silently swallowed project-listing failures and could proceed with no project selected, and creating a project-less secret returns a 404. Project selection is now required and listing failures are surfaced.
- Improved Bitwarden onboarding flow — the wizard now authenticates with the access token first, attempts to auto-detect the organization ID from the login response (falling back to asking when the SDK doesn't expose it), verifies it can reach the organization's secrets, then lists projects and requires you to pick one. Self-hosted endpoints are asked before connecting so verification uses the right server.

## 2.10.1

- Fix onboarding crash when using Bitwarden Secrets Manager — `list_projects()` returned the project `id` and `name` as `UUID` objects from the Bitwarden SDK, so the selected `project_id` was stored in the config as a `UUID` and `json.dump` failed with `TypeError: Object of type UUID is not JSON serializable` at the final save step. Project IDs and names are now coerced to strings, and config saving uses a `default=str` fallback as a safety net.

## 2.10.0

- Native reasoning memory — Memtrix now has a built-in "memory that reasons" layer inspired by Honcho, implemented entirely locally with no external service. A background **deriver** thread continuously reasons over each conversation and distills durable conclusions about both the user and the agent itself: explicit observations, certain deductions, and observed patterns. Conclusions are vector-indexed in a local ChromaDB store (`data/representations`) using the same on-device `nomic-embed-text-v1.5` embedder.
- Dual-peer profile cards — `USER.md` (about the user) and `MEMORY.md` (about the agent) are now compact, always-current profile cards that the deriver curates automatically and that stay injected into the system prompt. They are no longer hand-edited by the agent; the deriver re-curates them and enforces a character budget so they stay small. `write_core_file` now **rejects writes to `USER.md` and `MEMORY.md` at the code level** (only `BEHAVIOR.md` and `SOUL.md` remain writable), so the agent can't clobber the auto-maintained cards.
- Automatic recall injection — before each reply, relevant reasoned conclusions are retrieved and injected transiently into the prompt (not persisted to session history), so the agent recalls durable facts across sessions without bloating session files.
- Four native memory tools (gated by `recall_mode`) — `memory_profile` (read profile cards, no LLM), `memory_search` (semantic search over conclusions), `memory_context` (synthesized natural-language answer from memory), and `memory_conclude` (store a single high-signal durable fact immediately).
- Configurable via a new `memory` config section — `recall_mode` (`hybrid`/`context`/`tools`/`off`), `write_frequency` (`async`/`turn`/`session`/N), `reasoning_level` (`minimal`…`max`), optional `reasoning_model`, `batch_tokens`, `peer_card_max_chars`, `dual_peer`, and `inject_top_k`. The section is optional and falls back to sensible defaults, so existing deployments keep working.
- Main-agent scope for v1 — reasoning memory and its tools are enabled for the main agent only; sub-agents are unaffected. The existing daily memory journal and `search_memory` remain unchanged and complementary.

## 2.9.0

- Optional Bitwarden Secrets Manager backend — onboarding can now store all secrets (Matrix tokens, provider API keys) in Bitwarden Secrets Manager instead of a local `.env`. When enabled, the only secret on the host is a single `BWS_ACCESS_TOKEN`; everything else is fetched from Bitwarden at startup. Supports Bitwarden cloud and self-hosted servers.
- Auto-provisioning during setup — when you opt in, onboarding verifies your machine-account access token, lets you pick a Bitwarden project, and automatically creates each collected secret in Bitwarden. Only the access token is written to `.env`.
- Secrets resolution order — `$PLACEHOLDER` values now resolve from Bitwarden first (by placeholder name), then fall back to `MEMTRIX_SECRET_*` environment variables. The Bitwarden token is cleared from the process environment after boot, just like other secrets.

## 2.8.0

- Choose local or external Matrix homeserver — onboarding now lets you connect to the bundled local Conduit homeserver or an external/already-hosted Matrix server (your own Synapse, matrix.org, etc.). For external servers you provide the homeserver URL, the bot user ID, and an access token, which are verified via `/whoami` before saving.
- Channel `managed` flag — matrix channels now carry a `managed` boolean. `true` = bundled Conduit with automatic user registration; `false` = external server with manually supplied credentials.
- Server name is derived from the bot user ID — the hardcoded `memtrix.local` server name is gone; sub-agent and bot user IDs now use the domain of the configured bot account.
- Sub-agents on external homeservers — `create_agent` accepts `matrix_user_id` and `matrix_access_token` for pre-created accounts. On a managed homeserver it still registers accounts automatically; on an external one it returns clear instructions when credentials are missing.
- Conduit is now optional — the bundled Conduit container runs under a Compose `local` profile (`COMPOSE_PROFILES=local`), set automatically by onboarding. External setups don't start it.
- Resilient Matrix connect — the channel now retries the initial sync with backoff while the homeserver is starting up or briefly unreachable, instead of crashing.

## 2.7.0

- Inter-agent exchange memory — after an inter-agent call, a summary of the exchange is appended to the target agent's active user session. This means if Agent B asks Agent A something, A can later tell the user what B asked and what it answered. Previously inter-agent conversations were invisible to the user-facing session.

## 2.6.1

- Fix sub-agent verbose/reasoning settings reset on `/clear` — `_save_config()` was overwriting the entire agents section on disk with the stale in-memory copy, wiping out `verbose` and `reasoning` flags that `Commands._save_setting()` had written directly to disk. Now merges per-agent configs so disk-only keys are preserved.

## 2.6.0

- Cross-agent context awareness — when one agent consults another via `ask_agent`, the target agent's recent user conversation is automatically injected as context. If a user tells Agent A "remember the number 9, B will ask for it", and B later asks A, A now sees its own recent conversation with the user and can respond correctly. Context is capped at 10 message pairs / 4000 chars and stripped of tool-call noise.

## 2.5.4

- Automatic date for daily memory tools — `read_memory_file` and `write_memory_file` no longer accept a `filename` parameter. The date is derived server-side via `date.today()`, preventing the LLM from specifying incorrect dates.

## 2.5.3

- Clear inter-agent sessions on `/clear` — when a user clears an agent's session, all internal sessions where that agent was the caller are also dropped. Next `ask_agent` call starts a fresh conversation with no stale context.

## 2.5.2

- Per-agent verbose and reasoning — `/verbose` and `/reasoning` commands now apply to the agent they're used in. Sub-agents no longer toggle the main agent's settings. Each agent persists its own state to its own config section.
- Singleton embedding model — the SentenceTransformer model is now loaded once and shared across all `MemoryIndex` instances. Previously it was loaded separately for the main agent and every sub-agent.
- Local-only model loading — when the embedding model is already cached, `local_files_only=True` skips all HuggingFace Hub network calls. Startup with 4 sub-agents went from ~5 minutes to ~2 seconds.

## 2.5.1

- Structured logging — replaced all `print()` calls with Python's `logging` module. Logs include timestamps, log levels, and module names. Noisy third-party loggers (httpx, chromadb, sentence_transformers, nio) are suppressed to WARNING. Logging is configured centrally in `main.py` and flows to stdout for `docker compose logs`.

## 2.5.0

- Message reactions — Memtrix can now react to user messages with emoji in Matrix. The LLM decides when and what to react with, just like a human would use reactions in a chat.
- New tool: `react_to_message` — sends an emoji reaction to the current user message. Available to all agents (main and sub-agents). Only works on Matrix.
- AGENT.md updated with Reactions section guiding natural emoji usage.

## 2.4.2

- Simplified sub-agent Matrix usernames — sub-agents are now registered as `@<name>:memtrix.local` instead of `@memtrix-<name>:memtrix.local`.

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