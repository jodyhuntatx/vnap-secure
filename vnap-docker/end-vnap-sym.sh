#!/bin/bash

set -a
source docker.env

docker rm -f obu rsu
docker network rm vanetzalan0