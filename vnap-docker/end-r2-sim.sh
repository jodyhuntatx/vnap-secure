#!/bin/bash
docker rm -f -v obu rsu pseudo-client pseudo-broker eavesdropper 2>/dev/null  # -v: anonymous volumes too
docker network rm vanetzalan0
docker network inspect vnapctl0 >/dev/null 2>&1 && docker network rm vnapctl0
