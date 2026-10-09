#!/bin/bash
# On the host: copy the VM's vanetza-nap tree (the one the image was built from) to $HOME.
# VM_IP, VM_USER, SSH_KEY and VANETZA_NAP_DIR (in the VM) override the defaults.
scp -i "${SSH_KEY:-$HOME/.ssh/id_jody_git}" -r \
  "${VM_USER:-demo}@${VM_IP:-172.16.93.134}:${VANETZA_NAP_DIR:-vanetza-nap}" ~
