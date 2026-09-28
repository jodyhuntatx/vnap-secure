#!/bin/bash
VM_IP=172.16.93.134
scp -i ~/.ssh/id_jody_git -r demo@$VM_IP:/home/demo/vanetza-nap ~
