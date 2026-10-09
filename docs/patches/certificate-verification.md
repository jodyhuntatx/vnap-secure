# Patches: certificate verification

How the stations use these checks is described in
[security and chain verification](../functional/security-and-chain-verification.md).

## v3 full-chain verification

- **Problem:** upstream v3 verification accepted a message signed by any AT, regardless of its
  issuer, and `--trusted-certificate` had no effect for v3.
- **Change:**
  - a new `vanetza/security/v3/certificate_chain.{hpp,cpp}` verifies AT → AA → trusted root,
    with the IEEE 1609.2 signing input;
  - `straight_verify_service.{hpp,cpp}` uses it for received messages;
  - `tools/socktap/security.cpp` loads the AA chain and the trusted root for v3.
- **Effect:** messages from ATs of another hierarchy fail with a certificate error.
  `c-its-pki-badroot` (OBU trusts the TLM certificate) and `naive-v3` (self-made
  certificates) are the negative controls.
- **Upstream:** candidate.

## Startup certificate checks

- **Change:** `tools/socktap/security.cpp` logs the result of checking the configured AA and the
  station's own AT(s) at startup: `[V3-CHAIN]` / `[V2-CHAIN]` lines. Each line is a single
  write, because other threads log at the same time. The refill patches reuse it for every AT
  that arrives.
- **Upstream:** candidate.

## Optional Assurance_Level (v2)

- **Problem:** TS 103 097 V1.2.1 requires the `Assurance_Level` attribute, and upstream rejects
  v2 certificates without it. Older v2 certificates lack it.
- **Change:** `vanetza/security/v2/default_certificate_validator.cpp` accepts its absence.
  Current C-ITS-PKI and `certify` output include it, so this matters only for old certificate
  sets.
- **Upstream:** simulation convenience; not proposed.

## v3 DER keys

- **Problem:** v3 could not load PKCS#8 DER private keys, and failures ended in
  `terminate … char const*` without a message.
- **Change:** `vanetza/security/v3/persistence.cpp` loads PKCS#8 DER keys and reports readable
  errors.
- **Upstream:** candidate.

## Files

- `vanetza/security/v3/certificate_chain.{hpp,cpp}` (new);
- `vanetza/security/straight_verify_service.{hpp,cpp}`;
- `vanetza/security/CMakeLists.txt`;
- `vanetza/security/v2/default_certificate_validator.cpp`;
- `vanetza/security/v3/persistence.cpp`;
- `tools/socktap/security.cpp`.

## Testing

Every secured scenario checks success rates and chain results (`vnapctl check` default
expectations). The negative controls check that wrong chains are rejected. `vnap:msgcheck`
runs the same verification on message files offline
([troubleshooting](../operations/troubleshooting.md#checking-message-files-offline)).
