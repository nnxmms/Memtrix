#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker run --rm -it \
  --network memtrix_matrix \
  -v "$SCRIPT_DIR/data:/home/memtrix/data" \
  -v "$SCRIPT_DIR/workspace:/home/memtrix/workspace" \
  memtrix python -m src.app.onboarding

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

# Decide whether to run the bundled local Conduit homeserver based on the chosen
# main channel. Only matrix channels marked "managed" use the local Conduit.
CONFIG_JSON="$SCRIPT_DIR/data/config.json"
USE_LOCAL=$(python3 -c "
import json
try:
    c = json.load(open('$CONFIG_JSON'))
    ch = c['main-agent'].get('channel', '')
    cfg = c.get('channels', {}).get(ch, {})
    print('true' if cfg.get('type') == 'matrix' and cfg.get('managed', True) else 'false')
except Exception:
    print('false')
" 2>/dev/null || echo false)

if [[ -f "$ENV_FILE" ]]; then
    # Drop any previous COMPOSE_PROFILES line before re-setting it
    grep -v '^COMPOSE_PROFILES=' "$ENV_FILE" > "$ENV_FILE.tmp" 2>/dev/null && mv "$ENV_FILE.tmp" "$ENV_FILE" || true
fi

if [[ "$USE_LOCAL" == "true" ]]; then
    echo "COMPOSE_PROFILES=local" >> "$ENV_FILE"
    echo "✓ Using the bundled local Conduit homeserver."
else
    echo "✓ Using an external Matrix homeserver — the bundled Conduit will not run."
    docker compose stop conduit >/dev/null 2>&1 || true
fi

