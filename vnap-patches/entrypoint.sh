#!/bin/sh

update_config_field() {
    if grep -qE "^\[$2\]" "$1" && grep -qE "^$3\s*=" "$1"; then
        val=$(grep -E "^\[$2\]" -A 1000 "$1" | grep -m 1 -E "^$3\s*=" | sed -E 's/^[^=]*=\s*//')
        sed "/^\[$5]/,/^\[/{s/^$6[[:space:]]*=.*/$6 = $val/}" "$4" > "$4.tmp" && cp "$4.tmp" "$4" && rm "$4.tmp";
    else
        echo "Field '$3' in section '$2' not found in '$1'. Skipping update."
    fi
}

if [ -e "/info.ini" ]; then
    update_config_field "/info.ini" "general" "id" "/config.ini" "station" "id"
    update_config_field "/info.ini" "mobility" "stationType" "/config.ini" "station" "type"
    update_config_field "/info.ini" "mobility" "latitude" "/config.ini" "station" "latitude"
    update_config_field "/info.ini" "mobility" "longitude" "/config.ini" "station" "longitude"
    update_config_field "/info.ini" "mobility" "macAddr" "/config.ini" "station" "mac_address"
    update_config_field "/info.ini" "mobility" "interface" "/config.ini" "general" "interface"
    update_config_field "/info.ini" "general" "id" "/config.ini" "general" "dds_domain_id"
    echo "Config update process complete"
else
    echo "No global board config file found. Skipping config update process"
fi

# V2X link interface: eth0 by default. A container attached to several networks (e.g. a
# separate control network) cannot rely on docker naming the V2X network eth0, so
# VANETZA_BRIDGE_IP selects the interface carrying that address (vnap-secure).
LINK_IF=eth0
if [ -n "$VANETZA_BRIDGE_IP" ]; then
    LINK_IF=$(ip -o -4 addr show | awk -v want="$VANETZA_BRIDGE_IP" '{ split($4, a, "/"); if (a[1] == want) { print $2; exit } }')
    if [ -z "$LINK_IF" ]; then
        echo "No interface has address $VANETZA_BRIDGE_IP (VANETZA_BRIDGE_IP)."
        exit 1
    fi
fi
IP_ADDR=$(ip -f inet addr show $LINK_IF | awk '/inet / {print $2}')
# only the default route via the link interface is lost when its address moves to the bridge
GW_ADDR=$(ip r | awk -v d="$LINK_IF" '/^default / && $5 == d {print $3}')
BR_ID=br0

if [ -n "$SUPPORT_MAC_BLOCKING" ] && [ $SUPPORT_MAC_BLOCKING = true ] ; then
    brctl addbr $BR_ID
    ip a a $IP_ADDR dev $BR_ID
    ip a d  $IP_ADDR dev $LINK_IF
    brctl addif $BR_ID $LINK_IF
    ip link set $BR_ID up
    [ -n "$GW_ADDR" ] && ip r a default via $GW_ADDR
fi

printf '#!/bin/sh\nebtables -A INPUT -s $1 -j DROP;' > /bin/block
printf '#!/bin/sh\nebtables -D INPUT -s $1 -j DROP;' > /bin/unblock
chmod +x /bin/block
chmod +x /bin/unblock

if [ -n "$START_EMBEDDED_MOSQUITTO" ] && [ $START_EMBEDDED_MOSQUITTO = true ] ; then
    printf "\nlistener ${EMBEDDED_MOSQUITTO_PORT} 0.0.0.0\nallow_anonymous true\n\n" > mosquitto.conf
    /usr/sbin/mosquitto -c mosquitto.conf &>/dev/null &
    sleep 2
fi

# Security mode dispatch on $SECURITY (vnap-secure):
#   unset      -> socktap with config.ini settings (security=none by default)
#   certs      -> static AT cert/key; AA via --certificate-chain, root via --trusted-certificate
#   pseudonyms -> pseudonym pool: PSEUDO_CERT_0/PSEUDO_KEY_0, PSEUDO_CERT_1/PSEUDO_KEY_1, ...
#                 (consecutive pairs from index 0), changed on events from the control channel:
#                 PSEUDO_CONTROL_BROKER (MQTT broker host, required for changes), PSEUDO_CONTROL_PORT
#                 (1883), PSEUDO_CONTROL_TOPIC (default vnap/pseudonym/<station id>),
#                 PSEUDO_CONTROL_USERNAME/PSEUDO_CONTROL_PASSWORD, PSEUDO_MIN_INTERVAL (ms, 1000),
#                 PSEUDO_ID_CHANGE (full: GN address, MAC and stationId change with the certificate
#                 via the ETSI ID change notification; certificate: only the certificate; default full),
#                 PSEUDO_SILENT_MIN_MS/PSEUDO_SILENT_MAX_MS (random radio silence after each full ID
#                 change, ETSI TR 103 415 4.1.4; default 0 = off),
#                 PKI_REFILL_AT (request a certificate batch from the run's PKI service at this many
#                 unused pseudonyms; unset/0 = fixed pool that wraps around), PKI_BATCH_SIZE (8),
#                 PKI_TOPIC (default vnap/pki/<station id>); refill uses the control broker
# release2 selects the security entity from VANETZA_SECURITY (config.ini "security"),
# not --security. The existing vnap-certs are all v2 (TS 103 097 v1.2.1), hence the
# certs-v2 default; set VANETZA_SECURITY=certs-v3 explicitly for v3 certificates.
# Mobility (vnap-secure): POSITION_CONTROL_BROKER (MQTT broker), POSITION_CONTROL_PORT (1883),
# POSITION_CONTROL_TOPIC (default vnap/position/<station id>), POSITION_CONTROL_USERNAME and
# POSITION_CONTROL_PASSWORD (read by socktap from the environment) move the station at runtime.
# These options start the positional parameters, which every mode below passes to socktap.
set --
if [ -n "$POSITION_CONTROL_BROKER" ]; then
    set -- --position-control-broker "$POSITION_CONTROL_BROKER" --position-control-port "${POSITION_CONTROL_PORT:-1883}"
    [ -n "$POSITION_CONTROL_TOPIC" ] && set -- "$@" --position-control-topic "$POSITION_CONTROL_TOPIC"
    [ -n "$POSITION_CONTROL_USERNAME" ] && set -- "$@" --position-control-username "$POSITION_CONTROL_USERNAME"
