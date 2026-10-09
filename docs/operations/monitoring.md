# Monitoring

`vnapctl status`, `events` and `check` ([vnapctl](vnapctl.md)) cover most needs. This page
shows the underlying sources, for a closer look.

The examples use instance 0, with stations named as in the scenario (`rsu`, `obu`, …) on
`vanetzalan0` (192.168.98.0/24): the RSU usually at .10, the first OBU at .20. For instance N,
add `-iN` to container and network names and use 10.N.98.x addresses. `vnapctl status` shows
each station's address.

| What | Where to look | Shows |
|---|---|---|
| Received messages | MQTT `vanetza/out/<type>` on the receiving station's broker | the decoded message as JSON, plus its verification result |
| Sent CAMs | MQTT `vanetza/own/cam` on the sending station's broker | the station's own CAMs as sent |
| Frames on the link | `tcpdump` on `br0` inside a station | raw GeoNetworking frames (ethertype 0x8947), including the security header |
| Pseudonym, position and PKI traffic | MQTT `vnap/#` on the control broker (`pseudo-broker`, network `vnapctl0`) | change events and answers, positions, certificate batches; see [control channels](../reference/control-channels.md) |
| Station logs | `docker logs <station>` | `[V3-CHAIN]`, `[PSEUDONYM]`, `[IDCHANGE]`, `[PKI]`, `[MOBILITY]` lines |

## Application messages (MQTT)

Each station runs an embedded MQTT broker on port 1883. socktap publishes every message it
receives on `vanetza/out/<type>` (`cam`, `denm`, `cpm`, …):

```bash
# everything the RSU receives, with the topic
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t 'vanetza/out/#' -v

# one line per received CAM: sender, receiver, verification result, payload size
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t vanetza/out/cam |
  jq -c '{from: .stationID, to: .receiverID, secured, report: .security_report.description, size: .packet_size}'
#  {"from":2,"to":1,"secured":true,"report":"Success","size":82}

# only messages that failed verification
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.10 -t 'vanetza/out/#' |
  jq -c 'select(.security_report.description != "Success")'

# CAMs the OBU sends
docker run --rm --network vanetzalan0 eclipse-mosquitto:2 \
  mosquitto_sub -h 192.168.98.20 -t vanetza/own/cam
```

Useful JSON fields:
- `stationID` / `stationAddr`: the sender's station ID and MAC address (both change with a
  full ID change).
- `receiverID` / `receiverType`: the receiving station.
- `secured`, `security_report.description`: the verification result, `Success` or a failure
  reason such as `Invalid_Certificate`.
- `packet_size`: size of the message payload in bytes.
- `fields`: the decoded message, e.g. `fields.cam.camParameters.basicContainer.referencePosition`.

socktap always delivers received messages, even those that fail verification (non-strict
decapsulation). Judge a message by `security_report`, not by whether it arrived.

## Frames on the link (tcpdump)

The station image includes `tcpdump`. The station's link interface is the bridge `br0`
(`vnapctl` sets `SUPPORT_MAC_BLOCKING=true`):

```bash
# live, GeoNetworking frames only (both directions, as seen by the RSU)
docker exec rsu tcpdump -i br0 -e -nn ether proto 0x8947

# frames from one sender MAC only
docker exec rsu tcpdump -i br0 -e -nn ether src 6e:06:e0:03:00:02 and ether proto 0x8947

# save a capture for Wireshark (Ctrl-C to stop); under /mnt/hgfs it is visible on the host
docker exec rsu tcpdump -i br0 -U -w - ether proto 0x8947 > /mnt/hgfs/COIMBRA/vnap.pcap
```

- **tcpdump output:** it decodes the GeoNetworking and BTP headers. It does not parse the
  secured header, so for signed packets the values printed after it are wrong (e.g.
  `Payload:`, `lat`/`lon`).
- **Wireshark:** its GeoNetworking, BTP, ITS (CAM/DENM) and IEEE 1609.2 dissectors decode the
  full frame, including the signer certificate. Wireshark is not installed in the VM; open the
  capture on the host.
- **Frame size:** with v3 and 1 Hz CAMs, every CAM carries the full signer certificate, so
  frame size does not reveal a pseudonym change.
- **Offline analysis:** the eavesdropper decodes a capture offline
  (`python3 sim/images/eavesdropper/eavesdropper.py --pcap capture.pcap --log-dir out/`, needs
  `asn1tools`).

## Control channel

```bash
# all control traffic (add -u USER -P PASSWORD if the broker requires them)
docker run --rm --network vnapctl0 eclipse-mosquitto:2 mosquitto_sub -h pseudo-broker -t 'vnap/#' -v

docker logs -f pseudo-client                 # events sent by the client and the answers
docker logs -f mobility                      # vehicle positions every 10 s, laps, mix-zone events
docker logs -f pki                           # provisioning, issued batches with timings
```

For runs started by the service, the broker requires the run's credentials, which the
service keeps to itself. Use the run's *Events* tab, or `/api/runs/<id>/events`, instead.
