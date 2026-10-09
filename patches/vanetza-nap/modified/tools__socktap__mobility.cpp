#include "mobility.hpp"
#include "rapidjson/document.h"
#include <vanetza/units/angle.hpp>
#include <vanetza/units/length.hpp>
#include <vanetza/units/velocity.hpp>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace po = boost::program_options;
using namespace vanetza;

namespace
{

// one insertion per line: socktap threads share std::cerr and vnapctl parses the lines
void log(const std::string& line)
{
    std::cerr << ("[MOBILITY] " + line + "\n");
}

} // namespace

ControlledPositionProvider::ControlledPositionProvider(const PositionFix& initial) : m_fix(initial)
{
}

const PositionFix& ControlledPositionProvider::position_fix()
{
    // callers keep the reference for a while and run on several threads: hand out a per-thread copy
    thread_local PositionFix snapshot;
    std::lock_guard<std::mutex> lock(m_mutex);
    snapshot = m_fix;
    return snapshot;
}

void ControlledPositionProvider::update(const PositionFix& fix)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_fix = fix;
}

PositionChannel::PositionChannel(const Options& options, const std::string& client_id,
        ControlledPositionProvider& provider, const Runtime& runtime) :
    mosquittopp(client_id.c_str()), m_options(options), m_provider(provider), m_runtime(runtime)
{
    mosqpp::lib_init();
    if (!m_options.username.empty()) {
        username_pw_set(m_options.username.c_str(), m_options.password.c_str());
    }
    reconnect_delay_set(1, 30, true);
    loop_start();
    int rc = connect_async(m_options.host.c_str(), m_options.port, 60);
    log("position channel " + m_options.host + ":" + std::to_string(m_options.port) + ", listening on " + m_options.topic +
        (rc != MOSQ_ERR_SUCCESS ? std::string(" (connect: ") + mosqpp::strerror(rc) + ", retrying)" : std::string()));
}

PositionChannel::~PositionChannel()
{
    disconnect();
    loop_stop(true);
}

void PositionChannel::on_connect(int rc)
{
    if (rc == 0) {
        subscribe(nullptr, m_options.topic.c_str(), 0);
        log("position channel connected");
    } else {
        log(std::string("position channel connection refused (") + mosqpp::connack_string(rc) + ")");
    }
}

void PositionChannel::on_disconnect(int rc)
{
    if (rc != 0) {
        log(std::string("position channel lost (") + mosqpp::strerror(rc) + "), reconnecting");
    }
}

void PositionChannel::on_message(const struct mosquitto_message* message)
{
    rapidjson::Document doc;
    const char* payload = static_cast<const char*>(message->payload);
    auto reject = [this](const char* why) {
        if (m_rejected++ < 5) { // do not flood the log at the update rate
            log(std::string("position update rejected: ") + why);
        }
    };
    if (!payload || doc.Parse(payload, message->payloadlen).HasParseError() || !doc.IsObject()) {
        return reject("payload is not a JSON object");
    }
    if (!doc.HasMember("lat") || !doc["lat"].IsNumber() || !doc.HasMember("lon") || !doc["lon"].IsNumber()) {
        return reject("lat and lon are required numbers");
    }
    const double lat = doc["lat"].GetDouble();
    const double lon = doc["lon"].GetDouble();
    if (!(lat >= -90.0 && lat <= 90.0 && lon >= -180.0 && lon <= 180.0)) {
        return reject("lat/lon out of range");
    }

    PositionFix fix;
    fix.timestamp = m_runtime.now();
    fix.latitude = lat * units::degree;
    fix.longitude = lon * units::degree;
    fix.confidence.semi_major = 1 * units::si::meter;
    fix.confidence.semi_minor = fix.confidence.semi_major;
    if (doc.HasMember("speed") && doc["speed"].IsNumber()) {
        const double speed = doc["speed"].GetDouble();
        if (!(speed >= 0.0 && speed <= 163.82)) { // CAM SpeedValue range (0.01 m/s units)
            return reject("speed must be 0..163.82 m/s");
        }
        fix.speed.assign(speed * units::si::meter_per_second, 0.1 * units::si::meter_per_second);
    }
    if (doc.HasMember("heading") && doc["heading"].IsNumber()) {
        double heading = std::fmod(doc["heading"].GetDouble(), 360.0);
        if (heading < 0.0) {
            heading += 360.0;
        }
        const auto north = units::TrueNorth::from_value(0.0);
        fix.course.assign(north + heading * units::degree, north + 1.0 * units::degree);
    }
    if (doc.HasMember("alt") && doc["alt"].IsNumber()) {
        fix.altitude = ConfidentQuantity<units::Length>(doc["alt"].GetDouble() * units::si::meter, 1.0 * units::si::meter);
    }
    m_provider.update(fix);
    if (m_updates++ == 0) {
        std::ostringstream line;
        line << "first position update: lat " << lat << " lon " << lon;
        log(line.str());
    }
}

void add_position_channel_options(po::options_description& options)
{
    options.add_options()
        ("position-control-broker", po::value<std::string>(),
            "MQTT broker of the position control channel: the station's position (CAM contents and "
            "GeoNetworking position vector) follows updates on --position-control-topic.")
        ("position-control-port", po::value<int>()->default_value(1883), "Port of --position-control-broker.")
        ("position-control-topic", po::value<std::string>(),
            "Topic with position updates (default vnap/position/<station id>).")
        ("position-control-username", po::value<std::string>(), "Username for --position-control-broker.")
        ("position-control-password", po::value<std::string>(),
            "Password for --position-control-broker (default: environment variable POSITION_CONTROL_PASSWORD).")
    ;
}

std::unique_ptr<PositionChannel> create_position_channel(const po::variables_map& vm, PositionProvider* provider,
    const Runtime& runtime, int station_id)
{
    if (!vm.count("position-control-broker")) {
        return nullptr;
    }
    auto* controlled = dynamic_cast<ControlledPositionProvider*>(provider);
    if (!controlled) {
        throw std::runtime_error("--position-control-broker needs the static position provider "
                                 "(use_hardcoded_gps / VANETZA_USE_HARDCODED_GPS=true), not gpsd");
    }
    PositionChannel::Options options;
    options.host = vm["position-control-broker"].as<std::string>();
    options.port = vm["position-control-port"].as<int>();
    options.topic = vm.count("position-control-topic") ? vm["position-control-topic"].as<std::string>()
        : "vnap/position/" + std::to_string(station_id);
    if (vm.count("position-control-username")) {
        options.username = vm["position-control-username"].as<std::string>();
    }
    if (vm.count("position-control-password")) {
        options.password = vm["position-control-password"].as<std::string>();
    } else if (const char* password = std::getenv("POSITION_CONTROL_PASSWORD")) {
        options.password = password;
    }
    return std::make_unique<PositionChannel>(options, "vanetza-position-" + std::to_string(station_id), *controlled, runtime);
}
