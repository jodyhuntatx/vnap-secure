#ifndef TIME_TRIGGER_HPP_XRPGDYXO
#define TIME_TRIGGER_HPP_XRPGDYXO

#include <vanetza/common/manual_runtime.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/deadline_timer.hpp>
#include <boost/date_time/posix_time/posix_time_types.hpp>
#include <functional>
#include <mutex>

class TimeTrigger
{
public:
    TimeTrigger(boost::asio::io_context&);
    vanetza::Runtime& runtime() { return runtime_; }
    void schedule();

    /**
     * Run a function on the io_context thread, serialized with all runtime callbacks.
     * May be called from any thread (e.g. an MQTT client callback).
     */
    void post(std::function<void()>);

    /**
     * Lock guarding this trigger's runtime (and whatever is driven by it, e.g. a router).
     * Worker threads hold it while using the router; the timer handlers on the io_context
     * thread only try to take it and skip a round when a worker is busy.
     */
    std::recursive_mutex& mutex() { return schedule_mtx; }

private:
    boost::posix_time::ptime now() const;
    void on_timeout(const boost::system::error_code&);
    void update_runtime();
    void on_sync_timeout(const boost::system::error_code&);
    void schedule_sync();

    boost::asio::io_context& io_context_;
    boost::asio::deadline_timer timer_;
    boost::asio::deadline_timer sync_timer_;
    vanetza::ManualRuntime runtime_;
    std::recursive_mutex schedule_mtx;
};

#endif /* TIME_TRIGGER_HPP_XRPGDYXO */

