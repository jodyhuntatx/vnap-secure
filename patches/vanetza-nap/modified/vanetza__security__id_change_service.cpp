#include <vanetza/security/id_change_service.hpp>
#include <algorithm>
#include <chrono>
#include <vector>

namespace vanetza
{
namespace security
{

const char* to_string(IdChangeService::Command command)
{
    switch (command) {
        case IdChangeService::Command::Prepare: return "PREPARE";
        case IdChangeService::Command::Commit: return "COMMIT";
        case IdChangeService::Command::Abort: return "ABORT";
        case IdChangeService::Command::Dereg: return "DEREG";
    }
    return "?";
}

IdChangeService::~IdChangeService()
{
    deregister_all();
}

IdChangeService::Subscription IdChangeService::subscribe(Hook hook, std::string name, ByteBuffer subscriber_data)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const Subscription handle = m_next_subscription++;
    m_subscribers.emplace(handle, Subscriber { std::move(hook), std::move(name), std::move(subscriber_data) });
    return handle;
}

void IdChangeService::unsubscribe(Subscription handle)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_subscribers.erase(handle);
}

void IdChangeService::set_trigger_handler(TriggerHandler handler)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_trigger = std::move(handler);
}

void IdChangeService::trigger()
{
    TriggerHandler handler;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        handler = m_trigger;
    }
    if (handler) {
        handler();
    }
}

IdChangeService::LockHandle IdChangeService::lock(unsigned duration_s)
{
    const auto now = LockClock::now();
    std::lock_guard<std::mutex> lock(m_mutex);
    const LockHandle handle = m_next_lock++;
    // Table 18: duration 0 to 2^8 - 1 seconds
    m_locks.emplace(handle, now + std::chrono::seconds(std::min(duration_s, 255u)));
    return handle;
}

void IdChangeService::unlock(LockHandle handle)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_locks.erase(handle);
}

bool IdChangeService::locked()
{
    return locked_for() > 0.0;
}

double IdChangeService::locked_for()
{
    const auto now = LockClock::now();
    std::lock_guard<std::mutex> lock(m_mutex);
    LockClock::time_point until = now;
    for (auto it = m_locks.begin(); it != m_locks.end();) {
        if (it->second <= now) {
            it = m_locks.erase(it); // released automatically after the duration
        } else {
            until = std::max(until, it->second);
            ++it;
        }
    }
    return std::chrono::duration<double>(until - now).count();
}

IdChangeService::Result IdChangeService::change(const Id& id, const std::function<bool()>& switch_identity)
{
    Result result;
    if (locked()) {
        result.error = "ID locked (ID-LOCK)";
        return result;
    }

    // snapshot: hooks are called without the lock held
    std::vector<std::pair<Subscription, Subscriber>> subscribers;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        subscribers.assign(m_subscribers.begin(), m_subscribers.end());
    }

    // phase 1: PREPARE; every subscriber that received it gets COMMIT or ABORT afterwards
    std::size_t prepared = 0;
    for (; prepared < subscribers.size(); ++prepared) {
        const Subscriber& s = subscribers[prepared].second;
        if (!s.hook(Command::Prepare, id, s.data)) {
            result.error = "aborted: " + s.name + " refused PREPARE";
            ++prepared; // it received PREPARE as well
            break;
        }
    }
    if (result.error.empty() && !switch_identity()) {
        result.error = "aborted: security entity could not switch identity";
    }

    // phase 2: COMMIT, or ABORT to all prepared subscribers
    const Command outcome = result.error.empty() ? Command::Commit : Command::Abort;
    for (std::size_t i = 0; i < prepared; ++i) {
        const Subscriber& s = subscribers[i].second;
        s.hook(outcome, id, s.data);
    }
    result.committed = outcome == Command::Commit;
    return result;
}

void IdChangeService::deregister_all()
{
    std::map<Subscription, Subscriber> subscribers;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        subscribers.swap(m_subscribers);
    }
    const Id none {};
    for (auto& s : subscribers) {
        s.second.hook(Command::Dereg, none, s.second.data);
    }
}

std::size_t IdChangeService::subscribers() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_subscribers.size();
}

} // namespace security
} // namespace vanetza
