#pragma once
#include <vanetza/common/runtime.hpp>
#include <vanetza/security/private_key.hpp>
#include <vanetza/security/v3/certificate_provider.hpp>
#include <vanetza/security/v3/secured_message.hpp>  // sign_header_policy.hpp is not self-contained
#include <vanetza/security/v3/sign_header_policy.hpp>
#include <atomic>
#include <cstddef>
#include <vector>

namespace vanetza
{
namespace security
{
namespace v3
{

/**
 * \brief Certificate provider simulating an OBU pseudonym change scheme (TS 103 097 v1.3.1 / IEEE 1609.2)
 *
 * Rotates through a pool of pre-provisioned authorization tickets (issued by external
 * tooling, e.g. a butterfly batch from a PKI) on a fixed countdown. On every rotation the
 * sign header policy is asked to include the full new certificate in the next message,
 * so receivers learn it immediately, and the countdown restarts.
 *
 * Rotation runs as a Runtime callback. socktap's TimeTrigger executes all runtime callbacks
 * (including periodic CAM transmission) under one mutex, so a rotation cannot happen between
 * the certificate and private key lookups of a runtime-driven signing operation.
 *
 * \note Changing only the certificate does not make consecutive pseudonyms unlinkable:
 * lower-layer identifiers (MAC address, GeoNetworking address) are not changed by this class.
 */
class PseudonymCertificateProvider : public BaseCertificateProvider
{
public:
    /** One pre-provisioned pseudonym: an authorization ticket and its private key */
    struct Pseudonym
    {
        Certificate certificate;
        PrivateKey private_key;
    };

    /**
     * \param runtime runtime used to schedule rotations; must outlive this object
     * \param pool pseudonyms in rotation order (must not be empty)
     * \param lifetime duration each pseudonym is used before rotating to the next one
     *
     * AA/root certificates go into cache(), as for StaticCertificateProvider.
     */
    PseudonymCertificateProvider(Runtime& runtime, std::vector<Pseudonym> pool, Clock::duration lifetime);
    ~PseudonymCertificateProvider();

    /** Policy to notify after a rotation (e.g. set once the security entity is built) */
    void set_sign_header_policy(SignHeaderPolicy* policy) { m_sign_header_policy = policy; }

    const Certificate& own_certificate() override;
    const PrivateKey& own_private_key() override;

    std::size_t current_index() const { return m_index; }
    std::size_t pool_size() const { return m_pool.size(); }

private:
    void schedule_rotation();
    void rotate();

    Runtime& m_runtime;
    const std::vector<Pseudonym> m_pool;
    std::atomic<std::size_t> m_index;
    Clock::duration m_lifetime;
    SignHeaderPolicy* m_sign_header_policy = nullptr;
};

} // namespace v3
} // namespace security
} // namespace vanetza