fi

if [ -n "$SECURITY" ]; then
    case $SECURITY in
        certs)
            export VANETZA_SECURITY=${VANETZA_SECURITY:-certs-v2}
            echo "Running with $VANETZA_SECURITY, no pseudonym rotation..."
            set -x
            /usr/local/bin/socktap \
                --config /config.ini \
                "$@" \
                --certificate $AT_CERT \
                --certificate-key $AT_KEY \
                --certificate-chain $AA_CERT \
                --trusted-certificate $ROOT_CERT
            set +x
            ;;
        pseudonyms)
            export VANETZA_SECURITY=${VANETZA_SECURITY:-certs-v2}
            # POSIX sh (dash): collect PSEUDO_CERT_<i>/PSEUDO_KEY_<i> pairs into the positional parameters
            # (appended to the mobility options set above)
            i=0
            while true; do
                eval "cert=\${PSEUDO_CERT_$i:-}"
                eval "key=\${PSEUDO_KEY_$i:-}"
                [ -n "$cert" ] || break
                if [ -z "$key" ]; then
                    echo "PSEUDO_CERT_$i is set but PSEUDO_KEY_$i is not."
                    exit 1
                fi
                set -- "$@" --pseudonym-certificate "$cert" --pseudonym-certificate-key "$key"
                i=$((i + 1))
            done
            if [ $i -eq 0 ]; then
                echo "SECURITY=pseudonyms needs at least PSEUDO_CERT_0 and PSEUDO_KEY_0."
                exit 1
            fi
            if [ -n "$PSEUDO_CONTROL_BROKER" ]; then
                set -- "$@" --pseudonym-control-broker "$PSEUDO_CONTROL_BROKER" \
                    --pseudonym-control-port "${PSEUDO_CONTROL_PORT:-1883}" \
                    --pseudonym-min-interval "${PSEUDO_MIN_INTERVAL:-1000}"
                [ -n "$PSEUDO_CONTROL_TOPIC" ] && set -- "$@" --pseudonym-control-topic "$PSEUDO_CONTROL_TOPIC"
                [ -n "$PSEUDO_CONTROL_USERNAME" ] && set -- "$@" --pseudonym-control-username "$PSEUDO_CONTROL_USERNAME"
                # PSEUDO_CONTROL_PASSWORD is read by socktap from the environment (kept out of argv and set -x)
                if [ "${PKI_REFILL_AT:-0}" -gt 0 ]; then
                    set -- "$@" --pki-refill-at "$PKI_REFILL_AT" --pki-batch-size "${PKI_BATCH_SIZE:-8}"
                    [ -n "$PKI_TOPIC" ] && set -- "$@" --pki-topic "$PKI_TOPIC"
                fi
            else
                echo "PSEUDO_CONTROL_BROKER is not set: the pseudonym will not change."
            fi
            set -- "$@" --pseudonym-id-change "${PSEUDO_ID_CHANGE:-full}" \
                --pseudonym-silent-min "${PSEUDO_SILENT_MIN_MS:-0}" --pseudonym-silent-max "${PSEUDO_SILENT_MAX_MS:-0}"
            echo "Running with $VANETZA_SECURITY, pseudonym pool of $i certificate(s)..."
            set -x
            /usr/local/bin/socktap \
                --config /config.ini \
                "$@" \
                --certificate-chain $AA_CERT \
                --trusted-certificate $ROOT_CERT
            set +x
            ;;
        *)
            echo "Invalid value $SECURITY for SECURITY env var."
            echo "Valid values are 'certs' or 'pseudonyms'."
            exit 1
            ;;
    esac
else
    echo "Running with security from config.ini/VANETZA_SECURITY."
    /usr/local/bin/socktap -c /config.ini "$@"
fi
