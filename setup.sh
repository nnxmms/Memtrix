#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
WORKSPACE_DIR="$SCRIPT_DIR/workspace"
STATIC_DIR="$SCRIPT_DIR/src/static"

echo "Setting up Memtrix..."
echo ""

# Create data and workspace directories
mkdir -p "$DATA_DIR"
mkdir -p "$DATA_DIR/cache"
mkdir -p "$WORKSPACE_DIR/memory"

# Copy static files into ./data/ (skip if already exist)
for file in config.json; do
    dest="$DATA_DIR/$file"
    if [[ ! -f "$dest" ]]; then
        cp "$STATIC_DIR/$file" "$dest"
        echo "  Created $dest"
    else
        echo "  Skipped $dest (already exists)"
    fi
done

# Copy static files into ./workspace/ (skip if already exist)
for file in AGENT.md BEHAVIOR.md MEMORY.md SOUL.md USER.md; do
    dest="$WORKSPACE_DIR/$file"
    if [[ ! -f "$dest" ]]; then
        cp "$STATIC_DIR/$file" "$dest"
        echo "  Created $dest"
    else
        echo "  Skipped $dest (already exists)"
    fi
done

echo ""

# Fix ownership for Linux users who run setup with sudo.
# The container runs as memtrix (uid/gid 1000) and must be able to
# read/write the data and workspace directories.
chown -R 1000:1000 "$DATA_DIR" "$WORKSPACE_DIR" 2>/dev/null || true

# Build the Docker image
echo "Building Docker image..."
docker compose build

# Start Conduit so it's available for the onboarding wizard
echo ""
echo "Starting Conduit homeserver..."
docker compose up -d conduit

# Wait for Conduit to be ready
echo "Waiting for Conduit to become available..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:6167/_matrix/client/versions > /dev/null 2>&1; then
        echo "  Conduit is ready."
        break
    fi
    if [[ $i -eq 30 ]]; then
        echo "  Error: Conduit did not start within 30 seconds."
        exit 1
    fi
    sleep 1
done

echo ""
echo "All done! Run the Onboarding Wizard with:  ./onboard.sh"
