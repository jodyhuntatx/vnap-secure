#!/bin/bash
# Count CAMs each station publishes on vanetza/out/cam during a window, and tally the
# per-message "security_report" (verification result) that release2 adds to the JSON.
# NOTE: socktap hardcodes Non_Strict decap handling, so CAMs that FAIL verification are
# still published -- the security_report tally, not the CAM count, is the pass/fail signal.
# usage: ./check-r2-cams.sh [seconds]

SECS=${1:-10}

for st in rsu:192.168.98.10 obu:192.168.98.20; do
  name=${st%%:*}; ip=${st#*:}
  out=$(docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
        mosquitto_sub -h $ip -t vanetza/out/cam -W $SECS 2>/dev/null)
  n=$(printf '%s\n' "$out" | grep -c '"stationID"')
  ids=$(printf '%s\n' "$out" | grep -o '"stationID": *[0-9]*' | grep -o '[0-9]*$' | sort | uniq -c | tr -s ' ' | tr '\n' ';')
  reports=$(printf '%s\n' "$out" | grep -o '"secured":[a-z]*\|"description":"[A-Za-z_]*"' | sed 's/"description"://; s/"//g' | sort | uniq -c | tr -s ' ' | tr '\n' ';')
  echo "$name received $n CAMs in ${SECS}s  (count stationID: ${ids:-none})  security: ${reports:-none}"
done
