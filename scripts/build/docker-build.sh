#!/bin/bash
# Build the station image: the upstream vanetza-nap base (patches/vanetza-nap/BASE) with the
# patch set (patches/vanetza-nap/modified/) laid over it.
#
#   scripts/build/docker-build.sh           patched sources -> $IMAGE (default vnap:latest)
#   scripts/build/docker-build.sh origs     upstream originals (patches/vanetza-nap/upstream/) instead
#
# VANETZA_NAP_DIR (default ~/vanetza-nap) holds the vanetza-nap tree. If it does not exist, the
# base commit is cloned there. An existing tree must contain the base commit (e.g. upstream at
# BASE, or the 'jodyhuntatx' branch that carries the same patches as commits); anything else is
# refused rather than reset. The tree must be on the VM's own file system: vanetza-nap has file
# names that differ only by case, and snap-confined Docker reads only the real $HOME.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PATCHES=$REPO/patches/vanetza-nap
. "$PATCHES/BASE"
VNAP_REPO=${VANETZA_NAP_DIR:-$HOME/vanetza-nap}
IMAGE=${IMAGE:-vnap:latest}
git() { command git -c safe.directory='*' "$@"; }

fetch_base() {
  if [[ ! -e "$VNAP_REPO" ]]; then
    echo "Cloning $URL ($BRANCH @ ${COMMIT:0:8}) into $VNAP_REPO..."
    git clone --quiet --branch "$BRANCH" "$URL" "$VNAP_REPO"
    git -C "$VNAP_REPO" checkout --quiet --detach "$COMMIT"
  elif ! git -C "$VNAP_REPO" merge-base --is-ancestor "$COMMIT" HEAD 2>/dev/null; then
    echo "$VNAP_REPO does not contain the base commit ${COMMIT:0:8}: check it out there (or use another VANETZA_NAP_DIR)" >&2
    exit 1
  fi
}

copy_set() {
  echo "Copying $1 into $VNAP_REPO..."
  for f in "$PATCHES/$1"/*; do
    dest="$VNAP_REPO/$(basename "$f" | sed 's|__|/|g')"
    mkdir -p "$(dirname "$dest")"
    cp "$f" "$dest"
  done
}

fetch_base
if [[ "${1:-}" == "origs" ]]; then copy_set upstream; else copy_set modified; fi
[[ "${NO_BUILD:-}" == 1 ]] && { echo "sources ready in $VNAP_REPO (NO_BUILD=1)"; exit 0; }
cd "$VNAP_REPO" && docker build --network=host -t "$IMAGE" .
