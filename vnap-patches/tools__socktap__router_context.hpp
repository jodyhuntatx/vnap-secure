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
#include <vector>
#include "id_change.hpp"

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

    /**
     * Subscribe the network and transport layer to ID change events (ETSI TS 102 723-8 SN-SAP):
     * on PREPARE all routers are locked, so nothing is sent with the old identifiers until COMMIT
     * or ABORT; on COMMIT every router gets a GN address with a new MAC (also the link-layer
     * source address of its frames) derived from the event's id.
     */
    void subscribe_id_changes(vanetza::security::IdChangeService&);

    DccPassthrough& get_dccp();
    
    void log_packet_drop(vanetza::geonet::Router::PacketDropReason);

    vanetza::btp::PortDispatcher dispatcher_;

private:
    void indicate(vanetza::CohesivePacket&& packet, const vanetza::EthernetHeader& hdr);
    // void log_packet_drop(vanetza::geonet::Router::PacketDropReason);
    void update_position_vector();
    bool on_id_change(vanetza::security::IdChangeService::Command, const vanetza::security::IdChangeService::Id&);
    vanetza::MacAddress own_mac();
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
    // own MAC for filtering own frames; changes on ID change COMMIT, read by the link thread
    std::mutex own_mac_mutex_;
    vanetza::MacAddress own_mac_;
    // ID change in progress: router locks held from PREPARE to COMMIT/ABORT, new MAC
    std::vector<std::unique_lock<std::recursive_mutex>> id_change_locks_;
    vanetza::MacAddress id_change_mac_;
    std::unique_ptr<IdChangeSubscription> id_change_subscription_;
};

#endif /* ROUTER_CONTEXT_HPP_KIPUYBY2 */
