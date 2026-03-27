#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker run --rm -it \
  --network memtrix_matrix \
  -v "$SCRIPT_DIR/data:/home/memtrix/data" \
  -v "$SCRIPT_DIR/workspace:/home/memtrix/workspace" \
  memtrix python -m src.onboarding

# Fix ownership after onboarding may have written new files as root
chown -R 1000:1000 "$SCRIPT_DIR/data" "$SCRIPT_DIR/workspace" "$SCRIPT_DIR/agents" 2>/dev/null || true

# Move generated .env file to project root if it was created
if [ -f "$SCRIPT_DIR/data/.env.generated" ]; then
  mv "$SCRIPT_DIR/data/.env.generated" "$SCRIPT_DIR/.env"
  chmod 600 "$SCRIPT_DIR/.env"
  echo "✓ .env file has been placed in the project root."
fi

# Ensure the Conduit registration token is in .env (needed for sub-agent creation)
CONDUIT_TOML="$SCRIPT_DIR/src/static/conduit.toml"
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]] && ! grep -q 'MEMTRIX_SECRET_REGISTRATION_TOKEN' "$ENV_FILE" 2>/dev/null; then
    REG_TOKEN=$(grep 'registration_token' "$CONDUIT_TOML" 2>/dev/null | sed 's/.*= *"\(.*\)"/\1/')
    if [[ -n "$REG_TOKEN" ]]; then
        echo "" >> "$ENV_FILE"
        echo "# Conduit registration token (used by agent manager)" >> "$ENV_FILE"
        echo "MEMTRIX_SECRET_REGISTRATION_TOKEN=$REG_TOKEN" >> "$ENV_FILE"
        echo "✓ Registration token added to .env."
    fi
fi
