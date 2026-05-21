#!/bin/bash

# Create cluster with host directory mounts
kind create cluster --config ./kind-config.yaml

# Push images to cluster registry
kind load docker-image ghcr.io/k8snetworkplumbingwg/multus-cni:snapshot-thick -n vnap
kind load docker-image vnap:latest -n vnap

# Install bridge CNI plugin
if [ ! -f cni-plugins.tgz ]; then
  curl -L https://github.com/containernetworking/plugins/releases/download/v1.9.1/cni-plugins-linux-arm-v1.9.1.tgz -o cni-plugins.tgz
fi
docker cp cni-plugins.tgz vnap-control-plane:/opt/cni/bin
docker exec -it vnap-control-plane bash -c "cd /opt/cni/bin && tar xvf cni-plugins.tgz"

# Install Multus CNI
kubectl apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset-thick.yml
kubectl get pods -n kube-system | grep multus

#docker exec -it vnap-control-plane bash -c "ip link add vanetzalan0 type bridge && ip link set vanetzalan0 up"

# Create vanetzalan0 as bridge network
#kubectl apply -f vanetzalan0.yaml
kubectl apply -f vlan-detail.yaml
kubectl get network-attachment-definitions

