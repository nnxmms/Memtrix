# Changelog

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