#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
WORKSPACE_DIR="$SCRIPT_DIR/workspace"
AGENTS_DIR="$SCRIPT_DIR/agents"
STATIC_DIR="$SCRIPT_DIR/src/static"

echo "Setting up Memtrix..."
echo ""

# Create data, workspace, and agents directories
mkdir -p "$DATA_DIR"
mkdir -p "$DATA_DIR/cache"
mkdir -p "$WORKSPACE_DIR/memory"
mkdir -p "$AGENTS_DIR"

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
chown -R 1000:1000 "$DATA_DIR" "$WORKSPACE_DIR" "$AGENTS_DIR" 2>/dev/null || true

# Generate a random SearXNG secret_key if still set to the placeholder
SEARXNG_SETTINGS="$STATIC_DIR/searxng/settings.yml"
if grep -q 'REPLACE_ME_DURING_SETUP' "$SEARXNG_SETTINGS" 2>/dev/null; then
    SEARXNG_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i.bak "s/REPLACE_ME_DURING_SETUP/$SEARXNG_SECRET/" "$SEARXNG_SETTINGS"
    rm -f "${SEARXNG_SETTINGS}.bak"
    echo "  Generated SearXNG secret_key."
fi

# Generate a random Conduit registration token if still set to the placeholder
CONDUIT_TOML="$STATIC_DIR/conduit.toml"
if grep -q 'REPLACE_ME_DURING_SETUP' "$CONDUIT_TOML" 2>/dev/null; then
    REG_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    sed -i.bak "s/REPLACE_ME_DURING_SETUP/$REG_TOKEN/" "$CONDUIT_TOML"
    rm -f "${CONDUIT_TOML}.bak"
    echo "  Generated Conduit registration token."

    # Persist the token so the agent manager can register sub-agent users at runtime
    ENV_FILE="$SCRIPT_DIR/.env"
    if [[ -f "$ENV_FILE" ]]; then
        if ! grep -q 'MEMTRIX_SECRET_REGISTRATION_TOKEN' "$ENV_FILE"; then
            echo "" >> "$ENV_FILE"
            echo "# Conduit registration token (used by agent manager)" >> "$ENV_FILE"
            echo "MEMTRIX_SECRET_REGISTRATION_TOKEN=$REG_TOKEN" >> "$ENV_FILE"
        fi
    else
        echo "# Conduit registration token (used by agent manager)" > "$ENV_FILE"
        echo "MEMTRIX_SECRET_REGISTRATION_TOKEN=$REG_TOKEN" >> "$ENV_FILE"
    fi
fi

# Build the Docker image
echo "Building Docker image..."
docker compose build

# Start Conduit so it's available for the onboarding wizard
echo ""
echo "Starting Conduit homeserver..."
docker compose up -d conduit

# Wait for Conduit to be ready
echo "Waiting for Conduit to become available..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:6167/_matrix/client/versions > /dev/null 2>&1; then
        echo "  Conduit is ready."
        break
    fi
    if [[ $i -eq 60 ]]; then
        echo "  Error: Conduit did not start within 30 seconds."
        exit 1
    fi
    sleep 1
done

echo ""
echo "All done! Run the Onboarding Wizard with:  ./onboard.sh"
