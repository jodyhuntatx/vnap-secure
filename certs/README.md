# Test certificates

**Test material only.** The private keys in this directory are public: they are committed to
git. Never use these certificates or keys outside the simulation.

`vnapctl` mounts this directory read-only into every station at `/vnap-certs`; scenario files
refer to it as `/vnap-certs/...`. How to regenerate the sets is in
[docs/certificates.md](../docs/certificates.md).

| Directory | Format | Made with | Files | Valid until |
|---|---|---|---|---|
| `c-its-pki/` | v3 (`certs-v3`), TS 103 097 V1.3.1 | C-ITS-PKI `gen-vnap-certs.sh` (2026-10-04) | `root_ca`, `tlm`, `ea`, `aa` certificates; regular AT `at.cert` / `at.der`; butterfly ATs `bke_at_<0..23>.cert` / `bke_at_<j>_sign.der` | see `cli.py info` in C-ITS-PKI |
| `certify/` | v2 (`certs-v2`), TS 103 097 V1.2.1 | Vanetza `certify` (2026-09-24) | `root_ca_vnap`, `aa_vnap` (certificate and key); ATs `ticket_vnap_rsu_20260924`, `ticket_vnap_obu_20260924` | ATs 2026-11-23 |

Scenarios with their own PKI (`[pki]`) do not use these files: their certificates are
created per run and deleted with it.
