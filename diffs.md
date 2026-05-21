# Change Summary

---

## Build Mods

### `CMakeLists.txt`: Turn `BUILD_CERTIFY` on

```diff
- option(BUILD_CERTIFY "Build certify application" OFF)
+ option(BUILD_CERTIFY "Build certify application" ON)
```

---

### `Dockerfile`: Trigger certify build & copy to `/usr/bin`

```diff
+ RUN cmake --build . --target certify -j $(nproc)
+ RUN cp /vanetza/bin/certify /usr/local/bin/certify
+ COPY --from=build /vanetza/bin/certify /usr/local/bin/certify
```

---

### `entrypoint.sh`: Add logic to run with `security=[certs | none]`

```diff
- /usr/local/bin/socktap -c /config.ini
+ if [ -n "$SECURITY" ] && [ $SECURITY = certs ]; then
+     echo "Running with security=certs."
+     set -x
+     /usr/local/bin/socktap \
+         --config /config.ini \
+         --security certs \
+         --certificate $AT_CERT \
+         --certificate-key $AT_KEY \
+         --certificate-chain $AA_CERT \
+         --certificate-chain $ROOT_CERT
+     set +x
+ else
+     echo "Running with security=none."
+     /usr/local/bin/socktap -c /config.ini
+ fi
```

---

## Parser Fixes

### `security/secured_message.cpp`: Uncomment protocol version parsing

```diff
- //ar >> protocol_version;
+     ar >> protocol_version;
```

---

### `security/basic_header.cpp`: Delete redundant secure header parsing

```diff
-
- if (hdr.next_header == vanetza::geonet::NextHeaderBasic::
-     uint8_t secureProtocolVersion;
-     deserialize(secureProtocolVersion, ar);
-     if(secureProtocolVersion == 3) {
-         hdr.next_header = vanetza::geonet::NextHeaderBasi
-         uint8_t ignore;
-         uint8_t i = 1;
-         while(1) {
-             deserialize(ignore, ar);
-             if (ignore == 0x20) break;
-             else i++;
-         }
-         hdr.reserved = i;
-     } else {
-         uint8_t ignore;
-         deserialize(ignore, ar);
-     }
- } else {
-     uint8_t ignore;
-     deserialize(ignore, ar);
- }
  } // namespace vanetza
```

---

### `security/common_header`: Read first byte from archive

```diff
- uint8_t nextHeaderAndReserved = 0x20;
- //deserialize(nextHeaderAndReserved, ar);
+ uint8_t nextHeaderAndReserved = 0;
+ deserialize(nextHeaderAndReserved, ar);
```

---

## LRU Cache Fixes

### `security/backend_cryptopp.hpp`: Mutex defined to protect cache access

```diff
+ #include <mutex>
+     std::mutex m_mutex; // guards m_prng and both LRU caches
```

---

### `security/backend_cryptopp.cpp`: Mutex implemented to protect cache access

```diff
- return sign_data(m_private_cache[generic_key], data);
+ std::lock_guard<std::mutex> lock(m_mutex);
+ PrivateKey key = m_private_cache[generic_key]; // copy under lock
+ return sign_data(key, data);

- return verify_data(m_public_cache[generic_key], msg, sigb
+ PublicKey key;
+ {
+     std::lock_guard<std::mutex> lock(m_mutex);
+     key = m_public_cache[generic_key]; // copy under lock
+ }
+ return verify_data(key, msg, sigbuf);
```

---

### `security/ecdsa256.cpp`: Make public key hash deterministic

```diff
- boost::hash_combine(seed, k.x);
- boost::hash_combine(seed, k.y);
+ boost::hash_range(seed, k.x.begin(), k.x.end());
+ boost::hash_range(seed, k.y.begin(), k.y.end());
```
