# Test certificates

**Test material only.** The private keys in this directory are public: they are committed to
git. Never use these certificates or keys outside the simulation.

`vnapctl` mounts this directory read-only into every station at `/vnap-certs`; scenario files
refer to it as `/vnap-certs/...`. `vnapctl certs` shows every certificate's validity, and
`vnapctl certs regenerate v2|v3` (or the web UI's administration page) makes a new set with
chosen lifetimes: see [docs/certificates.md](../docs/certificates.md#validity-and-regeneration).
A regenerated set has a `generated.json` with the lifetimes used.

| Directory | Format | Made with | Files |
|---|---|---|---|
| `c-its-pki/` | v3 (`certs-v3`), TS 103 097 V1.3.1 | C-ITS-PKI | `root_ca`, `tlm`, `ea`, `aa` certificates; regular AT `at.cert` / `at.der`; butterfly ATs `bke_at_<0..23>.cert` / `bke_at_<j>_sign.der` |
| `certify/` | v2 (`certs-v2`), TS 103 097 V1.2.1 | Vanetza `certify` | `root_ca_vnap`, `aa_vnap` (certificate and key); ATs `ticket_vnap_rsu`, `ticket_vnap_obu` (certificate and key) |

Scenarios with their own PKI (`[pki]`) do not use these files: their certificates are
created per run and deleted with it.
