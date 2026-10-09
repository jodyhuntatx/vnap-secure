#!/bin/bash
# Start the API on 0.0.0.0:8080 (put a TLS reverse proxy in front; see README.md).
# Dependencies go into a virtualenv (.venv) or, where python3-venv is missing, into .deps;
# they are installed again whenever requirements.txt changes.
set -e
cd "$(dirname "$0")"
if [ -x .venv/bin/python ] || python3 -m venv .venv 2>/dev/null; then
    PY=.venv/bin/python
    [ .venv/.installed -nt requirements.txt ] || { $PY -m pip install -q -r requirements.txt && touch .venv/.installed; }
else
    rm -rf .venv
    PY=python3
    [ .deps/.installed -nt requirements.txt ] || { python3 -m pip install -q --target .deps -r requirements.txt && touch .deps/.installed; }
    export PYTHONPATH=$PWD/.deps
fi
exec $PY -m uvicorn vnapapi.app:app --host "${VNAP_API_HOST:-0.0.0.0}" --port "${VNAP_API_PORT:-8080}" --proxy-headers
