#include <vanetza/security/v3/pseudonym_certificate_provider.hpp>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace vanetza
{
namespace security
{
namespace v3
{

namespace
{

std::string to_hex(const Certificate& cert)
{
    auto digest = cert.calculate_digest();
    if (!digest) {
        return "?";
    }
    const HashedId8& id = *digest;
    std::ostringstream out;
    for (uint8_t byte : id) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
    }
    return out.str();
}

} // namespace

PseudonymCertificateProvider::PseudonymCertificateProvider(Runtime& runtime, std::vector<Pseudonym> pool,
        Clock::duration lifetime) :
    m_runtime(runtime), m_pool(std::move(pool)), m_index(0), m_lifetime(lifetime)
{
    if (m_pool.empty()) {
        throw std::invalid_argument("PseudonymCertificateProvider requires a non-empty pseudonym pool");
    }
    if (m_lifetime <= Clock::duration::zero()) {
        throw std::invalid_argument("PseudonymCertificateProvider requires a positive pseudonym lifetime");
    }

    std::cerr << "[PSEUDONYM] v3 pool of " << m_pool.size() << " certificate(s), rotating every "
              << std::chrono::duration_cast<std::chrono::seconds>(m_lifetime).count()
              << "s, starting at index 0 (certificate=" << to_hex(m_pool[0].certificate) << ")\n";
    schedule_rotation();
}

PseudonymCertificateProvider::~PseudonymCertificateProvider()
{
    m_runtime.cancel(this);
}

const Certificate& PseudonymCertificateProvider::own_certificate()
{
    return m_pool[m_index].certificate;
}

const PrivateKey& PseudonymCertificateProvider::own_private_key()
{
    return m_pool[m_index].private_key;
}

void PseudonymCertificateProvider::schedule_rotation()
{
    // "this" as scope lets the destructor cancel a pending rotation
    m_runtime.schedule(m_lifetime, [this](Clock::time_point) { rotate(); }, this);
}

void PseudonymCertificateProvider::rotate()
{
    const std::size_t previous = m_index;
    const std::size_t next = (previous + 1) % m_pool.size();
    m_index = next;

    std::cerr << "[PSEUDONYM] rotated pool index " << previous << " -> " << next << " (of " << m_pool.size()
              << "), certificate=" << to_hex(m_pool[next].certificate);
    if (next <= previous) {
        std::cerr << " [pool wrapped around, reusing an earlier pseudonym]";
    }
    std::cerr << "\n";

    // broadcast the new full certificate right away instead of waiting for the regular resend
    if (m_sign_header_policy) {
        m_sign_header_policy->request_certificate();
    }
    schedule_rotation();
}

} // namespace v3
} // namespace security
} // namespace vanetza
