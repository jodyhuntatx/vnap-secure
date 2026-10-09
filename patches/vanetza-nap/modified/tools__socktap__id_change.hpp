#ifndef ID_CHANGE_HPP_SOCKTAP
#define ID_CHANGE_HPP_SOCKTAP

#include <vanetza/net/mac_address.hpp>
#include <vanetza/security/id_change_service.hpp>
#include <cstdint>
#include <string>

/**
 * Identifiers derived from the 8-octet id of an ID change event (ETSI TS 102 723-8/-9), which
 * is the HashedId8 of the new authorization ticket. Each identifier is taken from SHA-256 over
 * a label and the id, so the identifiers do not share bytes with each other or the certificate.
 */
vanetza::MacAddress derive_mac_address(const vanetza::security::IdChangeService::Id&); // locally administered unicast
std::uint32_t derive_station_id(const vanetza::security::IdChangeService::Id&);       // 1 .. 2^32-1

std::string to_hex(const vanetza::security::IdChangeService::Id&);

/** IDCHANGE-SUBSCRIBE for the lifetime of this object (IDCHANGE-UNSUBSCRIBE on destruction) */
class IdChangeSubscription
{
public:
    IdChangeSubscription(vanetza::security::IdChangeService&, vanetza::security::IdChangeService::Hook, std::string name);
    ~IdChangeSubscription();
    IdChangeSubscription(const IdChangeSubscription&) = delete;
    IdChangeSubscription& operator=(const IdChangeSubscription&) = delete;

private:
    vanetza::security::IdChangeService& m_service;
    vanetza::security::IdChangeService::Subscription m_handle;
};

/**
 * Station ID this station puts in its own messages (facilities layer). Starts with the
 * configured station ID and changes on an ID change COMMIT; local metadata such as the
 * receiverID of published messages keeps the configured ID.
 */
std::uint32_t own_station_id();
void set_own_station_id(std::uint32_t);

#endif /* ID_CHANGE_HPP_SOCKTAP */
