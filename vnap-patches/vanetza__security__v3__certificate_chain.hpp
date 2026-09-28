#pragma once
#include <vanetza/common/clock.hpp>
#include <vanetza/security/certificate_validity.hpp>
#include <vanetza/security/hashed_id.hpp>
#include <vanetza/security/v3/certificate.hpp>
#include <mutex>
#include <unordered_set>

namespace vanetza
{
namespace security
{

// forward declaration
class Backend;

namespace v3
{

// forward declaration
class CertificateCache;

/**
 * Verify that a certificate's signature was made with the issuer's verification key.
 *
 * Signing input according to IEEE 1609.2 clause 5.3.1.2.2 and 6.4.3:
 * Hash( Hash(canonical tbsCertificate) || Hash(canonical issuer certificate) ),
 * where the issuer part is the hash of an empty string for self-signed certificates.
 *
 * \param backend backend for cryptographic operations
 * \param cert certificate whose signature is checked
 * \param issuer issuing certificate (pass cert itself for self-signed certificates)
 * \return true if signature is valid
 */
bool verify_certificate_signature(Backend& backend, const asn1::EtsiTs103097Certificate& cert,
    const asn1::EtsiTs103097Certificate& issuer);

/**
 * Check if a certificate's validity period lies within its issuer's validity period.
 */
bool validity_within_issuer(const asn1::EtsiTs103097Certificate& cert, const asn1::EtsiTs103097Certificate& issuer);

/**
 * Full-chain verification of v3 authorization tickets: AT -> AA -> trusted root CA.
 *
 * AA certificates are looked up in the certificate cache (e.g. loaded via --certificate-chain),
 * root CA certificates must be present in the trust store (e.g. loaded via --trusted-certificate).
 * Successfully verified certificate digests are memoized, so the additional signature
 * checks are only done once per AT/AA. Thread-safe with respect to the memoized sets;
 * cache and trust store must not be modified concurrently.
 */
class CertificateChainVerifier
{
public:
    CertificateChainVerifier(Backend& backend, const CertificateCache& trust_store);

    /**
     * Verify chain of a signing (AT) certificate
     * \param at authorization ticket
     * \param cache certificate cache used for AA lookup (may be null)
     * \param now current time for validity checks
     * \return valid or reason of failure
     */
    CertificateValidity verify(const asn1::EtsiTs103097Certificate& at, const CertificateCache* cache, Clock::time_point now);

    /**
     * Verify a CA certificate (AA) against the trust store, or a root CA itself
     * \param ca AA or root CA certificate
     * \param now current time for validity checks
     * \return valid or reason of failure
     */
    CertificateValidity verify_ca(const asn1::EtsiTs103097Certificate& ca, Clock::time_point now);

private:
    bool is_verified(const HashedId8&) const;
    void mark_verified(const HashedId8&);

    Backend& m_backend;
    const CertificateCache& m_trust_store;
    mutable std::mutex m_mutex;
    std::unordered_set<HashedId8> m_verified;
};

} // namespace v3
} // namespace security
} // namespace vanetza
