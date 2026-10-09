#!/bin/bash
# Shell in a vnap:latest container with the repository's certs/ mounted at /vnap-certs, for
# Vanetza's certify tool (see gen-certify.sh). IMAGE overrides the image.
REPO=$(cd "$(dirname "$0")/../.." && pwd)
docker run -d --name vnap-tools --volume "$REPO/certs":/vnap-certs --entrypoint /bin/sleep \
  "${IMAGE:-vnap:latest}" infinity
docker exec -it vnap-tools bash
docker rm -f vnap-tools &> /dev/null || true
