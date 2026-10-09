# Patches: certificate refill

The behaviour is described in [certificate refill](../functional/certificate-refill.md), and the
protocol in [control channels](../reference/control-channels.md#certificate-refill).

## Growing pool and PKI channel

- **Change:** the pseudonym pool grows at runtime.
  - `vanetza/security/pseudonym_pool.hpp` (new) keeps the pool, tracks unused entries and never
    wraps around when refill is on.
  - `tools/socktap/pki_channel.{hpp,cpp}` (new) requests batches at a threshold of unused ATs,
    retries with back-off, and installs the answer while running.
  - Each new AT is chain-checked before use (`[V3-CHAIN] batch authorization ticket …`).
  - With no unused AT left, changes are refused until a batch arrives.
- **Options:** `--pki-refill-at`, `--pki-batch-size`, `--pki-topic`, `--pki-retry`
  (`PKI_REFILL_AT`, `PKI_BATCH_SIZE`, `PKI_TOPIC`).
- **Logs:** `[PKI] batch requested …`, `[PKI] batch installed … refresh <ms> ms …`, retries.
- **Files:** `pseudonym_pool.hpp`, `pseudonym_control.hpp`,
  `vanetza/security/v{2,3}/pseudonym_certificate_provider.{hpp,cpp}`, `pki_channel.{hpp,cpp}`,
  `tools/socktap/security.{hpp,cpp}`, `main.cpp`, `tools/socktap/CMakeLists.txt`,
  `vanetza/security/CMakeLists.txt`, `entrypoint.sh`.

## Station key derivation

- **Change:** `tools/socktap/bke.{hpp,cpp}` (new) implements the IEEE 1609.2.1 butterfly
  expansion function f_k(i, j): three AES-128 blocks, each `AES_k(x+t) XOR (x+t)`, read as a
  big-endian integer mod n. The input x is the 32-bit prefix, i, j and 32 zero bits.
  - The station computes each AT's private key as a + f_k(i, j) + r mod n from its caterpillar
    key a, its expansion key k and the offset r the PKI sends.
  - `security.cpp` checks that every derived key matches its certificate's public key (v2 and
    v3), and rejects the AT otherwise.
- **Options:** `--pki-caterpillar-key`, `--pki-expansion-key` (`PKI_CATERPILLAR_KEY`,
  `PKI_EXPANSION_KEY`).
- **Logs:** the batch installed line adds `key derivation <ms> ms`.
- **Files:** `bke.{hpp,cpp}`, `pki_channel.{hpp,cpp}`, `security.cpp`, `entrypoint.sh`.

## Testing

- **Scenarios:** `c-its-pki-refill`, `c-its-pki-refill-sweep`, `c-its-pki-mixzone-pki` and
  `templates/pki-refill`, with the default expectations `<station>.pki.starved==0` and
  `pki.running==1`.
- **Measurements:** the earlier test runs are listed in [results](../../results/README.md);
  `results/pki-refill-test-2026-10-07.docx` relates refresh time to batch size.
- **C-ITS-PKI side:** the PKI's half of the expansion is checked by C-ITS-PKI's contract test
  ([architecture](../architecture.md#dependency-on-c-its-pki)).
