# Installation

How to prepare a development machine, from a fresh VM to the first build. The
[README](../README.md) has the short version.

## Why a Linux VM

Vanetza-NAP has file names that differ only by case (e.g. `vanetza/asn1/its/r2/actionid.c`
and `ActionID.c`). It cannot be checked out or built on a case-insensitive file system such as
the default macOS one. Development therefore uses an Ubuntu 24.04 VM (VMware on a MacBook);
every Docker image is built in the VM.

## 1. Install the prerequisite packages

On a fresh Ubuntu 24.04 VM, install `git`, `make`, `python3.12-venv` and Docker:

```bash
sudo apt update
sudo apt install -y git make python3.12-venv docker.io
sudo usermod -aG docker $USER       # then log out and in again, so docker runs without sudo
```

- **`python3.12-venv`** provides the `venv` module and pip for the virtual environment of
  [step 5](#5-set-up-the-python-virtual-environment); Ubuntu's `python3` has neither.
- **Docker** from another source works too: Docker's own apt repository (`docker-ce`), or snap,
  with the limits in [step 4](#4-sharing-the-repository-with-the-host-optional).
- **`jq`** is optional: the monitoring and API examples in this documentation use it.

## 2. Prepare the VM

A full station image build needs about 10 GB of Docker build cache. Grow the VM's disk in
VMware, then extend the root volume:

```bash
scripts/vm/extend-docker-fs.sh      # lvextend -l +100%FREE -r on the root logical volume
```

Without LVM, use `growpart`, `pvresize` and `lvextend -r` by hand.

## 3. Get the sources

```bash
git clone --recurse-submodules git@github.com:jodyhuntatx/vnap-secure.git ~/COIMBRA/vnap-secure
```

- **C-ITS-PKI** comes with it, as the submodule `external/C-ITS-PKI` at the tested commit.
  After a `git pull` that moved the submodule, or in a clone made without
  `--recurse-submodules`, run `make submodule` (`git submodule update --init`). See
  [architecture](architecture.md#dependency-on-c-its-pki).
- **vanetza-nap** is not cloned by hand: `make image` clones the upstream base into
  `~/vanetza-nap` (or `VANETZA_NAP_DIR`) the first time. See [build](build.md).

## 4. Sharing the repository with the host (optional)

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

## 5. Set up the Python virtual environment

The API service's tests need the packages of `service/requirements.txt`. They go into the
virtual environment `service/.venv`, not into the system Python:

```bash
cd ~/COIMBRA/vnap-secure
make service-deps                            # creates service/.venv (with pip) and installs requirements.txt
```

By hand, the same is:

```bash
cd ~/COIMBRA/vnap-secure/service
python3 -m venv .venv                        # needs python3.12-venv (step 1); puts pip into .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

- **`make test` runs `make service-deps` first**, so the packages are in place before the
  service tests start. It installs again whenever `requirements.txt` changes.
- **A `.venv` without pip** ("No module named pip") was created while `python3.12-venv` was
  missing. Install the package (step 1) and run `make service-deps`: it creates the
  environment again.
- **Check:** `service/.venv/bin/python -m pip list` shows `fastapi`, `uvicorn` and the others.

## 6. First build and smoke test

```bash
cd ~/COIMBRA/vnap-secure
make image                                   # about 20 minutes the first time
make test                                    # unit tests, UI syntax, patch diffs, documentation links
cd sim && ./vnapctl up c-its-pki && ./vnapctl check && ./vnapctl down
```

`./vnapctl check` should end with `PASS`.
