# Security and chain verification

Stations sign every message with an authorization ticket (AT) and verify received messages,
following ETSI TS 103 097: `certs-v2` for V1.2.1 certificates, `certs-v3` for V1.3.1 /
IEEE 1609.2 certificates.

## Trust chain

```
 root CA ──signs──▶ AA (authorization authority) ──signs──▶ AT (pseudonym certificate) ──signs──▶ CAM
```

Each station is configured with:
- its own AT and key, or a pool of ATs;
- the AA certificate (the chain);
- the trusted root.

A received message is accepted only if its signer's AT chains, through an AA, up to the
trusted root, and the signature verifies. Upstream v3 accepted any AT regardless of its
issuer; the patch set adds the full check, AT → AA → trusted root, with the IEEE 1609.2
signing input. It also makes `--trusted-certificate` work for v3.

## Certificate checks in the station log

At startup, each station checks its configured chain and its own AT(s), and logs the results:

```
[V3-CHAIN] chain certificate /vnap-certs/c-its-pki/aa.cert: OK
[V3-CHAIN] own authorization ticket /vnap-certs/c-its-pki/at.cert: OK
```

- **Refill:** with [certificate refill](certificate-refill.md), every AT that arrives during a
  run is checked the same way before it is used, and rejected if the chain or its key does
  not match:

  ```
  [V3-CHAIN] batch authorization ticket /tmp/vnap-pki/2-2/7.cert: OK
  ```

  Here obu1 (configured station ID 2) received ticket 7 of its 2nd batch request (`2-2`),
  and its signature chain to the root CA is valid.
- **Where the checks show up:** `vnapctl events --kind chain` lists these "certificate check"
  events, and `status` and the web UI report a failed one as a warning.
- **Expectations:** the default expectations require `<station>.chain.fail == 0`.

## Verification results

- **Results:** each received message is published on the station's MQTT broker with
  `security_report` (`Success`, or the failure reason such as `Invalid_Certificate`).
- **Rates:** `check` computes per-station success rates from these, and expects a CAM success
  rate of 1 with security enabled ([vnapctl](../operations/vnapctl.md#checks)).
- **Non-strict decapsulation:** socktap delivers messages even when they fail verification.
- **Negative controls:** `c-its-pki-badroot` (trusts the TLM certificate instead of the root)
  and `naive-v3` (self-made certificates) must show failing reports; their scenarios expect
  exactly that.

## Robustness

Verification runs on several reception threads while another thread signs. Several upstream
data races there crashed socktap under load. They were found with ThreadSanitizer and fixed:
- the sign header policy;
- the certificate caches;
- the runtime clock and ASN.1 descriptors;
- the routers, PubSub/MQTT/DDS and the RSSI reader.

See [thread safety](../patches/thread-safety.md). `stress-naive-v3` (4 stations at 50 Hz)
reproduces the load.

## Related

- **Certificates:** the fixed test sets and key formats ([certificates](../certificates.md)).
- **Patches:** [certificate verification](../patches/certificate-verification.md).
- **Limitations:** the known upstream limitations are in
  [troubleshooting](../operations/troubleshooting.md#known-limitations).
