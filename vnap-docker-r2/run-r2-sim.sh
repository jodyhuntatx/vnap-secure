#!/bin/bash
# Start an RSU + OBU pair from a release2-main image on vanetzalan0.
# usage: ./run-r2-sim.sh [none|certify|certify-fresh|certify-fresh-badroot|naive-v3|c-its-pki|c-its-pki-badroot] [image]
#   none          - no security (baseline: proves the link/CAM path works)
#   certify       - certs-v2 with vnap-certs/certify (both stations use ticket_vnap; AT EXPIRED 2026-06-07)
#   certify-fresh - certs-v2, certify root/AA + per-station ATs issued 2026-09-24
#   naive-v3      - certs-v3, self-generated certs per station (exercises v3 sign/verify)
#   c-its-pki     - certs-v3 with vnap-certs/c-its-pki (RSU: at, OBU: bke_at_0); -badroot: OBU trusts tlm.cert

SCENARIO=${1:-none}
IMAGE=${2:-vnap:r2-stock}
# NATIVE=1 uses the image's own (patched) /entrypoint.sh with SECURITY=certs instead of
# mounting r2-entrypoint.sh -- only for images built with the vnap-patches-r2 entrypoint
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

run_station() {  # name ip station_id station_type mac sec-args...
  local name=$1 ip=$2 id=$3 type=$4 mac=$5; shift 5
  local ep=(--volume "$HERE/r2-entrypoint.sh":/r2-entrypoint.sh:ro --entrypoint /bin/sh) cmd=(/r2-entrypoint.sh)
  if [ "$NATIVE" = 1 ]; then
    ep=(); cmd=()
    [[ " $* " == *AT_CERT=* ]] && ep=(-e SECURITY=certs)
  fi
  docker run -d --name $name \
    --volume "$CERTS_DIR":/vnap-certs:ro \
    "${ep[@]}" \
    --network vanetzalan0 --ip $ip --cap-add NET_ADMIN \
    -e VANETZA_STATION_ID=$id -e VANETZA_STATION_TYPE=$type -e VANETZA_MAC_ADDRESS=$mac \
    -e VANETZA_INTERFACE=br0 -e START_EMBEDDED_MOSQUITTO=true -e SUPPORT_MAC_BLOCKING=true \
    "$@" $IMAGE "${cmd[@]}"
}

run_station rsu 192.168.98.10 1 15 6e:06:e0:03:00:01 "${RSU_SEC[@]}"
run_station obu 192.168.98.20 2 5  6e:06:e0:03:00:02 "${OBU_SEC[@]}"
