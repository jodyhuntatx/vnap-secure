# vnap-secure: build, test and documentation targets (run in the development VM).
#   make image         station image (patches/vanetza-nap over the upstream base) -> vnap:latest
#   make origs         the same image from the unpatched upstream sources, for comparison
#   make pki-image     per-run PKI image (vnapctl also builds it on demand)
#   make msgcheck      offline message checker vnap:msgcheck (after make image)
#   make test          all tests that need no simulation: sim, service, UI syntax, OpenAPI, diffs, docs
#   make diffs         regenerate patches/vanetza-nap/diffs/ after editing the patch set
#   make openapi       regenerate docs/reference/openapi.json after changing the API
#   make submodule     fetch C-ITS-PKI (external/C-ITS-PKI) at the pinned commit
# Variables: IMAGE (default vnap:latest), VANETZA_NAP_DIR (default ~/vanetza-nap), CITS_PKI_DIR.

SHELL := /bin/bash
SERVICE_PY := $(shell [ -x service/.venv/bin/python ] && echo .venv/bin/python || echo "PYTHONPATH=.deps python3")

.PHONY: image origs pki-image msgcheck test test-sim test-service service-deps test-ui openapi openapi-check diffs diffs-check docs-check submodule

image:
	scripts/build/docker-build.sh

origs:
	scripts/build/docker-build.sh origs

pki-image:
	sim/images/pki/build.sh

msgcheck:
	sim/images/msgcheck/build-msgcheck.sh

test: test-sim test-service test-ui openapi-check diffs-check docs-check

test-sim:
	cd sim && python3 -m unittest discover -s tests

# needs the packages of service/requirements.txt: installed into service/.venv (or service/.deps)
# the same way service/run.sh does, whenever requirements.txt changed
test-service: service-deps
	cd service && $(SERVICE_PY) -m unittest discover -s tests

service-deps:
	@cd service && if [ -x .venv/bin/python ] || python3 -m venv .venv 2>/dev/null; then \
	  [ .venv/.installed -nt requirements.txt ] || { .venv/bin/python -m pip install -q -r requirements.txt && touch .venv/.installed; }; \
	else \
	  [ .deps/.installed -nt requirements.txt ] || { python3 -m pip install -q --target .deps -r requirements.txt && touch .deps/.installed; }; \
	fi

# JavaScript syntax of the web UI (node in a container; no build step)
test-ui:
	tar -C service/ui -c js | docker run --rm -i node:20-alpine sh -c \
	  'mkdir /w && tar -C /w -x && for f in /w/js/*.js /w/js/views/*.js; do node --check "$$f" || exit 1; done && echo "UI syntax ok"'

# the API's OpenAPI description, as the service serves it at /api/openapi.json
openapi: service-deps
	cd service && $(SERVICE_PY) -m vnapapi.openapi

openapi-check: service-deps
	cd service && $(SERVICE_PY) -m vnapapi.openapi --check

diffs:
	scripts/build/gen-diffs.sh

diffs-check:
	scripts/build/gen-diffs.sh --check

docs-check:
	scripts/docs-check.py

submodule:
	git submodule update --init
