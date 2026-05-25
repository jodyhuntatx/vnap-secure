#!/bin/bash

docker run -d \
  --name vnap-tools \
  --volume /Users/josephhunt/COIMBRA/vnap-secure/vnap-certs:/vnap-certs \
  --entrypoint /bin/sleep \
  vnap:latest \
  infinity
docker exec -it vnap-tools bash
docker rm -f vnap-tools &> /dev/null || true
