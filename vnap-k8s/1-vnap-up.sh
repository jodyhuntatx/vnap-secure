#!/bin/bash

kubectl create configmap rsu-env --from-env-file=rsu.env
kubectl apply -f rsu-deploy-vlan.yaml

kubectl create configmap obu-env --from-env-file=obu.env
kubectl apply -f obu-deploy-vlan.yaml
