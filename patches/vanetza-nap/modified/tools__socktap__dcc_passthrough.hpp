#ifndef DCC_PASSTHROUGH_HPP_GSDFESAE
#define DCC_PASSTHROUGH_HPP_GSDFESAE

#include "time_trigger.hpp"
#include <atomic>
#include <chrono>
#include <cstdint>
#include <vanetza/access/interface.hpp>
#include <vanetza/dcc/data_request.hpp>
#include <vanetza/dcc/interface.hpp>
#include <vanetza/net/cohesive_packet.hpp>
#include <thread>

class DccPassthrough : public vanetza::dcc::RequestInterface
{
public:
    DccPassthrough(vanetza::access::Interface&, boost::asio::io_context&);

    void request(const vanetza::dcc::DataRequest& request, std::unique_ptr<vanetza::ChunkPacket> packet) override;

    void allow_packet_flow(bool allow);
    bool allow_packet_flow();

    /**
     * Radio silence: drop every outgoing frame (CAMs, beacons, injected messages) for the given
     * duration, e.g. the silent period after a pseudonym change (ETSI TR 103 415 clause 4.1.4).
     * Reception is not affected.
     */
    void silence_for(std::chrono::milliseconds);
    TimeTrigger& get_trigger();
    TimeTrigger& get_trigger(std::thread::id id);

private:
    vanetza::access::Interface& access_;
    boost::asio::io_context& io_context_;
    std::atomic<bool> allow_packet_flow_ { true }; // set on the main thread, read by sending threads
    std::atomic<std::int64_t> silent_until_ns_ { 0 }; // steady_clock; 0 = not silent
    std::atomic<std::uint64_t> silenced_frames_ { 0 }; // dropped in the current silent period
};

#endif /* DCC_PASSTHROUGH_HPP_GSDFESAE */
