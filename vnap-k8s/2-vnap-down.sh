#!/bin/bash
kubectl delete -f rsu-deploy-vlan.yaml
kubectl delete configmap rsu-env

kubectl delete -f obu-deploy-vlan.yaml
kubectl delete configmap obu-env
