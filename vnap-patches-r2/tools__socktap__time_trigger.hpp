#ifndef TIME_TRIGGER_HPP_XRPGDYXO
#define TIME_TRIGGER_HPP_XRPGDYXO

#include <vanetza/common/manual_runtime.hpp>
#include <boost/asio/io_context.hpp>
#include <boost/asio/deadline_timer.hpp>
#include <boost/date_time/posix_time/posix_time_types.hpp>
#include <mutex>

class TimeTrigger
{
public:
    TimeTrigger(boost::asio::io_context&);
    vanetza::Runtime& runtime() { return runtime_; }
    void schedule();

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

