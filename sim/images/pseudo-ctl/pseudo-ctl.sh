#!/bin/sh
# Pseudonym change event channel (vnap-secure), kept apart from the V2X message path:
# its own MQTT broker on its own docker network, reachable only by the OBUs and this client.
#
# pseudo-ctl broker   MQTT broker on port CONTROL_PORT (1883). With CONTROL_USERNAME and
#                     CONTROL_PASSWORD set, anonymous access is disabled and that account is required.
# pseudo-ctl client   publishes change events on <CONTROL_TOPIC>/<station>/change and prints the
#                     stations' answers from <CONTROL_TOPIC>/+/status. Settings:
#   CONTROL_BROKER    broker host (pseudo-broker)
#   CONTROL_PORT      broker port (1883)
#   CONTROL_TOPIC     topic prefix (vnap/pseudonym)
#   STATIONS          station ids to send events to, space separated ("2")
#   MODE              periodic: every INTERVAL seconds
#                     random:   every MIN_INTERVAL..MAX_INTERVAL seconds, per station
#                     once:     one event after DELAY seconds (INDEX: pool index, default next), then idle
#                     manual:   no events of its own; use "docker exec <container> change <station> [index]"
#   INTERVAL (30), MIN_INTERVAL (10), MAX_INTERVAL (60), DELAY (5), INDEX
#   COUNT             stop sending after this many events per station (0 = unlimited)
set -e

ROLE=${1:-client}
export CONTROL_BROKER=${CONTROL_BROKER:-pseudo-broker}
export CONTROL_PORT=${CONTROL_PORT:-1883}
export CONTROL_TOPIC=${CONTROL_TOPIC:-vnap/pseudonym}

log() { echo "$(date -u +%H:%M:%S) $*"; }

run_broker() {
    conf=/tmp/pseudo-broker.conf
    printf 'listener %s 0.0.0.0\n' "$CONTROL_PORT" > $conf
    if [ -n "$CONTROL_USERNAME" ] && [ -n "$CONTROL_PASSWORD" ]; then
        mosquitto_passwd -b -c /tmp/pseudo-broker.passwd "$CONTROL_USERNAME" "$CONTROL_PASSWORD"
        chown mosquitto /tmp/pseudo-broker.passwd 2>/dev/null || true
        chmod 0600 /tmp/pseudo-broker.passwd
        printf 'allow_anonymous false\npassword_file /tmp/pseudo-broker.passwd\n' >> $conf
        log "control broker on port $CONTROL_PORT, authentication required"
    else
        printf 'allow_anonymous true\n' >> $conf
        log "control broker on port $CONTROL_PORT, anonymous access (set CONTROL_USERNAME/CONTROL_PASSWORD to restrict)"
    fi
    exec mosquitto -c $conf
}

random_between() {  # min max
    r=$(od -An -N2 -tu2 /dev/urandom | tr -d ' ')
    echo $(( $1 + r % ($2 - $1 + 1) ))
}

send_loop() {  # station
    station=$1 sent=0
    while [ "${COUNT:-0}" -eq 0 ] || [ $sent -lt "$COUNT" ]; do
        case $MODE in
            periodic) sleep "${INTERVAL:-30}" ;;
            random)   sleep "$(random_between "${MIN_INTERVAL:-10}" "${MAX_INTERVAL:-60}")" ;;
        esac
        change "$station" "" "$MODE" || true
        sent=$((sent + 1))
    done
    log "station $station: $sent event(s) sent, done"
}

run_client() {
    MODE=${MODE:-periodic}
    STATIONS=${STATIONS:-2}
    set -- -h "$CONTROL_BROKER" -p "$CONTROL_PORT" -q 1 -v -t "$CONTROL_TOPIC/+/status"
    [ -n "$CONTROL_USERNAME" ] && set -- "$@" -u "$CONTROL_USERNAME" -P "$CONTROL_PASSWORD"
    # print every answer from the stations, retrying until the broker is up
    ( while true; do
          mosquitto_sub "$@" 2>/dev/null | while read -r topic status; do log "status <- $topic: $status"; done
          sleep 1
      done ) &
    log "client mode=$MODE stations=[$STATIONS] broker=$CONTROL_BROKER:$CONTROL_PORT topic=$CONTROL_TOPIC"

    case $MODE in
        periodic|random)
            for station in $STATIONS; do send_loop "$station" & done ;;
        once)
            sleep "${DELAY:-5}"
            for station in $STATIONS; do change "$station" "$INDEX" once || true; done ;;
        manual) ;;
        *) log "unknown MODE $MODE (periodic, random, once, manual)"; exit 1 ;;
    esac
    wait
}

case $ROLE in
    broker) run_broker ;;
    client) run_client ;;
    *) exec "$@" ;;
esac
