#ifndef CAM_APPLICATION_HPP_EUIC2VFR
#define CAM_APPLICATION_HPP_EUIC2VFR

#include "../application.hpp"
#include <vanetza/common/clock.hpp>
#include <vanetza/common/position_provider.hpp>
#include <vanetza/common/runtime.hpp>
#include <vanetza/asn1/cam.hpp>
#include <vanetza/security/id_change_service.hpp>
#include <atomic>
#include <climits>
#include <memory>
#include <random>
#include <math.h>

class CamApplication : public Application, public PubSub_application
{
public:
    CamApplication(vanetza::PositionProvider& positioning, vanetza::Runtime& rt, PubSub* pubsub_, config_t config_s_, metrics_t metrics_s_, vanetza::geonet::Router* timer_router_, int priority_, std::mutex& prom_mtx_);
    PortType port() override;
    void indicate(const DataIndication&, UpPacketPtr) override;
    void set_interval(vanetza::Clock::duration);
    /**
     * Restart the CAM timer at a random phase after every committed ID change (vnapctl
     * scenarios: full ID change), so the new identity's CAM timing does not continue the
     * old one's. Without this, the phase within the CAM interval links the identities.
     */
    void rephase_on_id_change(vanetza::security::IdChangeService&);
    void on_message(string topic, string mqtt_message, const std::vector<uint8_t>& bytes, bool is_encoded, double time_reception, string test, vanetza::geonet::Router* router);
    int priority;

private:
    void schedule_timer();
    void on_timer(vanetza::Clock::time_point);

    vanetza::PositionProvider& positioning_;
    vanetza::Runtime& runtime_;
    vanetza::Clock::duration cam_interval_;
    PubSub* pubsub;
    std::mutex& prom_mtx;
    config_t config_s;
    metrics_t metrics_s;
    vanetza::geonet::Router* timer_router;

    std::atomic<bool> rephase_ { false };  // set by the ID change hook, applied by on_timer
    std::mt19937 rephase_rng_ { std::random_device{}() };
    std::shared_ptr<void> id_change_subscription_;

    // State for acceleration/yaw rate calculation
    double time_speed = 0;
    double time_heading = 0;
    long last_speed = LLONG_MIN;
    long last_heading = LLONG_MIN;

    bool isNewInfo(long stationID, long latitude, long longitude, double speed, long heading, double time_reception);
    long double calcDistance(long double lat1, long double long1, long double lat2, long double long2);
    long double toRadians(const long double & degree);

};

#endif /* CAM_APPLICATION_HPP_EUIC2VFR */
