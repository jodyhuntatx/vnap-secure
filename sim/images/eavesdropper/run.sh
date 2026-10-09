#!/bin/bash
# Attach the eavesdropper to an already running simulation (any harness).
# usage: eavesdropper/run.sh [network] [container name] [extra eavesdropper.py options]
#   default network vanetzalan0, name eavesdropper; logs: docker logs -f <name>,
#   docker exec <name> cat /logs/tracks.json, docker cp <name>:/logs ./eavesdropper-logs
NET=${1:-vanetzalan0}
NAME=${2:-eavesdropper}
shift 2 2>/dev/null
HERE=$(cd "$(dirname "$0")" && pwd)
# rebuild from the current sources (cached layers make this quick when nothing changed)
tar -C "$HERE" --exclude=logs --exclude=__pycache__ -c . | docker build -q -t vnap-eavesdropper - >/dev/null  # stdin: snap docker cannot read /mnt/hgfs
docker run -d --name "$NAME" --network "$NET" --cap-add NET_RAW \
  --label vnap.role=eavesdropper --label vnap.started_by=${USER:-unknown} \
  vnap-eavesdropper "$@"
