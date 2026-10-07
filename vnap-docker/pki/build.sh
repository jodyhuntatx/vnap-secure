#!/bin/bash
# Build the vnap-pki image: this directory plus C-ITS-PKI's src/ (CITS_PKI_DIR, default: the
# C-ITS-PKI checkout next to vnap-secure). The context goes to docker on stdin (snap docker
# cannot read /mnt/hgfs).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
CITS=${CITS_PKI_DIR:-$HERE/../../../C-ITS-PKI}
[ -f "$CITS/src/pki.py" ] || { echo "C-ITS-PKI not found at $CITS (set CITS_PKI_DIR)" >&2; exit 1; }
tar -c -C "$HERE" --exclude=__pycache__ Dockerfile pki_service.py \
    -C "$CITS" --exclude=__pycache__ --transform 's,^src,cits-pki/src,' src \
  | docker build -q -t "${1:-vnap-pki}" -
