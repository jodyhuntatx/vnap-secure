#!/bin/bash
docker rm -f obu rsu pseudo-client pseudo-broker 2>/dev/null
docker network rm vanetzalan0
docker network inspect vnapctl0 >/dev/null 2>&1 && docker network rm vnapctl0
