# ---------------------------------------------------------------------------
# Stage 1: build the React control-panel SPA
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend

WORKDIR /build

# Install dependencies first for better layer caching
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

# Build the static SPA into /build/dist
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: python runtime (shared by the agent and the web control panel)
# ---------------------------------------------------------------------------
FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN groupadd --gid 1000 memtrix \
 && useradd --uid 1000 --gid memtrix --shell /bin/sh --create-home memtrix

# App lives at ~/source (i.e. /home/memtrix/source)
WORKDIR /home/memtrix/source

# Install dependencies as root before locking down
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and the supervisor entrypoint, handing ownership to memtrix
COPY --chown=memtrix:memtrix src/ src/
COPY --chown=memtrix:memtrix docker/ docker/
RUN chmod +x docker/agent-entrypoint.sh

# Bundle the documentation site so the agent can research its own docs at runtime.
# website/ is not mounted into the container, so the file must be baked into the
# image. website/docs.html remains the single source of truth.
COPY --chown=memtrix:memtrix website/docs.html src/static/docs.html

# Copy the built SPA into the location the web backend serves from
COPY --from=frontend --chown=memtrix:memtrix /build/dist/ src/web/static/

# Pre-create runtime directories so mounted volumes are owned correctly
RUN mkdir -p /home/memtrix/workspace \
 && mkdir -p /home/memtrix/agents \
 && mkdir -p /home/memtrix/data \
 && chown -R memtrix:memtrix /home/memtrix

# Drop to non-root for runtime
USER memtrix

# Workspace and data are mounted at runtime — do not bake them in
# ~/workspace  → /home/memtrix/workspace
# ~/data/      → /home/memtrix/data

ENV PYTHONUNBUFFERED=1

# Default command runs the agent directly. Compose overrides this with the
# supervisor entrypoint (agent) or the web server command (control panel).
CMD ["python", "-m", "src.main"]
