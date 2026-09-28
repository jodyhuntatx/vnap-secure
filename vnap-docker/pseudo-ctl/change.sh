#!/bin/sh
# Publish one pseudonym change event.
# usage: change <station id> [pool index] [reason]
# Without an index the station changes to the next pseudonym of its pool.
# Uses CONTROL_BROKER, CONTROL_PORT, CONTROL_TOPIC, CONTROL_USERNAME, CONTROL_PASSWORD (see pseudo-ctl.sh).
[ -n "$1" ] || { echo "usage: change <station id> [pool index] [reason]" >&2; exit 1; }
station=$1 index=$2 reason=${3:-manual}
case "$index" in ''|*[!0-9]*) [ -z "$index" ] || { echo "index must be a non-negative integer" >&2; exit 1; } ;; esac
case "$reason" in *[\"\\]*) echo "reason must not contain quotes or backslashes" >&2; exit 1 ;; esac

counter_file=/tmp/pseudo-ctl-events
n=$(( $(cat $counter_file 2>/dev/null || echo 0) + 1 )); echo $n > $counter_file
event_id="$(hostname)-$n"
payload="{\"event_id\":\"$event_id\",\"reason\":\"$reason\"${index:+,\"index\":$index}}"

set -- -h "${CONTROL_BROKER:-pseudo-broker}" -p "${CONTROL_PORT:-1883}" -q 1 \
    -t "${CONTROL_TOPIC:-vnap/pseudonym}/$station/change" -m "$payload"
[ -n "$CONTROL_USERNAME" ] && set -- "$@" -u "$CONTROL_USERNAME" -P "$CONTROL_PASSWORD"
if mosquitto_pub "$@"; then
    echo "$(date -u +%H:%M:%S) event -> station $station: $payload"
else
    echo "$(date -u +%H:%M:%S) FAILED to publish event to station $station" >&2
    exit 1
fi
