#include "dcc_passthrough.hpp"
#include "time_trigger.hpp"
#include <vanetza/access/data_request.hpp>
#include <vanetza/dcc/data_request.hpp>
#include <vanetza/dcc/interface.hpp>
#include <vanetza/dcc/mapping.hpp>
#include <vanetza/net/chunk_packet.hpp>
#include <iostream>
#include <mutex>

using namespace vanetza;

std::map<std::thread::id, TimeTrigger*> triggers_;
static std::mutex triggers_mutex_; // any thread may add its trigger on first use

DccPassthrough::DccPassthrough(access::Interface& access, boost::asio::io_context& io_context) :
        access_(access), io_context_(io_context) {}


void DccPassthrough::request(const dcc::DataRequest& request, std::unique_ptr<ChunkPacket> packet)
{
    if (!allow_packet_flow_) {
        std::cout << "ignored request because packet flow is suppressed\n";
        return;
    }

    const std::int64_t silent_until = silent_until_ns_.load();
    if (silent_until != 0) {
        const std::int64_t now = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
        if (now < silent_until) {
            ++silenced_frames_;
            return; // silent period: nothing goes on air
        }
        std::int64_t expected = silent_until;
        if (silent_until_ns_.compare_exchange_strong(expected, 0)) {
            std::cerr << "[IDCHANGE] silent period over, " << silenced_frames_.exchange(0)
                      << " frame(s) suppressed" << std::endl;
        }
    }

    get_trigger().schedule();

    access::DataRequest acc_req;
    acc_req.ether_type = request.ether_type;
    acc_req.source_addr = request.source_override ? *request.source_override : request.source;
    acc_req.destination_addr = request.destination;
    acc_req.access_category = dcc::map_profile_onto_ac(request.dcc_profile);
    access_.request(acc_req, std::move(packet));
}

void DccPassthrough::allow_packet_flow(bool allow)
{
    allow_packet_flow_ = allow;
}

void DccPassthrough::silence_for(std::chrono::milliseconds duration)
{
    silenced_frames_ = 0;
    silent_until_ns_ = std::chrono::duration_cast<std::chrono::nanoseconds>(
        (std::chrono::steady_clock::now() + duration).time_since_epoch()).count();
}

bool DccPassthrough::allow_packet_flow()
{
    return allow_packet_flow_;
}

TimeTrigger &DccPassthrough::get_trigger() {
    std::thread::id curr_id = std::this_thread::get_id();
    return this->get_trigger(curr_id);
}

TimeTrigger &DccPassthrough::get_trigger(std::thread::id id) {
    {
        std::lock_guard<std::mutex> lock(triggers_mutex_);
        auto found = triggers_.find(id);
        if (found != triggers_.end()) {
            return *found->second; // triggers are never removed, the reference stays valid
        }
    }
    // Create the trigger without the map lock: its constructor takes the trigger's own lock,
    // while threads holding a trigger's lock call get_trigger(), which would invert the order.
    // Each thread creates its own trigger (or the main thread does, before the thread starts),
    // so two creations for one id cannot race; the loser would be leaked, never destroyed,
    // because its timers already refer to it.
    TimeTrigger* created = new TimeTrigger(io_context_);
    std::lock_guard<std::mutex> lock(triggers_mutex_);
    return *triggers_.emplace(id, created).first->second;
}
