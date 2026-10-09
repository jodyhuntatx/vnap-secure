#!/bin/bash
# Unified diffs of the patch set against upstream, one per file, in patches/vanetza-nap/diffs/
# (generated: edit modified/, then run this). With --check, only verify that diffs/ is current
# and that every patched file is listed in docs/patches/README.md.
set -euo pipefail
cd "$(dirname "$0")/../../patches/vanetza-nap"
OUT=diffs
[[ "${1:-}" == "--check" ]] && OUT=$(mktemp -d)
mkdir -p "$OUT"
[[ "$OUT" == diffs ]] && rm -f diffs/*.diff
for f in modified/*; do
  name=$(basename "$f"); path=${name//__//}
  if [[ -f "upstream/$name" ]]; then
    diff -u --label "a/$path" --label "b/$path" "upstream/$name" "$f" > "$OUT/$name.diff" || true
  else
    diff -u --label /dev/null --label "b/$path" /dev/null "$f" > "$OUT/$name.diff" || true
  fi
done
if [[ "${1:-}" == "--check" ]]; then
  # every patched file must be documented in the patch index
  INDEX=../../docs/patches/README.md
  missing=0
  for f in modified/*; do
    path=$(basename "$f"); path=${path//__//}
    grep -q "^| \`$path\` |" "$INDEX" || { echo "not in docs/patches/README.md: $path" >&2; missing=1; }
  done
  [[ $missing == 0 ]] || { rm -rf "$OUT"; exit 1; }
  if diff -rq diffs "$OUT" >/dev/null 2>&1; then echo "diffs/ is current; all $(ls modified | wc -l) patched files documented"; rm -rf "$OUT"
  else echo "diffs/ is out of date: run scripts/build/gen-diffs.sh" >&2; diff -rq diffs "$OUT" >&2 || true; rm -rf "$OUT"; exit 1; fi
else
  echo "wrote $(ls diffs | wc -l) diffs to patches/vanetza-nap/diffs/"
fi
