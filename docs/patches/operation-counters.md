# Patches: operation counters

Phase 4 of the pseudonym-change overhead design: what a change and a refill cost the stations in
security operations. The analysis is `sim/vnapsim/counters.py`
(`overhead.json` and `metrics.json`: [vnapctl](../operations/vnapctl.md#saving-a-runs-results)).
First image: `vnap:r2-p20`.

## Counters and the `[COUNTERS]` line

- **Change:** `vanetza/security/op_counters.{hpp,cpp}` (new) hold process-wide counters, each a
  count and a total time (atomics, so the worker threads can add to them). Every second,
  socktap logs them all, cumulative:

  ```
  [COUNTERS] {"sign":[1830,61234],"verify":[5410,402113],...}   (count, total us)
  ```

  | Counter | Counted where | What |
  |---|---|---|
  | `sign` | `security.cpp`, around the entity's `encapsulate_packet` | outgoing messages to sign, refusals included (e.g. before the station has a position its AT is valid for); `ecdsa_sign` counts the signatures made |
  | `verify` | `security.cpp`, around `decapsulate_packet` | received messages verified |
  | `verify_failed` | the same, report not `Success` | count only |
  | `unknown_signer` | the same, report `Signer_Certificate_Not_Found` | a digest of an AT the receiver has not seen (a cache miss) |
  | `ecdsa_sign`, `ecdsa_verify` | `CountingBackend` in `security.cpp`, around the backend | every ECDSA operation, messages and certificates |
  | `chain_check` | `CertificateChainVerifier::verify`, past the memo | a received AT checked against its AA for the first time |
  | `batch_check` | `pseudonym_batch_loader` in `security.cpp` | the chain check of each AT of a refill batch |
  | `derivation` | `pki_channel.cpp` | butterfly private keys derived on the station |
  | `id_change` | `IdChangeService::change` | committed ID changes, PREPARE to the last COMMIT |

- **ID change line:** `IdChangeService::change` also logs each committed change:
  `[IDCHANGE] completed in <us> us`.
- **Backend independence:** the ECDSA counts come from a decorator around the backend that
  `create_backend("default")` returns, so they do not depend on Crypto++ or OpenSSL.
- **Chain checks:** `CertificateChainVerifier::count_into(OpCounter*)` names the counter; the
  default (null) counts nothing. The verify service passes `chain_check`. The batch loader times
  its own verifier's calls as `batch_check`, so own ATs and received ones stay apart.
- **Scope:** v3 counts everything. v2 counts signing, verification and the ECDSA operations;
  its certificate checks (`DefaultCertificateValidator`) are not counted separately.
- **Option:** `--op-counters-interval <ms>` (default 1000; 0: no `[COUNTERS]` lines). Without
  security no line is logged.
- **Cost:** one log line per second per station; the counters are relaxed atomic additions.
- **Files:** `op_counters.{hpp,cpp}`, `vanetza/security/CMakeLists.txt`,
  `straight_verify_service.cpp`, `v3/certificate_chain.{hpp,cpp}`, `id_change_service.cpp`,
  `tools/socktap/security.cpp`, `pki_channel.cpp`, `main.cpp`.

## Reading the counts

- **Differences:** two `[COUNTERS]` lines give what happened between them. A refill is the only
  source of derivations and batch checks; chain checks happen when a receiver first hears an AT,
  after the sender's silent period.
- **Carrying over to other hardware:** counts do not depend on the host; times do. Multiply the
  counts by a target OBU's or HSM's per-operation times
  ([design](../../Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx), section 5.3).

## Testing

- `sim/tests/test_counters.py`: the analysis on synthetic logs.
- A live run of `crossing` on the new image: chain checks per change near the number of
  receivers, one derivation and one batch check per refilled AT (see the
  [CHANGELOG](../../CHANGELOG.md) entry).
