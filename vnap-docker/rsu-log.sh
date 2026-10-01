#!/bin/bash
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t 'vanetza/out/#' -v

