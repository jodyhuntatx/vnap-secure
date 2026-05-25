#!/bin/bash

set -a
source docker.env

if [[ ! -d $EXEC_DIR/vnap-certs ]]; then
  cp -r ../vnap-certs $EXEC_DIR
fi

docker network create vanetzalan0 --subnet=192.168.98.0/24

docker run -d \
    --name rsu \
    --restart always \
    --volume $EXEC_DIR/vnap-certs:/vnap-certs \
    --network vanetzalan0 \
    --ip 192.168.98.10 \
    --cap-add "NET_ADMIN" \
    --env-file rsu-certify.env \
    vnap:latest

docker run -d \
    --name obu \
    --restart always \
    --volume $EXEC_DIR/vnap-certs:/vnap-certs \
    --network vanetzalan0 \
    --ip 192.168.98.20 \
    --cap-add "NET_ADMIN" \
    --env-file obu.env \
    vnap:latest
