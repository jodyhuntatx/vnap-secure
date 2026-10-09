#include "id_change.hpp"
#include <cryptopp/sha.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdio>
#include <string>

using vanetza::security::IdChangeService;

namespace
{

std::atomic<std::uint32_t> station_id { 0 };

std::array<std::uint8_t, CryptoPP::SHA256::DIGESTSIZE> labelled_hash(const char* label, const IdChangeService::Id& id)
{
    std::array<std::uint8_t, CryptoPP::SHA256::DIGESTSIZE> digest;
    CryptoPP::SHA256 sha;
    sha.Update(reinterpret_cast<const CryptoPP::byte*>(label), std::char_traits<char>::length(label));
    sha.Update(id.data(), id.size());
    sha.Final(digest.data());
    return digest;
}

} // namespace

vanetza::MacAddress derive_mac_address(const IdChangeService::Id& id)
{
    const auto digest = labelled_hash("vnap id-change mac", id);
    vanetza::MacAddress mac;
    std::copy_n(digest.begin(), mac.octets.size(), mac.octets.begin());
    mac.octets[0] = (mac.octets[0] | 0x02) & 0xFE; // locally administered, unicast
    return mac;
}

std::uint32_t derive_station_id(const IdChangeService::Id& id)
{
    const auto digest = labelled_hash("vnap id-change station id", id);
    std::uint32_t value = (std::uint32_t(digest[0]) << 24) | (std::uint32_t(digest[1]) << 16) |
        (std::uint32_t(digest[2]) << 8) | digest[3];
    return value != 0 ? value : 1;
}

std::string to_hex(const IdChangeService::Id& id)
{
    char out[2 * 8 + 1];
    for (std::size_t i = 0; i < id.size(); ++i) {
        std::snprintf(out + 2 * i, 3, "%02x", id[i]);
    }
    return out;
}

IdChangeSubscription::IdChangeSubscription(IdChangeService& service, IdChangeService::Hook hook, std::string name) :
    m_service(service), m_handle(service.subscribe(std::move(hook), std::move(name)))
{
}

IdChangeSubscription::~IdChangeSubscription()
{
    m_service.unsubscribe(m_handle);
}

std::uint32_t own_station_id()
{
    return station_id.load();
}

void set_own_station_id(std::uint32_t id)
{
    station_id.store(id);
}
