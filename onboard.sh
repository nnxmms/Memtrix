#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker run --rm -it \
  --network memtrix_matrix \
  -v "$SCRIPT_DIR/data:/home/memtrix/data" \
  -v "$SCRIPT_DIR/workspace:/home/memtrix/workspace" \
  memtrix python -m src.onboarding
