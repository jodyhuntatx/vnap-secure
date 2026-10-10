#!/bin/bash
# Start the API on 0.0.0.0:8080 (put a TLS reverse proxy in front; see README.md).
# Dependencies go into a virtualenv (.venv) or, where python3-venv is missing, into .deps;
# they are installed again whenever requirements.txt changes. A virtualenv without pip (made
# while python3-venv was missing) is created again.
set -e
cd "$(dirname "$0")"
if .venv/bin/python -m pip --version >/dev/null 2>&1 || python3 -m venv --clear .venv 2>/dev/null; then
    PY=.venv/bin/python
    [ .venv/.installed -nt requirements.txt ] || { $PY -m pip install -q -r requirements.txt && touch .venv/.installed; }
else
    rm -rf .venv
    PY=python3
    [ .deps/.installed -nt requirements.txt ] || { python3 -m pip install -q --target .deps -r requirements.txt && touch .deps/.installed; }
    export PYTHONPATH=$PWD/.deps
fi
exec $PY -m uvicorn vnapapi.app:app --host "${VNAP_API_HOST:-0.0.0.0}" --port "${VNAP_API_PORT:-8080}" --proxy-headers
