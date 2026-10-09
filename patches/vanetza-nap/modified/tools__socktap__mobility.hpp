#ifndef MOBILITY_HPP_SOCKTAP
#define MOBILITY_HPP_SOCKTAP

#include <vanetza/common/position_provider.hpp>
#include <vanetza/common/runtime.hpp>
#include <boost/program_options/options_description.hpp>
#include <boost/program_options/variables_map.hpp>
#include <mosquittopp.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <string>

/**
 * Position provider whose fix can be replaced at runtime (vnap-secure). Used instead of the
 * static "hardcoded GPS" provider: it starts at the configured position and follows updates
 * from the position control channel. Thread-safe: every reader thread gets its own snapshot.
 */
class ControlledPositionProvider : public vanetza::PositionProvider
{
public:
    explicit ControlledPositionProvider(const vanetza::PositionFix& initial);
    const vanetza::PositionFix& position_fix() override;
    void update(const vanetza::PositionFix&);

private:
    std::mutex m_mutex;
    vanetza::PositionFix m_fix;
};

/**
 * Position updates on the control channel (vnap-secure): subscribes to "<topic>" on a
 * dedicated MQTT connection and applies each update to the ControlledPositionProvider, which
 * feeds the CAM contents and the GeoNetworking position vector.
 *
 * Payload (JSON object): {"lat": <deg>, "lon": <deg>, "speed": <m/s>, "heading": <deg 0..360>,
 * "alt": <m>}; lat and lon are required. Retained messages are applied too (the last known
 * position is a sensible start).
 */
class PositionChannel : public mosqpp::mosquittopp
{
public:
    struct Options
    {
        std::string host;
        int port = 1883;
        std::string username;
        std::string password;
        std::string topic;
    };

    PositionChannel(const Options&, const std::string& client_id, ControlledPositionProvider&, const vanetza::Runtime&);
    ~PositionChannel();

private:
    void on_connect(int rc) override;
    void on_disconnect(int rc) override;
    void on_message(const struct mosquitto_message*) override;

    Options m_options;
    ControlledPositionProvider& m_provider;
    const vanetza::Runtime& m_runtime;
    std::atomic<unsigned long> m_updates { 0 };
    std::atomic<unsigned long> m_rejected { 0 };
};

void add_position_channel_options(boost::program_options::options_description&);

/** Create the position channel if --position-control-broker is given (nullptr otherwise) */
std::unique_ptr<PositionChannel> create_position_channel(const boost::program_options::variables_map&,
    vanetza::PositionProvider*, const vanetza::Runtime&, int station_id);

#endif /* MOBILITY_HPP_SOCKTAP */
