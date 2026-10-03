#!/bin/bash
# Stop the RSU/OBU pair started by start-vnap.sh and remove the vanetzalan0 network.

source docker.env

cd $EXEC_DIR && docker-compose down -v &> /dev/null  # -v: anonymous volumes too (vnap-certs is a bind mount)
docker network rm vanetzalan0 &> /dev/null
