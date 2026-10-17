# Introduction: what VNAP-Secure is for

## The question

Connected vehicles broadcast Cooperative Awareness Messages (CAMs) several times a second. Each
one carries the vehicle's position, speed and heading, and each is signed with a certificate
(ETSI TS 103 097, IEEE 1609.2) so that receivers can trust it. The signature that makes a
message trustworthy also makes it traceable: a certificate, a MAC address or a station ID that
stays the same lets anyone with a receiver follow a vehicle from street to street.

The standards answer this with pseudonyms. A vehicle holds a pool of short-lived authorization
tickets and changes to a new one from time to time, together with every other identifier it
sends. Whether that protects anybody depends on details the standards leave open: whether all
identifiers really change at once, whether anything else carries over (the rhythm of the
messages, the position), whether the vehicle falls silent for a moment, where it changes, and
how many other vehicles could plausibly be the one that reappears.

VNAP-Secure exists to answer that question by measurement rather than argument: **how well do
pseudonym changes protect vehicles against tracking, and what do they cost?**

## Design intentions

The project is built around a few decisions, each of which shapes the chapters that follow.

- **The real protocol stack, not a model of it.** Stations run Vanetza-NAP's `socktap`, an
  implementation of the ETSI ITS-G5 stack, with C-ITS security switched on. Where upstream does
  not do what a deployment must (verify the whole certificate chain, change all identifiers
  together under the ETSI ID change notification, hold and refill a pseudonym pool, survive
  concurrent load), the project adds a patch set rather than simulating the behaviour. The
  frames on the wire are standard frames that any receiver, or Wireshark, can decode.
- **An honest attacker.** The eavesdropper is a passive listener that sees only what a roadside
  receiver would see: the frames on the message network. It holds no keys, is not on the
  control network, and never sees ground truth. Its linking techniques are documented one by
  one, each with the countermeasure that defeats it.
- **Ground truth kept apart from the attacker's view.** Which certificate each vehicle really
  used comes from the stations' own logs and the PKI's issue log. Every link the eavesdropper
  makes is scored correct or wrong against it, so a privacy result is a count, not an
  impression.
- **A message plane and a control plane.** What a real vehicle would know internally or receive
  over a backend link (its GNSS position, the decision to change pseudonym, the connection to
  its certificate authority) travels on a separate control network that the attacker cannot
  reach. This keeps the simulation's own machinery from leaking into what is measured.
- **Certificate handling close to a deployment.** Each run can have its own PKI. Vehicles start
  with a batch of butterfly-expanded authorization tickets and request new batches as they run
  low; by default they derive the private keys themselves, so the PKI never holds them, and no
  certificate is used twice. The time to refresh certificates is measured, because a vehicle
  without an unused pseudonym cannot change.
- **Measured countermeasures.** Each defence was added because a measurement showed an attack
  that worked: full ID change against shared identifiers, silent periods against position
  continuity, mix zones against a patient attacker, a random CAM phase against timing, random
  turns against fixed traffic patterns. The test summaries record each step with its numbers.
- **Reproducible runs.** A run is defined by a scenario file, a seed and image tags, and records
  them together with the C-ITS-PKI commit it used. The eavesdropper's frames are kept, so its linking can
  be replayed with other settings without running the simulation again.
- **Usable without a shell.** A multi-user service puts accounts, quotas and isolation in front
  of Docker. Users author scenarios from templates, in a builder or as text; watch runs live;
  and receive a summary report that states what was measured and, by fixed rules, which run to
  try next.

## How the parts fit

| Part | Role | Chapters |
|---|---|---|
| Patch set | makes `socktap` behave like a deployed station: chain verification, ID change, pseudonym pools, refill, controllable position, thread safety | How it works; Patch set |
| Simulation harness (`vnapctl`, `vnapsim`) | starts stations, control broker, per-run PKI, mobility client and eavesdropper from a scenario file; status, events, checks and scoring | Operations; Reference |
| Service | the same for many users, through an HTTP API and a web UI, with results and reports kept per run | Operations; Reference |
| Results | test summaries of the privacy scenarios, scored against ground truth | Results |

## Limits by design

VNAP-Secure runs on a single host. Stations exchange frames over a Docker bridge, which loses
nothing and has no range, so there is no radio model. Traffic is a handful of vehicles on
routes and crossings. Results are therefore comparisons between countermeasures under stated
conditions, not predictions for a road network. Certificate revocation, misbehaviour reporting
and encrypted delivery of certificate batches are outside the scope, as stated in the product
requirements.

The design proposals in `Specs/` describe where the project is meant to go next: adversarial
agents that evolve attacks and defences against each other, and measurements of the
computational and safety cost of pseudonym changes, weighed against privacy in a joint
experiment.

## How to read this document

This document collects the Markdown documentation of the repository as it stood when it was
generated. The parts follow the README's documentation table:

- **Overview:** the README and the architecture, with networks and trust boundaries.
- **Installation and build:** the VM, the image build and the certificate sets.
- **How it works:** security and chain verification, pseudonym change, certificate refill,
  vehicle movement, and the eavesdropper with its scoring.
- **Operations:** `vnapctl`, deploying and using the service, backups, monitoring and
  troubleshooting.
- **Reference:** the scenario format, configuration, container environment, control channels
  and the API.
- **Patch set:** each group of changes to Vanetza-NAP, with its reason and status.
- **Results:** the test summaries.
- **Appendix:** the changelog and source notes.

Links between documents are links within this PDF. Links to source files point to the
repository on GitHub. The Markdown files remain the source; this PDF is a snapshot of them.
