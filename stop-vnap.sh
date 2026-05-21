#!/bin/bash

source docker.env

docker rm -f ${DIR_NAME}_rsu_1 ${DIR_NAME}_obu_1 &> /dev/null
docker network rm vanetzalan0 &> /dev/null
