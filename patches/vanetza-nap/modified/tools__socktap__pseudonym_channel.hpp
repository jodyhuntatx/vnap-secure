#ifndef PSEUDONYM_CHANNEL_HPP_K3QZ7WNM
#define PSEUDONYM_CHANNEL_HPP_K3QZ7WNM

#include "time_trigger.hpp"
#include <vanetza/security/pseudonym_control.hpp>
#include <boost/program_options/options_description.hpp>
#include <boost/program_options/variables_map.hpp>
#include <mosquittopp.h>
#include <chrono>
#include <memory>
#include <string>

/**
 * Pseudonym change event listener (vnap-secure)
 *
 * Subscribes to "<topic>/change" on a dedicated MQTT broker connection, separate from the
 * message pub/sub of the applications (own client, own broker host/port and credentials), and
 * changes the pseudonym on each valid event. Every event is answered on "<topic>/status".
 *
 * Event payload (JSON object, all members optional; "{}" changes to the next pseudonym):
 *   {"event_id": <string|number>, "index": <pool index>, "reason": <string>}
 * Without "index" the next pseudonym of the pool is used. Retained messages are rejected, so
 * a stale event is not replayed on (re)connect, and events closer together than the minimum
 * interval are rejected.
 *
 * Status payload:
 *   {"event_id": ..., "result": "changed"|"rejected", "error": <string>,
 *    "previous": <index>, "index": <index>, "pool_size": <n>, "certificate": <HashedId8 hex>}
 */
class PseudonymChannel : public mosqpp::mosquittopp
{
public:
    struct Options
    {
        std::string host;
        int port = 1883;
        std::string username;
        std::string password;
        std::string topic; /*!< topic prefix: events on <topic>/change, answers on <topic>/status */
        std::chrono::milliseconds min_interval { 1000 };
    };

    PseudonymChannel(const Options&, const std::string& client_id, vanetza::security::PseudonymControl&, TimeTrigger&);
    ~PseudonymChannel();

private:
    void on_connect(int rc) override;
    void on_disconnect(int rc) override;
    void on_message(const struct mosquitto_message*) override;

    void handle_event(const std::string& payload);
    void publish_status(const std::string& json);

    Options m_options;
    std::string m_change_topic;
    std::string m_status_topic;
    vanetza::security::PseudonymControl& m_control;
    TimeTrigger& m_trigger;
    std::chrono::steady_clock::time_point m_last_change; // only accessed on the io_context thread
    bool m_changed_once = false;
};

void add_pseudonym_channel_options(boost::program_options::options_description&);

/**
 * Create the listener if a pseudonym pool and --pseudonym-control-broker are configured
 * \return listener or nullptr
 */
std::unique_ptr<PseudonymChannel> create_pseudonym_channel(const boost::program_options::variables_map&,
    vanetza::security::PseudonymControl*, TimeTrigger&, int station_id);

#endif /* PSEUDONYM_CHANNEL_HPP_K3QZ7WNM */
