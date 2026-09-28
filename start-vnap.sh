#!/bin/bash
# Start the RSU/OBU pair defined in docker-compose.yml (image vnap:latest) and follow the RSU log.

set -a
source docker.env

if [[ ! -d $EXEC_DIR/vnap-certs ]]; then
  cp -r ./vnap-certs $EXEC_DIR
fi
if [[ ! -f $EXEC_DIR/docker-compose.yml ]]; then
  cp docker-compose.yml $EXEC_DIR
fi

cd $EXEC_DIR
if [[ "$(docker network ls -f"name=vanetzalan0" -q)" == "" ]]; then
  docker network create vanetzalan0 --subnet 192.168.98.0/24
fi
docker-compose up -d
echo; echo "Watching RSU log..."
docker-compose logs -f rsu
