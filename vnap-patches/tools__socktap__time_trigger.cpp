#include "time_trigger.hpp"
#include <boost/asio/post.hpp>
#include <boost/date_time/posix_time/posix_time.hpp>
#include <iostream>
#include <functional>

namespace asio = boost::asio;
namespace posix_time = boost::posix_time;
using namespace vanetza;

TimeTrigger::TimeTrigger(asio::io_context& io_context) :
    io_context_(io_context), timer_(io_context), sync_timer_(io_context), runtime_(Clock::at(now()))
{
    // std::cout << "Starting runtime at " << now() <<"\n";
    schedule();
    schedule_sync();
}

posix_time::ptime TimeTrigger::now() const
{
    return posix_time::microsec_clock::universal_time();
}

void TimeTrigger::schedule()
{
    // called from the io_context thread and from packet reception threads
    std::lock_guard<std::recursive_mutex> lock(schedule_mtx);
    update_runtime();
    auto next = runtime_.next();
    if (next < Clock::time_point::max()) {
        timer_.expires_at(Clock::at(next));
        timer_.async_wait(std::bind(&TimeTrigger::on_timeout, this, std::placeholders::_1));
    } else {
        timer_.cancel();
    }
}

void TimeTrigger::post(std::function<void()> fn)
{
    asio::post(io_context_, [this, fn]() {
        std::lock_guard<std::recursive_mutex> lock(schedule_mtx);
        fn();
    });
}

void TimeTrigger::on_timeout(const boost::system::error_code& ec)
{
    if (asio::error::operation_aborted != ec) {
        schedule();
    }
}

// Dedicated 10ms wall-clock sync pulse: keeps the runtime clock (used for signing
// generation time and verification windows) within 10ms of wall-clock time, even
// when no runtime events or received packets would otherwise advance it.
void TimeTrigger::schedule_sync()
{
    sync_timer_.expires_from_now(boost::posix_time::milliseconds(10));
    sync_timer_.async_wait(std::bind(&TimeTrigger::on_sync_timeout, this, std::placeholders::_1));
}

void TimeTrigger::on_sync_timeout(const boost::system::error_code& ec)
{
    if (asio::error::operation_aborted != ec) {
        schedule();       // advance runtime to wall clock, reschedule app event timer
        schedule_sync();  // arm next 10ms sync pulse
    }
}

void TimeTrigger::update_runtime()
{
    auto current_time = now();
    runtime_.trigger(Clock::at(current_time));
}
