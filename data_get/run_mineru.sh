#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname -- "$SCRIPT_DIR")"
PYTHON_BIN="${MINERU_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi
# Homebrew Python 3.10 on this Mac needs Homebrew's current libexpat.
if [[ "$(uname -s)" == "Darwin" && -f /opt/homebrew/opt/expat/lib/libexpat.1.dylib ]]; then
  export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
fi
exec "$PYTHON_BIN" "$SCRIPT_DIR/mineru_pdf_to_md.py" "$@"
