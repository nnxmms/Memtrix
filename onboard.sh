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
