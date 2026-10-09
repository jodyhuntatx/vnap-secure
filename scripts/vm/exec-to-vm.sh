#!/bin/bash
# On the host: ssh into the development VM. VM_IP, VM_USER and SSH_KEY override the defaults.
ssh -i "${SSH_KEY:-$HOME/.ssh/id_jody_git}" "${VM_USER:-demo}@${VM_IP:-172.16.93.134}" "$@"
