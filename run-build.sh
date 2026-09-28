#!/bin/bash
VM_IP=172.16.93.134
ssh -i ~/.ssh/id_jody_git demo@$VM_IP /home/demo/COIMBRA/vnap-secure/docker-build.sh
ssh -i ~/.ssh/id_jody_git demo@$VM_IP docker save vnap:latest -o /home/demo/saved/vnap.tar
scp -i ~/.ssh/id_jody_git demo@$VM_IP:/home/demo/saved/vnap.tar .
docker load -i vnap.tar
