# Changelog

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