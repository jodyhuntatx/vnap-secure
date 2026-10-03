#!/bin/sh
# ID management events on the pseudonym control channel (ETSI TS 102 723-8/-9 clauses 5.2.8-5.2.10).
# usage: ctl lock    <station id> [seconds 0..255, default 30] [reason]   ID-LOCK: no ID changes
#        ctl unlock  <station id> <lock handle>                          ID-UNLOCK
#        ctl trigger <station id> [reason]                               IDCHANGE-TRIGGER
# The station answers on <CONTROL_TOPIC>/<station>/status (see docker logs of the client), e.g.
# {"result":"locked","lock_handle":3,"locked_for_s":30}. Pseudonym changes: see "change".
action=$1 station=$2
[ -n "$action" ] && [ -n "$station" ] || { sed -n '3,5p' "$0" >&2; exit 1; }
case "$action" in
    lock)
        seconds=${3:-30} reason=${4:-lock}
        case "$seconds" in ''|*[!0-9]*) echo "seconds must be 0..255" >&2; exit 1 ;; esac
        payload="{\"action\":\"lock\",\"duration\":$seconds,\"reason\":\"$reason\"" ;;
    unlock)
        handle=$3
        case "$handle" in ''|*[!0-9]*) echo "unlock needs the lock handle" >&2; exit 1 ;; esac
        payload="{\"action\":\"unlock\",\"lock_handle\":$handle" ;;
    trigger)
        reason=${3:-trigger}
        payload="{\"action\":\"trigger\",\"reason\":\"$reason\"" ;;
    *)
        echo "unknown action $action (lock, unlock, trigger)" >&2; exit 1 ;;
esac
case "$payload" in *\\*) echo "no backslashes please" >&2; exit 1 ;; esac

counter_file=/tmp/pseudo-ctl-events
n=$(( $(cat $counter_file 2>/dev/null || echo 0) + 1 )); echo $n > $counter_file
payload="$payload,\"event_id\":\"$(hostname)-$n\"}"
set -- -h "${CONTROL_BROKER:-pseudo-broker}" -p "${CONTROL_PORT:-1883}" -q 1 \
    -t "${CONTROL_TOPIC:-vnap/pseudonym}/$station/change" -m "$payload"
[ -n "$CONTROL_USERNAME" ] && set -- "$@" -u "$CONTROL_USERNAME" -P "$CONTROL_PASSWORD"
mosquitto_pub "$@" && echo "$(date -u +%H:%M:%S) $action -> station $station: $payload"
