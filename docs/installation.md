# Installation

How to prepare a development machine, from a fresh VM to the first build. The
[README](../README.md) has the short version.

## Why a Linux VM

Vanetza-NAP has file names that differ only by case (e.g. `vanetza/asn1/its/r2/actionid.c`
and `ActionID.c`). It cannot be checked out or built on a case-insensitive file system such as
the default macOS one. Development therefore uses an Ubuntu 24.04 VM (VMware on a MacBook);
every Docker image is built in the VM.

## 1. Prepare the VM

- **Packages:** `git`, `make`, `python3` (≥ 3.11), `jq`, and Docker. Docker from snap works,
  with the limits below.
- **Disk:** a full station image build needs about 10 GB of Docker build cache. Grow the VM's
  disk in VMware, then extend the root volume:

  ```bash
  scripts/vm/extend-docker-fs.sh      # lvextend -l +100%FREE -r on the root logical volume
  ```

  Without LVM, use `growpart`, `pvresize` and `lvextend -r` by hand.

## 2. Get the sources

```bash
git clone --recurse-submodules git@github.com:jodyhuntatx/vnap-secure.git ~/COIMBRA/vnap-secure
```

- **C-ITS-PKI** comes with it, as the submodule `external/C-ITS-PKI` at the tested commit.
  After a `git pull` that moved the submodule, or in a clone made without
  `--recurse-submodules`, run `make submodule` (`git submodule update --init`). See
  [architecture](architecture.md#dependency-on-c-its-pki).
- **vanetza-nap** is not cloned by hand: `make image` clones the upstream base into
  `~/vanetza-nap` (or `VANETZA_NAP_DIR`) the first time. See [build](build.md).

## 3. Sharing the repository with the host (optional)

The repository can live on the host and be shared into the VM. vanetza-nap cannot: it must
stay on the VM's own file system because of its case-only file name differences.

```bash
scripts/vm/mount-shared-folder.sh     # in the VM: mount the VMware shared folders at /mnt/hgfs
ln -s /mnt/hgfs/COIMBRA ~/COIMBRA     # e.g., so the repository is at ~/COIMBRA/vnap-secure
```

- **Git on `/mnt/hgfs`** reports "dubious ownership", because the mount shows the files as
  owned by another user. Use `git -c safe.directory='*' …`, or run
  `git config --global --add safe.directory <path>` once.
- **Snap-confined Docker** reads only files under the real `$HOME`, and not in dot-directories.
  - It cannot read build contexts on `/mnt/hgfs`. The scripts therefore send build contexts on
    stdin (`tar … | docker build -`), and `sim/images/msgcheck/build-msgcheck.sh` stages its
    files in `$HOME`.
  - Bind mounts from `/mnt/hgfs` do work: `vnapctl` mounts `certs/` from there.
  - vanetza-nap's build context (`~/vanetza-nap`) is in `$HOME`, as required.

## 4. Host-side helpers (optional)

On the Mac, these scripts reach the VM over ssh. `VM_IP` (default `172.16.93.134`),
`VM_USER` (`demo`) and `SSH_KEY` (`~/.ssh/id_jody_git`) override the defaults.

| Script | Does |
|---|---|
| `scripts/vm/exec-to-vm.sh` | opens an ssh session to the VM |
| `scripts/vm/run-build.sh` | builds the station image in the VM (`VM_REPO`: the VM's checkout), then `docker save`, `scp` and `docker load` on the host |
| `scripts/vm/sync-vnap-dist.sh` | copies the VM's vanetza-nap tree to the host's `$HOME` |

## 5. First build and smoke test

```bash
cd ~/COIMBRA/vnap-secure
make image                                   # about 20 minutes the first time
make test                                    # unit tests, UI syntax, patch diffs, documentation links
cd sim && ./vnapctl up c-its-pki && ./vnapctl check && ./vnapctl down
```

`make test` installs the service's Python packages into `service/.venv` on first use.
`./vnapctl check` should end with `PASS`.
