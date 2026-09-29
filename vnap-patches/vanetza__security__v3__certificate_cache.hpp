#pragma once
#include <vanetza/security/hashed_id.hpp>
#include <vanetza/security/v3/certificate.hpp>
#include <mutex>
#include <unordered_map>
#include <unordered_set>

namespace vanetza
{
namespace security
{
namespace v3
{

/**
 * CertificateCache stores validated v1.3.1 certificates for later lookup.
 * Required for checking messages' signatures containing only a certificate digest.
 *
 * Thread-safe: socktap stores and announces certificates from several reception threads
 * while other threads look them up. Stored certificates are never removed or modified, so
 * pointers returned by lookup() stay valid for the lifetime of the cache. Removing entries
 * (e.g. expiry) would need a different lookup interface, such as returning copies.
 */
class CertificateCache
{
public:
    /**
     * Lookup certificate based on given digest
     * \param digest certificate digest
     * \return certificate matching digest
     */
    const Certificate* lookup(const HashedId8& digest) const;
    const Certificate* lookup(const HashedId3& digest) const;

    /**
     * Store a (pre-validated) certificate in cache
     * \param cert certificate
     */
    void store(Certificate cert);

    size_t size() const;

    /**
     * Announce a station with a given certificate digest.
     * \param digest certificate digest
     * \return true if digest was not known before
     */
    bool announce(const HashedId8& digest);

    /**
     * Test if a certificate digest is already known, i.e. either
     * its certificate is stored or at least the digest has been announced.
     * \param digest certificate digest
     * \return true if digest is known
     */
    bool is_known(const HashedId8& digest) const;

private:
    using CertificateMap = std::unordered_map<HashedId8, Certificate>;
    // element pointers stay valid across rehashing, iterators do not
    using ShortDigestMap = std::unordered_map<HashedId3, const Certificate*>;

    // TODO add bounded capacity and automatic removal of expired certificates
    CertificateMap m_storage;
    ShortDigestMap m_short_digests;
    std::unordered_set<HashedId8> m_digests;
    mutable std::mutex m_mutex;
};

} // namespace v3
} // namespace security
} // namespace vanetza
