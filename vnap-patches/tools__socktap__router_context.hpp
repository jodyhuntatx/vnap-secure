#ifndef ROUTER_CONTEXT_HPP_KIPUYBY2
#define ROUTER_CONTEXT_HPP_KIPUYBY2

#include "dcc_passthrough.hpp"
#include "link_layer.hpp"
#include <vanetza/btp/port_dispatcher.hpp>
#include <vanetza/common/position_provider.hpp>
#include <vanetza/geonet/mib.hpp>
#include <vanetza/geonet/router.hpp>
#include <array>
#include <list>
#include <memory>
#include <mutex>

class Application;
class TimeTrigger;

extern std::vector<std::unique_ptr<vanetza::geonet::Router>> routers;

void packet_reception_thread(int i);
void packet_processing_thread();
vanetza::geonet::Router* get_router(int i);

/**
 * Lock of routers[i] (its trigger's mutex). Threads other than the router's own reception
 * thread must hold it while using the router, e.g. PubSub transmission threads.
 */
std::recursive_mutex& get_router_mutex(int i);

/** Block until RouterContext::start() has been called (routers and applications ready). */
void wait_for_start();

class RouterContext
{
public:
    RouterContext(const vanetza::geonet::MIB&, TimeTrigger&, vanetza::PositionProvider&, vanetza::security::SecurityEntity*, bool ignore_own_messages_, bool ignore_rsu_messages_, int num_threads_, boost::asio::io_context&);
    ~RouterContext();
    void enable(Application*);
    void disable(Application*);

    /**
     * Allow/disallow transmissions without GNSS position fix
     *
     * \param flag true if transmissions shall be dropped when no GNSS position fix is available
     */
    void require_position_fix(bool flag);

    void set_link_layer(LinkLayer*);

    /**
     * Let the reception, processing and transmission threads start working. Call once after
     * set_link_layer() and after all applications are enabled; packets arriving earlier are queued.
     */
    void start();

    DccPassthrough& get_dccp();
    
    void log_packet_drop(vanetza::geonet::Router::PacketDropReason);

    vanetza::btp::PortDispatcher dispatcher_;

private:
    void indicate(vanetza::CohesivePacket&& packet, const vanetza::EthernetHeader& hdr);
    // void log_packet_drop(vanetza::geonet::Router::PacketDropReason);
    void update_position_vector();
    void update_packet_flow(const vanetza::geonet::LongPositionVector&);

    vanetza::geonet::MIB mib_;
    boost::asio::io_context& io_context_;
    vanetza::PositionProvider& positioning_;
    // vanetza::btp::PortDispatcher dispatcher_;
    std::unique_ptr<DccPassthrough> request_interface_;
    std::list<Application*> applications_;
    vanetza::security::SecurityEntity* security_entity_;
    TimeTrigger& trigger_;
    bool require_position_fix_ = false;
    bool ignore_own_messages = true;
    bool ignore_rsu_messages = false;
    int num_threads = 1;
};

#endif /* ROUTER_CONTEXT_HPP_KIPUYBY2 */
