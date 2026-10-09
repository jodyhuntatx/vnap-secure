#!/bin/bash
# Show every patched file side by side with its upstream original (for reading in a terminal;
# patches/vanetza-nap/diffs/ has the same as unified diffs, see gen-diffs.sh).
# Files in patches/vanetza-nap/{modified,upstream}/ use '__' as path separator in their names.
cd "$(dirname "$0")/../../patches/vanetza-nap"
line() { printf "$1%.0s" $(seq 1 "${COLUMNS:-$(tput cols 2>/dev/null || echo 100)}"); echo; }

for patch in modified/*; do
  name=$(basename "$patch")
  line '='
  echo "### ${name//__//}"
  if [[ -f "upstream/$name" ]]; then
    sdiff -s "upstream/$name" "$patch"
  else
    echo "(new file, not in upstream)"
  fi
done
line '='
