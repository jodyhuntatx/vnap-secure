#!/bin/bash
# Start an RSU + OBU pair from a release2-main image on vanetzalan0.
# usage: ./run-r2-sim.sh [none|certify|certify-fresh|certify-fresh-badroot|naive-v3|c-its-pki|c-its-pki-badroot|c-its-pki-pseudo] [image]
#   none          - no security (baseline: proves the link/CAM path works)
#   certify       - certs-v2 with vnap-certs/certify (both stations use ticket_vnap; AT EXPIRED 2026-06-07)
#   certify-fresh - certs-v2, certify root/AA + per-station ATs issued 2026-09-24
#   naive-v3      - certs-v3, self-generated certs per station (exercises v3 sign/verify)
#   c-its-pki     - certs-v3 with vnap-certs/c-its-pki (RSU: at, OBU: bke_at_0); -badroot: OBU trusts tlm.cert
#   c-its-pki-pseudo - like c-its-pki, but the OBU holds the pool bke_at_0..7 (SECURITY=pseudonyms) and
#                   changes pseudonym on events from the pseudonym control channel: broker "pseudo-broker"
#                   and client "pseudo-client" on the separate network vnapctl0 (image vnap-pseudo-ctl,
#                   built on demand from pseudo-ctl/). Client settings: PSEUDO_MODE (periodic|random|once|
#                   manual, default periodic), PSEUDO_INTERVAL (30), PSEUDO_RANDOM_MIN/PSEUDO_RANDOM_MAX
#                   (10/60, random mode), PSEUDO_COUNT (0 = unlimited),
#                   PSEUDO_MIN_CHANGE_MS (OBU rate limit, 1000); PSEUDO_CONTROL_USERNAME/PSEUDO_CONTROL_PASSWORD enable broker auth.
#                   Manual event: docker exec pseudo-client change 2 [index]

SCENARIO=${1:-none}
IMAGE=${2:-vnap:r2-stock}
# NATIVE=1 uses the image's own (patched) /entrypoint.sh with SECURITY=certs instead of
# mounting r2-entrypoint.sh -- only for images built with the vnap-patches entrypoint
NATIVE=${NATIVE:-0}

HERE=$(cd "$(dirname "$0")" && pwd)
# CERTS_DIR can be overridden to test other cert sets (mounted at /vnap-certs)
CERTS_DIR=$(realpath "${CERTS_DIR:-$HERE/../vnap-certs}")

case $SCENARIO in
  none)
    RSU_SEC=(-e VANETZA_SECURITY=none); OBU_SEC=(-e VANETZA_SECURITY=none) ;;
  certify)
    C=/vnap-certs/certify
    COMMON=(-e VANETZA_SECURITY=certs-v2 -e AA_CERT=$C/aa_vnap.cert -e ROOT_CERT=$C/root_ca_vnap.cert)
    RSU_SEC=("${COMMON[@]}" -e AT_CERT=$C/ticket_vnap.cert -e AT_KEY=$C/ticket_vnap.key)
    OBU_SEC=("${COMMON[@]}" -e AT_CERT=$C/ticket_vnap.cert -e AT_KEY=$C/ticket_vnap.key) ;;
  certify-fresh|certify-fresh-badroot)
    # per-station ATs issued 2026-09-24 by certify/aa_vnap (valid to 2026-11-23);
    # -badroot is a negative control: OBU trusts the unrelated c-its-pki root
    C=/vnap-certs/certify
    OBU_ROOT=$C/root_ca_vnap.cert
    [ $SCENARIO = certify-fresh-badroot ] && OBU_ROOT=/vnap-certs/c-its-pki/root_ca.cert
    RSU_SEC=(-e VANETZA_SECURITY=certs-v2 -e AA_CERT=$C/aa_vnap.cert -e ROOT_CERT=$C/root_ca_vnap.cert
             -e AT_CERT=$C/ticket_vnap_rsu_20260924.cert -e AT_KEY=$C/ticket_vnap_rsu_20260924.key)
    OBU_SEC=(-e VANETZA_SECURITY=certs-v2 -e AA_CERT=$C/aa_vnap.cert -e ROOT_CERT=$OBU_ROOT
             -e AT_CERT=$C/ticket_vnap_obu_20260924.cert -e AT_KEY=$C/ticket_vnap_obu_20260924.key) ;;
  naive-v3)
    # certs-v3 with no cert files: each station self-generates root/AA/AT at startup
    # (NaiveCertificateProvider) and has no trusted root, so with full-chain verification
    # every received message must be rejected -- a negative control for the chain check.
    RSU_SEC=(-e VANETZA_SECURITY=certs-v3); OBU_SEC=(-e VANETZA_SECURITY=certs-v3) ;;
  c-its-pki-pseudo)
    # OBU holds the butterfly ATs bke_at_0..7 and changes pseudonym on control channel events;
    # needs NATIVE=1 and an image with the event-driven pseudonym patch
    C=/vnap-certs/c-its-pki
    SEC=${PKI_SECURITY:-certs-v3}
    PSEUDO_CTL=1
    RSU_SEC=(-e VANETZA_SECURITY=$SEC -e AA_CERT=$C/aa.cert -e ROOT_CERT=$C/root_ca.cert
             -e AT_CERT=$C/at.cert -e AT_KEY=$C/at.der)
    OBU_SEC=(-e VANETZA_SECURITY=$SEC -e SECURITY=pseudonyms -e PSEUDO_CONTROL_BROKER=pseudo-broker
             -e PSEUDO_MIN_INTERVAL=${PSEUDO_MIN_CHANGE_MS:-1000}
             -e AA_CERT=$C/aa.cert -e ROOT_CERT=$C/root_ca.cert)
    if [ -n "$PSEUDO_CONTROL_USERNAME" ]; then
      OBU_SEC+=(-e PSEUDO_CONTROL_USERNAME="$PSEUDO_CONTROL_USERNAME" -e PSEUDO_CONTROL_PASSWORD="$PSEUDO_CONTROL_PASSWORD")
    fi
    for i in 0 1 2 3 4 5 6 7; do
      OBU_SEC+=(-e PSEUDO_CERT_$i=$C/bke_at_$i.cert -e PSEUDO_KEY_$i=$C/bke_at_${i}_sign.der)
    done ;;
  c-its-pki|c-its-pki-badroot)
    # v3 certs from the C-ITS-PKI tool; -badroot is a negative control: OBU trusts the
    # TLM certificate (self-signed, but not the issuer of aa.cert) instead of root_ca
    C=/vnap-certs/c-its-pki
    OBU_ROOT=$C/root_ca.cert
    [ $SCENARIO = c-its-pki-badroot ] && OBU_ROOT=$C/tlm.cert
    SEC=${PKI_SECURITY:-certs-v3}  # PKI_SECURITY=certs-v2 for v2 cert sets from the C-ITS-PKI tool
    RSU_SEC=(-e VANETZA_SECURITY=$SEC -e AA_CERT=$C/aa.cert -e ROOT_CERT=$C/root_ca.cert
             -e AT_CERT=$C/at.cert -e AT_KEY=$C/at.der)
    OBU_SEC=(-e VANETZA_SECURITY=$SEC -e AA_CERT=$C/aa.cert -e ROOT_CERT=$OBU_ROOT
             -e AT_CERT=$C/bke_at_0.cert -e AT_KEY=$C/bke_at_0_sign.der) ;;
  *)
    echo "unknown scenario: $SCENARIO"; exit 1 ;;
