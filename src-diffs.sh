#!/bin/bash
# Show every vnap-secure patch as a side-by-side diff against the upstream original.
# vnap-patches/ and vnap-origs/ use '__' as path separator in file names.

cd "$(dirname "$0")"
line() { printf "$1%.0s" $(seq 1 "${COLUMNS:-$(tput cols 2>/dev/null || echo 100)}"); echo; }

for patch in vnap-patches/*; do
  name=$(basename "$patch")
  line '='
  echo "### ${name//__//}"
  if [[ -f "vnap-origs/$name" ]]; then
    sdiff -s "vnap-origs/$name" "$patch"
  else
    echo "(new file, not in upstream)"
  fi
done
line '='
