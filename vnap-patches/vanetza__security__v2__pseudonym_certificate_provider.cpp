#include <vanetza/security/v2/pseudonym_certificate_provider.hpp>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace vanetza
{
namespace security
{
namespace v2
{

namespace
{

std::string to_hex(const HashedId8& id)
{
    std::ostringstream out;
    for (uint8_t byte : id) {
        out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
    }
    return out.str();
}

} // namespace

PseudonymCertificateProvider::PseudonymCertificateProvider(std::vector<Pseudonym> pool, std::list<Certificate> chain) :
    m_pool(std::move(pool)), m_index(0), m_chain(std::move(chain))
{
    if (m_pool.empty()) {
        throw std::invalid_argument("PseudonymCertificateProvider requires a non-empty pseudonym pool");
    }

    std::cerr << "[PSEUDONYM] v2 pool of " << m_pool.size() << " certificate(s), changed on events only, "
              << "starting at index 0 (certificate=" << to_hex(calculate_hash(m_pool[0].certificate)) << ")\n";
}

const Certificate& PseudonymCertificateProvider::own_certificate()
{
    return m_pool[m_index].certificate;
}

std::list<Certificate> PseudonymCertificateProvider::own_chain()
{
    return m_chain;
}

const ecdsa256::PrivateKey& PseudonymCertificateProvider::own_private_key()
{
    return m_pool[m_index].private_key;
}

PseudonymControl::Result PseudonymCertificateProvider::change_pseudonym(boost::optional<std::size_t> index)
{
    Result result;
    result.previous = m_index;
    const std::size_t next = index ? *index : (result.previous + 1) % m_pool.size();

    if (next >= m_pool.size()) {
        result.error = "index out of range";
    } else if (next == result.previous) {
        result.error = "pseudonym already in use";
    } else {
        m_index = next;
        result.changed = true;
    }
    result.current = m_index;
    result.certificate = to_hex(calculate_hash(m_pool[result.current].certificate));

    if (!result.changed) {
        std::cerr << "[PSEUDONYM] change to index " << next << " rejected: " << result.error << "\n";
        return result;
    }

    std::cerr << "[PSEUDONYM] changed pool index " << result.previous << " -> " << result.current
              << " (of " << m_pool.size() << "), certificate=" << result.certificate;
    if (!index && result.current < result.previous) {
        std::cerr << " [pool wrapped around, reusing an earlier pseudonym]";
    }
    std::cerr << "\n";

    // broadcast the new full certificate right away instead of waiting for the regular resend
    if (m_sign_header_policy) {
        m_sign_header_policy->request_certificate();
    }
    return result;
}

} // namespace v2
} // namespace security
} // namespace vanetza
