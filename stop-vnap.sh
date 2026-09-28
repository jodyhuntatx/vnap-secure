#!/bin/bash
# Stop the RSU/OBU pair started by start-vnap.sh and remove the vanetzalan0 network.

source docker.env

cd $EXEC_DIR && docker-compose down &> /dev/null
docker network rm vanetzalan0 &> /dev/null
