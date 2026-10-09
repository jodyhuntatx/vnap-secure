# Patches: thread safety

socktap receives and verifies packets on several threads, signs and sends on others, and
handles MQTT, DDS, timers and RSSI polling on still others. Running a sanitized build
(ThreadSanitizer) under load, mostly with `stress-naive-v3` (4 stations at 50 Hz), found the
races below. Several crashed socktap. The number of reports went from 69 to 17 in socktap and
Vanetza code, then to none. The remaining reports are inside the Zenoh library's
uninstrumented Rust threads and are most likely false positives
([troubleshooting](../operations/troubleshooting.md#known-limitations)).

| Patch | Files | Race and fix |
|---|---|---|
| Clock sync | `tools/socktap/time_trigger.{hpp,cpp}` | the runtime clock lagged wall time, so CAMs were rejected as `Invalid_Timestamp`; also a concurrent `schedule()` assertion crash. A 10 ms sync pulse keeps the runtime clock within 10 ms of wall time, and scheduling is serialized by a recursive mutex (the pulse only try-locks, keeping the `io_context` thread unblocked) |
| PRNG mutex | `vanetza/security/backend_cryptopp.{hpp,cpp}` | the shared CryptoPP random pool was used from several threads (v3 verification, key-check asserts); now under a mutex |
| Sign header policy | `vanetza/security/v{2,3}/sign_header_policy.{hpp,cpp}` | verification on several threads and signing on another accessed the policy's P2P request trackers unsynchronized, which aborted socktap (`PeerRequestTracker` assertion) under load; now locked. Reproduce with `stress-naive-v3` |
| v3 certificate cache | `vanetza/security/v3/certificate_cache.{hpp,cpp}` | reception threads stored certificates while others looked them up (data races and a SEGV in lookup); locked, and the short-digest index keeps pointers instead of rehash-invalidated iterators |
| v2 certificate cache | `vanetza/security/v2/certificate_cache.{hpp,cpp}` | concurrent inserts and lookups, and lookups modify the cache (expiry heap): data races and a corrupted heap (boost assertion abort); locked |
| Runtime clock and ASN.1 descriptors | `vanetza/common/manual_runtime.{hpp,cpp}`, `vanetza/asn1/asn1c_wrapper.hpp`, `vanetza/asn1/cam.hpp`, `tools/socktap/applications/cam_application.cpp` | reception threads read the runtime clocks while the main thread advances them (now atomic); `asn1c_wrapper_common::swap` wrote to the global type descriptors; `CompactR2DecodeGuard` swaps a global R2 CAM member descriptor, now under a process-wide recursive mutex that all CAM encode/decode/JSON paths take. Reports 69 → 38 |
| Routers | `tools/socktap/{router_context,time_trigger,dcc_passthrough,raw_socket_link}.{hpp,cpp}`, `pubsub.cpp`, `main.cpp` | each reception router was used by its reception thread, by timers and position updates on the main thread, and by the PubSub transmission thread. Now each router is guarded by its trigger's lock (main-thread timers only try-lock), worker threads wait for `RouterContext::start()`, the thread-to-trigger map is locked (new triggers created outside that lock, avoiding a lock-order inversion) and the link-layer callback is published safely. Reports 38 → 19 |
| PubSub, MQTT and DDS | `tools/socktap/{pubsub,dds,mqtt}.cpp`, `mqtt.hpp` | MQTT subscriptions were added while the mosquitto loop thread iterated them; callback threads inserted into the topic priority map; several threads used one UDP socket; DDS `operator[]` lookups inserted (and dereferenced) null publishers. Maps locked, lookups use `find()`, UDP sends serialized; also reads MQTT payloads by length instead of as NUL-terminated strings. Reports 19 → 17 |
| RSSI reader | `tools/socktap/rssi_reader.cpp` | the RSSI thread (nl80211 polling) inserts into and expires the RSSI/MCS maps and writes the channel survey while the receive thread reads them for every packet; on a real radio this could crash socktap. One mutex, never held across netlink I/O. Reports 2 → 0 |

**Upstream:** all candidates. They fix crashes and undefined behaviour that are independent
of this study.

## Testing

- **`stress-naive-v3`** must run its full duration without a crash line (`check`'s
  `errors.total`).
- **ThreadSanitizer:** to repeat the analysis, build socktap with `-fsanitize=thread` in a
  vanetza-nap tree, run a station with it in a scenario, and read the reports from its log.
