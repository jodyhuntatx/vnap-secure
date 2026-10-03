#pragma once
#include <vanetza/security/id_change_service.hpp>
#include <boost/optional/optional.hpp>
#include <cstddef>
#include <string>

namespace vanetza
{
namespace security
{

/**
 * \brief Interface of a certificate provider whose pseudonym (authorization ticket) is changed on request
 *
 * Changes are event-driven: the provider never changes its pseudonym on its own. Callers must
 * serialize change_pseudonym() with signing operations (e.g. by running it on the same runtime
 * as the applications), so a change cannot happen between certificate and private key lookups.
 */
class PseudonymControl
{
public:
    /** Outcome of a change request */
    struct Result
    {
        bool changed = false;
        std::size_t previous = 0; /*!< pool index before the request */
        std::size_t current = 0; /*!< pool index after the request */
        std::string certificate; /*!< HashedId8 of the current certificate as hex */
        std::string error; /*!< why the request was rejected (empty if changed) */
    };

    /**
     * Change to another pseudonym of the pool
     * \param index pool index to change to, or the next one (wrapping around) if none given
     * \return result of the change request
     */
    virtual Result change_pseudonym(boost::optional<std::size_t> index) = 0;

    virtual std::size_t current_pseudonym() const = 0;
    virtual std::size_t pseudonym_pool_size() const = 0;

    /**
     * ID change notification service (ETSI TS 102 723-8/-9): every pseudonym change runs its
     * two-phase commit with the new certificate's HashedId8 as id, so subscribed layers change
     * their identifiers together with the certificate. A change is refused while an ID-LOCK is
     * held; IDCHANGE-TRIGGER changes to the next pseudonym.
     */
    virtual IdChangeService& id_changes() = 0;

    virtual ~PseudonymControl() = default;
};

} // namespace security
} // namespace vanetza
