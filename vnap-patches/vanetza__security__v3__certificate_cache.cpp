#include <vanetza/security/v3/certificate_cache.hpp>
#include <boost/optional/optional.hpp>

namespace vanetza
{
namespace security
{
namespace v3
{

const Certificate* CertificateCache::lookup(const HashedId8& digest) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto found = m_storage.find(digest);
    if (found != m_storage.end()) {
        return &found->second;
    } else {
        return nullptr;
    }
}

const Certificate* CertificateCache::lookup(const HashedId3& digest) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto found = m_short_digests.find(digest);
    if (found != m_short_digests.end()) {
        return found->second;
    } else {
        return nullptr;
    }
}

void CertificateCache::store(Certificate cert)
{
    auto maybe_hash = cert.calculate_digest(); // outside the lock: encoding and hashing
    if (maybe_hash) {
        std::lock_guard<std::mutex> lock(m_mutex);
        CertificateMap::iterator it;
        bool inserted;
        std::tie(it, inserted) = m_storage.emplace(*maybe_hash, std::move(cert));
        if (inserted) {
            m_short_digests.emplace(truncate(*maybe_hash), &it->second);
            m_digests.insert(*maybe_hash);
        }
    }
}

size_t CertificateCache::size() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_storage.size();
}

bool CertificateCache::announce(const HashedId8& digest)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    bool inserted = false;
    std::tie(std::ignore, inserted) = m_digests.insert(digest);
    return inserted;
}

bool CertificateCache::is_known(const HashedId8& digest) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_digests.find(digest) != m_digests.end();
}

} // namespace v3
} // namespace security
} // namespace vanetza
