#!/bin/bash
# On the host: build the station image in the VM, then copy it to the host's Docker.
# VM_IP, VM_USER, SSH_KEY and VM_REPO (the VM's vnap-secure checkout) override the defaults.
set -e
SSH=(ssh -i "${SSH_KEY:-$HOME/.ssh/id_jody_git}" "${VM_USER:-demo}@${VM_IP:-172.16.93.134}")
VM_REPO=${VM_REPO:-/home/demo/COIMBRA/vnap-secure}
"${SSH[@]}" "$VM_REPO/scripts/build/docker-build.sh"
"${SSH[@]}" "mkdir -p ~/saved && docker save vnap:latest -o ~/saved/vnap.tar"
scp -i "${SSH_KEY:-$HOME/.ssh/id_jody_git}" "${VM_USER:-demo}@${VM_IP:-172.16.93.134}:saved/vnap.tar" .
docker load -i vnap.tar