esac

docker network inspect vanetzalan0 >/dev/null 2>&1 || docker network create vanetzalan0 --subnet=192.168.98.0/24

# run labels, shown by "vnapctl status" (who started what, when)
LABELS=(--label vnap.scenario=$SCENARIO --label vnap.started_by=${USER:-unknown}
        --label vnap.started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ))

# pseudonym control channel: its own network and broker, apart from vanetzalan0 and the stations' brokers
CTL_IMAGE=vnap-pseudo-ctl
CTL_AUTH=()
[ -n "$PSEUDO_CONTROL_USERNAME" ] && CTL_AUTH=(-e CONTROL_USERNAME="$PSEUDO_CONTROL_USERNAME" -e CONTROL_PASSWORD="$PSEUDO_CONTROL_PASSWORD")
if [ "${PSEUDO_CTL:-0}" = 1 ]; then
  docker image inspect $CTL_IMAGE >/dev/null 2>&1 || tar -C "$HERE/pseudo-ctl" -c . | docker build -q -t $CTL_IMAGE - >/dev/null  # stdin: snap docker cannot read /mnt/hgfs
  docker network inspect vnapctl0 >/dev/null 2>&1 || docker network create vnapctl0 --subnet=192.168.99.0/24
  docker run -d --name pseudo-broker --network vnapctl0 --ip 192.168.99.2 "${LABELS[@]}" "${CTL_AUTH[@]}" $CTL_IMAGE broker
fi

run_station() {  # name ip station_id station_type mac sec-args...
  local name=$1 ip=$2 id=$3 type=$4 mac=$5; shift 5
  local ep=(--volume "$HERE/r2-entrypoint.sh":/r2-entrypoint.sh:ro --entrypoint /bin/sh) cmd=(/r2-entrypoint.sh)
  if [ "$NATIVE" = 1 ]; then
    ep=(); cmd=()
    [[ " $* " == *AT_CERT=* ]] && ep=(-e SECURITY=certs)
  fi
  docker create --name $name "${LABELS[@]}" \
    --volume "$CERTS_DIR":/vnap-certs:ro \
    "${ep[@]}" \
    --network vanetzalan0 --ip $ip --cap-add NET_ADMIN \
    -e VANETZA_STATION_ID=$id -e VANETZA_STATION_TYPE=$type -e VANETZA_MAC_ADDRESS=$mac \
    -e VANETZA_INTERFACE=br0 -e START_EMBEDDED_MOSQUITTO=true -e SUPPORT_MAC_BLOCKING=true \
    -e VANETZA_BRIDGE_IP=$ip \
    "$@" $IMAGE "${cmd[@]}" >/dev/null
  # stations with a pseudonym pool also join the control network (as eth1, before start)
  if [ "${PSEUDO_CTL:-0}" = 1 ] && [[ " $* " == *SECURITY=pseudonyms* ]]; then
    docker network connect --ip ${ip/192.168.98./192.168.99.} vnapctl0 $name  # same host part as on vanetzalan0
  fi
  docker start $name
}

run_station rsu 192.168.98.10 1 15 6e:06:e0:03:00:01 "${RSU_SEC[@]}"
run_station obu 192.168.98.20 2 5  6e:06:e0:03:00:02 "${OBU_SEC[@]}"

if [ "${PSEUDO_CTL:-0}" = 1 ]; then
  docker run -d --name pseudo-client --network vnapctl0 --ip 192.168.99.3 "${LABELS[@]}" "${CTL_AUTH[@]}" \
    -e STATIONS=2 -e MODE=${PSEUDO_MODE:-periodic} -e INTERVAL=${PSEUDO_INTERVAL:-30} \
    -e MIN_INTERVAL=${PSEUDO_RANDOM_MIN:-10} -e MAX_INTERVAL=${PSEUDO_RANDOM_MAX:-60} \
    -e INDEX=${PSEUDO_INDEX:-} -e COUNT=${PSEUDO_COUNT:-0} \
    $CTL_IMAGE client
fi
