#ifndef BKE_HPP_SOCKTAP
#define BKE_HPP_SOCKTAP

#include <cryptopp/integer.h>
#include <array>
#include <cstdint>
#include <string>

/**
 * Butterfly key expansion, end entity side (IEEE 1609.2.1, ETSI TS 102 941 clause 6.2.3.5),
 * NIST P-256 only (vnap-secure, certificate refill "option B").
 *
 * The vehicle keeps its caterpillar private key a and expansion key k. For i-period i and
 * index j the cocoon private key is a + f_k(i, j) mod n; the authorization authority certifies
 * the cocoon public key plus r*G and returns the offset r, so the certificate's private key is
 *   a + f_k(i, j) + r mod n
 * and only the vehicle can compute it. f_k is the expansion function of C-ITS-PKI
 * (src/crypto.py): x = prefix(32) | i(32) | j(32) | 0(32), f_k^int(x) = three AES-128 blocks
 * AES_k(x + t) XOR (x + t) for t = 1, 2, 3, f_k(x) = f_k^int(x) mod n.
 */
struct BkeSecrets
{
    CryptoPP::Integer caterpillar;            // a
    std::array<uint8_t, 16> expansion_key;    // k (AES-128)
};

/** Caterpillar private key (PKCS#8 DER) and expansion key (16 raw bytes) from files */
BkeSecrets load_bke_secrets(const std::string& caterpillar_key_path, const std::string& expansion_key_path);

/** f_k(i, j) mod n for signing keys (prefix 0) */
CryptoPP::Integer bke_expansion(const std::array<uint8_t, 16>& expansion_key, uint32_t i, uint32_t j);

/** Private key of the butterfly certificate (i, j) with AA offset r; throws if it is zero */
CryptoPP::Integer bke_butterfly_private_key(const BkeSecrets&, uint32_t i, uint32_t j, const CryptoPP::Integer& offset);

/** Write a P-256 private key as PKCS#8 DER (named curve), the format socktap loads */
void write_pkcs8_private_key(const CryptoPP::Integer& key, const std::string& path);

#endif /* BKE_HPP_SOCKTAP */
