# Certificates

Scenarios either bring their own certificate files (fixed sets in `certs/`) or get a fresh
PKI per run ([certificate refill](functional/certificate-refill.md)). The fixed sets are
**test material**: their private keys are committed and must never be used outside the
simulation. `certs/README.md` lists the files and their validity.

## Fixed sets

The repository's `certs/` is mounted read-only into every station at `/vnap-certs`. A
scenario's `certs_dir` mounts another directory instead (see
[scenario format](reference/scenario-format.md)).

### `certs/c-its-pki/` (v3, `certs-v3`)

Made with C-ITS-PKI:
- the authorities: `root_ca`, `aa`, `ea` and `tlm`;
- a regular AT (`at.cert` / `at.der`), used by RSUs;
- 24 butterfly ATs (`bke_at_<j>.cert` / `bke_at_<j>_sign.der`), used by fixed pseudonym pools.

The CA private keys are not kept: the set only has to verify. C-ITS-PKI's `docs/compliance.md`
documents the certificates' conformance.

### `certs/certify/` (v2, `certs-v2`)

Made with Vanetza's `certify` tool (TS 103 097 V1.2.1 format): a root and AA (`root_ca_vnap`,
`aa_vnap`, certificate and key) and one AT per station (`ticket_vnap_rsu`, `ticket_vnap_obu`).
Used by the `trust:v2` variant.

## Validity and regeneration

Certificates expire, and a station drops messages signed under an expired AT without logging
anything: the scenario then fails its check with "no CAMs received". Check the sets and make
new ones with `vnapctl certs`, or in the web UI under *Administration → Test certificates*
(administrators; [web UI](operations/web-ui.md)).

```bash
cd ${VNAP_HOME}/sim
./vnapctl certs                                              # every certificate with its validity; exit 1 if one expired
./vnapctl certs regenerate v3 --lifetime at_hours=8760       # new v3 set, ATs valid for a year
./vnapctl certs regenerate v2 --lifetime at_days=90
```

**Lifetimes.** No ETSI document gives defaults: TS 103 097 fixes no certificate lifetime, and
TS 102 941 leaves it to deployment policy (C-ITS-PKI's review of the standards,
`external/C-ITS-PKI/docs/privacy-and-deployment-notes.md` section 2). The defaults are those of
the tool that makes each set, and each can be changed:

| Set | Lifetime key | Certificate | Default | Basis |
|---|---|---|---|---|
| v3 | `root_years` | root CA | 10 years | C-ITS-PKI default |
| v3 | `tlm_years` | trust list manager | 10 years | C-ITS-PKI default |
| v3 | `ea_years` | enrolment authority | 5 years | C-ITS-PKI default |
| v3 | `aa_years` | authorization authority | 5 years | C-ITS-PKI default |
| v3 | `at_hours` | `at.cert` and the butterfly ATs | 168 hours | C-ITS-PKI default |
| v2 | `root_days` | root CA | 365 days | `certify` default |
| v2 | `aa_days` | authorization authority | 180 days | `certify` default |
| v2 | `at_days` | the station ATs | 7 days | `certify` default |

- **Short ATs expire quickly.** One week (the AT default of both tools) suits a deployment.
  For a committed test set it means regenerating every week, so pick a longer `at_hours` or
  `at_days` for test sets that should last.
- **Nesting:** a certificate may not outlive the one that signed it. An AA longer than the root
  CA, or ATs longer than the AA, are refused.
- **How it works:** the new set is built in a scratch directory under `certs/`. v3 is made by
  `sim/images/pki/test_certs.py` in the `vnap-pki` image, which `vnapctl` builds from the
  C-ITS-PKI submodule. v2 is made by the steps of `scripts/certs/gen-certify.sh` with `certify`
  in `vnap:latest`. The new set is read back and checked for missing or expired files. Only
  then are the set's `*.cert`, `*.der` and `*.key` files replaced. The new files keep the same
  names, so scenarios keep working.
- **Record:** `generated.json` in the set holds the time, who regenerated it, the lifetimes and,
  for v3, the C-ITS-PKI commit.
- **Afterwards:** the files are tracked in git, so commit them. Running simulations keep the
  certificates they loaded; new runs use the new ones.
- **Reading validity:** v3 dates come from C-ITS-PKI's decoder with Vanetza's unit lengths (a
  year is 31,556,952 s). v2 dates come from `certify show-certificate`, the parser the stations
  use.

C-ITS-PKI's `gen-vnap-certs.sh` and `scripts/certs/gen-certify.sh` still work by hand, but they
take no lifetimes for the authorities and write no record.

## Key formats

socktap loads private keys as PKCS#8 DER (v3 also accepts PEM). Keys from `certify` and
C-ITS-PKI already have that format. To convert a PEM key:

```bash
scripts/certs/convert-sign-key.sh <key.pem>     # writes <key.pem>.der and prints it
```

## Trust configuration

The [container environment](reference/container-environment.md) sets the certificates:
`AT_CERT` / `AT_KEY` (or a pseudonym pool), `AA_CERT` and `ROOT_CERT`. In scenarios, the
station and default keys `at_cert`, `at_key`, `aa_cert`, `root_cert` and `pseudonyms` set
them. The negative controls point `root_cert` at the wrong certificate on purpose
(`trust:badroot` trusts `tlm.cert`). How the stations check chains is described in
[security and chain verification](functional/security-and-chain-verification.md).
