#!/bin/sh
# Test-only wrapper for an unmodified release2-main image (mounted over --entrypoint).
# Runs the stock /entrypoint.sh setup (bridge, MAC blocking, embedded mosquitto), then
# starts socktap with certificate args, which the stock entrypoint has no way to pass.
#
# Security mode comes from VANETZA_SECURITY (read by socktap's config.cpp):
#   none | dummy[-v2|-v3] | certs[-v2|-v3]   (existing vnap-certs are all v2 -> certs-v2)

grep -v '^/usr/local/bin/socktap' /entrypoint.sh > /tmp/entrypoint-setup.sh
. /tmp/entrypoint-setup.sh

set -- -c /config.ini
if [ -n "$AT_CERT" ]; then
    set -- "$@" --certificate "$AT_CERT" --certificate-key "$AT_KEY"
    [ -n "$AA_CERT" ] && set -- "$@" --certificate-chain "$AA_CERT"
    # release2 only caches --certificate-chain; trust anchors need --trusted-certificate (v2)
    [ -n "$ROOT_CERT" ] && set -- "$@" --trusted-certificate "$ROOT_CERT"
fi

echo "[R2-ENTRYPOINT] VANETZA_SECURITY=${VANETZA_SECURITY:-<config.ini>} socktap $*"
exec /usr/local/bin/socktap "$@"
