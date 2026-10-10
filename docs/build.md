# Build

## Station image

```bash
cd ${VNAP_HOME}
make image                                     # = scripts/build/docker-build.sh
IMAGE=vnap:r2-p20 make image                   # another tag
VANETZA_NAP_DIR=~/vanetza-test make image      # another vanetza-nap tree
make origs                                     # the unpatched upstream sources, for comparison
```

`scripts/build/docker-build.sh`:

1. **Base.** `patches/vanetza-nap/BASE` names the upstream repository, branch and commit
   (`release2-main` @ `241438fd`). If `VANETZA_NAP_DIR` (default `~/vanetza-nap`) does not
   exist, the script clones that commit there.
   - An existing tree must contain the base commit, for example upstream at `BASE` or a branch
     built on it. Anything else is refused, not reset.
2. **Patch set.** It copies `patches/vanetza-nap/modified/` over the tree: each file is named
   after its path with `/` written as `__`, e.g. `tools__socktap__time_trigger.cpp`.
   `make origs` copies `upstream/` instead, restoring the original files. Files the patch
   set adds stay.
3. **Build.** `docker build --network=host -t $IMAGE .` in the tree. The first build takes
   about 20 minutes and needs about 10 GB of build cache; later builds reuse the cached
   dependency layers. `NO_BUILD=1` stops after preparing the sources.

The vanetza-nap tree must be on the VM's own file system (see [installation](installation.md)).
A fresh clone with the patch set is byte-identical to the patched branch the current image
was built from (checked during the reorganization; see [CHANGELOG](../CHANGELOG.md)).

**Check the result:**

```bash
docker run --rm --entrypoint /usr/local/bin/socktap vnap:latest --help | grep -E 'pseudonym-control|pki-refill'
```

## Image tags

- **Tags:** `vnap:latest` is the newest build. Measurements use a fixed tag (`vnap:r2-pNN`), so
  results can name their image. Tag a build with `docker tag vnap:latest vnap:r2-pNN`, and add
  the tag to [CHANGELOG.md](../CHANGELOG.md).
- **Users of the service** may choose only the images in `sim/policy.toml`
  (`approved_images`). Add a new tag there when users should be able to select it.

## Helper images

`vnapctl` builds these from `sim/images/` when a scenario needs them. Each is tagged with the
digest of its sources (`<image>:<16 hex digits>`, plus `:latest`), so different checkouts
never overwrite each other's images and a source change rebuilds on the next run.

| Image | Sources | Used for |
|---|---|---|
| `vnap-pseudo-ctl` | `sim/images/pseudo-ctl/` | control broker and pseudonym event client |
| `vnap-mobility` | `sim/images/mobility/` | mobility client (routes, crossings, mix zones) |
| `vnap-eavesdropper` | `sim/images/eavesdropper/` | passive tracking attacker |
| `vnap-pki` | `sim/images/pki/` + C-ITS-PKI `src/` | the run's own PKI; the image label `vnap.cits_pki` records the C-ITS-PKI commit |

- **`make pki-image`** builds `vnap-pki:latest` by hand (`sim/images/pki/build.sh`), with
  C-ITS-PKI found as `vnapctl` finds it: `CITS_PKI_DIR`, then the submodule.
- **`make msgcheck`** builds `vnap:msgcheck`, the offline message checker. It compiles
  `sim/images/msgcheck/vnap-msgcheck.cpp` against the same vanetza-nap tree, so build the
  station image first. See [troubleshooting](operations/troubleshooting.md).

## Patch review and diffs

```bash
make diffs                     # regenerate patches/vanetza-nap/diffs/ (one unified diff per file)
make diffs-check               # part of make test: diffs/ matches modified/ and upstream/
scripts/build/src-diffs.sh     # side-by-side view in a terminal
```

`diffs/` is generated: edit `modified/`, then run `make diffs`. What each patch does and why
is in the [patch documentation](patches/README.md).

The same patches are committed as a series on the local vanetza-nap branch `jodyhuntatx`,
which is not published. A tree on that branch already contains them, so `make image` on it
changes nothing.

## Tests

```bash
make test           # all of the following
make test-sim       # vnapsim unit tests (no docker)
make test-service   # service unit tests (no docker; installs service/requirements.txt first)
make test-ui        # JavaScript syntax of the web UI (node in a container)
make openapi-check  # docs/reference/openapi.json matches the API (regenerate with make openapi)
make diffs-check
make docs-check     # relative links and anchors in all Markdown files
```

Station behaviour is tested with live runs: a scenario, then `vnapctl check` with the
scenario's expectations. C-ITS-PKI changes are tested from its side by
`tests/test_vnap_secure_interface.py`, and here by a `[pki]` scenario (see
[architecture](architecture.md#dependency-on-c-its-pki)).
