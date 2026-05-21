#!/bin/bash

# Mounts host folders defined in VM's System Settings->Sharing config

# install vmware tools if not already
if [[ "$(which vmhgfs-fuse)" == "" ]]; then
  sudo apt update
  sudo apt install open-vm-tools open-vm-tools-desktop -y
fi

# create mount point if needed
sudo mkdir -p /mnt/hgfs

# mount all shared folders from host at mount point
sudo vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other

# verify mount
echo "Mounted at /mnt/hgfs:"
MOUNTED=$(ls /mnt/hgfs)
echo $MOUNTED
echo; echo "For convenience you can create soft links to these directories."
