#!/bin/bash
# In the VM, after growing its disk: extend the root logical volume (and file system) to all
# free space. Docker needs about 10 GB of build cache for the station image.
sudo lvextend -l +100%FREE -r /dev/mapper/ubuntu--vg-ubuntu--lv
