#!/bin/bash

set -a
source docker.env

CERTS_DIR=$(realpath "$EXEC_DIR/../vnap-certs")

docker network create vanetzalan0 --subnet=192.168.98.0/24

docker run -d \
    --name rsu \
    --restart always \
    --volume $CERTS_DIR:/vnap-certs \
    --network vanetzalan0 \
    --ip 192.168.98.10 \
    --cap-add "NET_ADMIN" \
    --env-file rsu-c-its.env \
    vnap:latest

docker run -d \
    --name obu \
    --restart always \
    --volume $CERTS_DIR:/vnap-certs \
    --network vanetzalan0 \
    --ip 192.168.98.20 \
    --cap-add "NET_ADMIN" \
    --env-file obu-c-its.env \
    vnap:latest
