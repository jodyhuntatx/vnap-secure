#pragma once
#include <vanetza/security/pseudonym_control.hpp>
#include <vanetza/security/v2/certificate_provider.hpp>
#include <vanetza/security/v2/sign_header_policy.hpp>
#include <atomic>
#include <cstddef>
#include <list>
#include <vector>

namespace vanetza
{
namespace security
{
namespace v2
{

/**
 * \brief Certificate provider simulating an OBU pseudonym change scheme (TS 103 097 v1.2.1)
 *
 * Holds a pool of pre-provisioned authorization tickets (issued by external tooling, e.g. a
 * butterfly batch from a PKI) and changes to another one only when change_pseudonym() is
 * called, i.e. on an external pseudonym change event. After each change the sign header
 * policy is asked to include the full new certificate in the next message, so receivers
 * learn it immediately.
 *
 * change_pseudonym() must be serialized with signing, see PseudonymControl. socktap runs it
 * under the TimeTrigger mutex that also guards all runtime callbacks (e.g. CAM transmission).
 *
 * \note Changing only the certificate does not make consecutive pseudonyms unlinkable:
 * lower-layer identifiers (MAC address, GeoNetworking address) are not changed by this class.
 */
class PseudonymCertificateProvider : public CertificateProvider, public PseudonymControl
{
public:
    /** One pre-provisioned pseudonym: an authorization ticket and its private key */
    struct Pseudonym
    {
        Certificate certificate;
        ecdsa256::PrivateKey private_key;
    };

    /**
     * \param pool pseudonyms of the pool (must not be empty), index 0 is used first
     * \param chain certificate chain (AA) shared by all pseudonyms of the pool
     */
    explicit PseudonymCertificateProvider(std::vector<Pseudonym> pool, std::list<Certificate> chain = {});

    /** Policy to notify after a change (e.g. set once the security entity is built) */
    void set_sign_header_policy(SignHeaderPolicy* policy) { m_sign_header_policy = policy; }

    const Certificate& own_certificate() override;
    std::list<Certificate> own_chain() override;
    const ecdsa256::PrivateKey& own_private_key() override;

    Result change_pseudonym(boost::optional<std::size_t> index) override;
    std::size_t current_pseudonym() const override { return m_index; }
    std::size_t pseudonym_pool_size() const override { return m_pool.size(); }
    IdChangeService& id_changes() override { return m_id_changes; }

private:
    const std::vector<Pseudonym> m_pool;
    std::atomic<std::size_t> m_index;
    SignHeaderPolicy* m_sign_header_policy = nullptr;
    IdChangeService m_id_changes; // last member: destroyed (DEREG) first
    std::list<Certificate> m_chain;
};

} // namespace v2
} // namespace security
} // namespace vanetza
