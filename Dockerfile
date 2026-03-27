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

# Copy source code and hand ownership to memtrix
COPY --chown=memtrix:memtrix src/ src/

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

CMD ["python", "-m", "src.main"]
