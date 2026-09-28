#!/bin/bash
# Build the vnap:latest image from ~/vanetza-nap with the vnap-secure patch set applied.
#
#   ./docker-build.sh           copy vnap-patches/* into ~/vanetza-nap, then build
#   ./docker-build.sh origs     copy vnap-origs/* (upstream release2-main) instead,
#                               restoring the original sources, then build
#
# Files in vnap-patches/ and vnap-origs/ are named after their path in vanetza-nap with
# '/' replaced by '__' (e.g. tools__socktap__time_trigger.cpp). The same patches are
# also committed on the vanetza-nap 'jodyhuntatx' branch, so checking out that branch
# is equivalent to running this script without arguments.
#
# Must run inside the VM (paths are relative to the VM user's home).

BUILD_DIR=~/COIMBRA/vnap-secure
VNAP_REPO=~/vanetza-nap

copy_set() {
  local set_dir="$BUILD_DIR/$1"
  echo "Copying $1 into $VNAP_REPO..."
  for f in "$set_dir"/*; do
    dest="$VNAP_REPO/$(basename "$f" | sed 's|__|/|g')"
    mkdir -p "$(dirname "$dest")"
    cp "$f" "$dest"
  done
}

main() {
  if [[ "$1" != "" ]]; then
    copy_set vnap-origs
  else
    copy_set vnap-patches
  fi
  cd "$VNAP_REPO" && docker build --network=host -t vnap:latest .
}

main "$@"
