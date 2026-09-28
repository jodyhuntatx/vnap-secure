#!/bin/bash
# Build vnap:msgcheck = the vnap runtime image plus vnap-msgcheck, compiled against the
# vanetza tree in ~/vanetza-nap (same sources and patches as the socktap image).
# The checker stages are appended to a temporary copy of vanetza's Dockerfile, so the
# (large) build stage is reused from cache and never exported as an image.
# usage: ./build-msgcheck.sh [vanetza-dir]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
VANETZA=${1:-$HOME/vanetza-nap}
# snap-confined docker can only read files under the real $HOME (not /mnt/hgfs),
# so stage the checker sources and the Dockerfile there
STAGE=$(mktemp -d -p "$HOME" vnap-msgcheck-stage.XXXX)  # not a dot-dir: snap hides those too
trap 'rm -rf "$STAGE"' EXIT
cp "$HERE/vnap-msgcheck.cpp" "$HERE/CMakeLists.txt" "$STAGE/"
DOCKERFILE=$STAGE/Dockerfile

cat "$VANETZA/Dockerfile" - > "$DOCKERFILE" <<'EOF'

FROM build AS msgcheck-build
COPY --from=msgcheck-src vnap-msgcheck.cpp CMakeLists.txt /vanetza/tools/vnap-msgcheck/
RUN grep -q 'tools/vnap-msgcheck' CMakeLists.txt || echo 'add_subdirectory(tools/vnap-msgcheck)' >> CMakeLists.txt
RUN cmake . && cmake --build . --target vnap-msgcheck -j $(nproc)

FROM final AS msgcheck
COPY --from=msgcheck-build /vanetza/bin/vnap-msgcheck /usr/local/bin/vnap-msgcheck
ENTRYPOINT ["/usr/local/bin/vnap-msgcheck"]
EOF

docker build --network host --build-context msgcheck-src="$STAGE" \
    -f "$DOCKERFILE" --target msgcheck -t vnap:msgcheck "$VANETZA"
