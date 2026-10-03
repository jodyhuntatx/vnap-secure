#include <vanetza/security/v3/pseudonym_certificate_provider.hpp>
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

PseudonymCertificateProvider::PseudonymCertificateProvider(std::vector<Pseudonym> pool) :
    m_pool(std::move(pool)), m_index(0)
{
    if (m_pool.empty()) {
        throw std::invalid_argument("PseudonymCertificateProvider requires a non-empty pseudonym pool");
    }

    std::cerr << "[PSEUDONYM] v3 pool of " << m_pool.size() << " certificate(s), changed on events only, "
              << "starting at index 0 (certificate=" << to_hex(m_pool[0].certificate) << ")\n";

    // IDCHANGE-TRIGGER from any layer: change to the next pseudonym (unless ID-locked)
    m_id_changes.set_trigger_handler([this]() { change_pseudonym(boost::none); });
}

const Certificate& PseudonymCertificateProvider::own_certificate()
{
    return m_pool[m_index].certificate;
}

const PrivateKey& PseudonymCertificateProvider::own_private_key()
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
        // ID change notification (TS 102 723-8/-9 clause 6.3): PREPARE all subscribed layers,
        // switch the authorization ticket, then COMMIT -- or ABORT and keep the old one
        const IdChangeService::Id id = *m_pool[next].certificate.calculate_digest();
        auto outcome = m_id_changes.change(id, [&]() { m_index = next; return true; });
        result.changed = outcome.committed;
        result.error = outcome.error;
    }
    result.current = m_index;
    result.certificate = to_hex(m_pool[result.current].certificate);

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

} // namespace v3
} // namespace security
} // namespace vanetza
