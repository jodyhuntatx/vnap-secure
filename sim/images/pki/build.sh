#!/bin/bash
# Build the vnap-pki image: this directory plus C-ITS-PKI's src/. C-ITS-PKI is found as in
# vnapctl: CITS_PKI_DIR, else the git submodule external/C-ITS-PKI, else a checkout next to
# vnap-secure. The context goes to docker on stdin (snap docker cannot read /mnt/hgfs).
# vnapctl builds this image itself when a scenario needs it (tagged by the sources' digest).
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
if [ -n "$CITS_PKI_DIR" ]; then CITS=$CITS_PKI_DIR
elif [ -f "$REPO/external/C-ITS-PKI/src/pki.py" ]; then CITS=$REPO/external/C-ITS-PKI
else CITS=$REPO/../C-ITS-PKI
fi
[ -f "$CITS/src/pki.py" ] || { echo "C-ITS-PKI not found at $CITS: run 'git submodule update --init' (or set CITS_PKI_DIR)" >&2; exit 1; }
VERSION=$(git -c safe.directory='*' -C "$CITS" rev-parse --short HEAD 2>/dev/null || true)
tar -c -C "$HERE" --exclude=__pycache__ Dockerfile pki_service.py \
    -C "$CITS" --exclude=__pycache__ --transform 's,^src,cits-pki/src,' src \
  | docker build -q ${VERSION:+--label vnap.cits_pki=$VERSION} -t "${1:-vnap-pki}" -
