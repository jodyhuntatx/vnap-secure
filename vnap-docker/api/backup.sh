#!/bin/bash
# Backups of the service's data (see vnapapi/backup.py):
#   ./backup.sh create | list | verify <archive> | restore <archive> [--force]
# Restore only with the service stopped (systemctl stop vnap-api).
set -e
cd "$(dirname "$0")"
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec $PY -m vnapapi.backup "$@"
