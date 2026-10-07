#include "pki_channel.hpp"
#include "rapidjson/document.h"
#include "rapidjson/stringbuffer.h"
#include "rapidjson/writer.h"
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace po = boost::program_options;
namespace fs = std::filesystem;
using vanetza::security::PseudonymControl;

namespace
{

void log(const std::string& line)
{
    std::cerr << ("[PKI] " + line + "\n"); // one insertion per line: other threads log too
}

bool base64_decode(const std::string& in, std::string& out)
{
    static const std::string chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    out.clear();
    unsigned value = 0;
    int bits = -8;
    for (char c : in) {
        if (c == '=') {
            break;
        }
        const auto pos = chars.find(c);
        if (pos == std::string::npos) {
            return false;
        }
        value = (value << 6) | static_cast<unsigned>(pos);
        bits += 6;
        if (bits >= 0) {
            out.push_back(static_cast<char>((value >> bits) & 0xFF));
            bits -= 8;
        }
    }
    return true;
}

void write_file(const fs::path& path, const std::string& data, bool secret)
{
    std::ofstream file(path, std::ios::binary | std::ios::trunc);
    file.write(data.data(), data.size());
    if (!file) {
        throw std::runtime_error("cannot write " + path.string());
    }
    file.close();
    if (secret) {
        fs::permissions(path, fs::perms::owner_read | fs::perms::owner_write, fs::perm_options::replace);
    }
}

} // namespace

PkiChannel::PkiChannel(const Options& options, const std::string& client_id, PseudonymControl& control,
        std::function<PseudonymBatchResult(const PseudonymBatch&)> loader, TimeTrigger& trigger, int station_id) :
    mosquittopp(client_id.c_str()),
    m_options(options),
    m_request_topic(options.topic + "/request"),
    m_batch_topic(options.topic + "/batch"),
    m_control(control),
    m_loader(std::move(loader)),
    m_trigger(trigger),
    m_station_id(station_id),
    m_backoff(options.retry)
{
    mosqpp::lib_init();
    if (!m_options.username.empty()) {
        username_pw_set(m_options.username.c_str(), m_options.password.c_str());
    }
    reconnect_delay_set(1, 30, true);
    loop_start();
    connect_async(m_options.host.c_str(), m_options.port, 60);
    log("certificate refill: batches of " + std::to_string(m_options.batch_size) + " requested at "
        + std::to_string(m_options.refill_at) + " unused, " + m_options.host + ":" + std::to_string(m_options.port)
        + " " + m_request_topic + " / " + m_batch_topic);
    // on the io_context thread, like every pseudonym change
    m_trigger.post([this]() {
        m_control.enable_refill(m_options.refill_at, [this](std::size_t unused) { low(unused); });
    });
}

PkiChannel::~PkiChannel()
{
    m_control.enable_refill(m_options.refill_at, nullptr);
    m_trigger.runtime().cancel(this);
    disconnect();
    loop_stop(true);
}

void PkiChannel::on_connect(int rc)
{
    if (rc == 0) {
        subscribe(nullptr, m_batch_topic.c_str(), 1);
        log("refill channel connected");
    } else {
        log(std::string("refill channel connection refused (") + mosqpp::connack_string(rc) + ")");
    }
}

void PkiChannel::on_disconnect(int rc)
{
    if (rc != 0) {
        log(std::string("refill channel lost (") + mosqpp::strerror(rc) + "), reconnecting");
    }
}

void PkiChannel::on_message(const struct mosquitto_message* message)
{
    if (message->retain) {
        return; // a stale batch must not be installed again on reconnect
    }
    std::string payload(static_cast<const char*>(message->payload), message->payloadlen);
    m_trigger.post([this, payload]() { handle_batch(payload); });
}

void PkiChannel::low(std::size_t unused)
{
    if (m_outstanding.empty()) {
        m_first_request = std::chrono::steady_clock::now();
        m_backoff = m_options.retry;
        send_request(unused);
    }
}

void PkiChannel::send_request(std::size_t unused)
{
    m_outstanding = std::to_string(m_station_id) + "-" + std::to_string(++m_sequence);
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> request(buffer);
    request.StartObject();
    request.Key("request_id"); request.String(m_outstanding.c_str());
    request.Key("unused"); request.Uint64(unused);
    request.Key("count"); request.Uint64(m_options.batch_size);
    request.EndObject();
    const int rc = publish(nullptr, m_request_topic.c_str(), buffer.GetSize(), buffer.GetString(), 1, false);
    log("batch requested: request " + m_outstanding + ", " + std::to_string(unused) + " unused, "
        + std::to_string(m_options.batch_size) + " wanted"
        + (rc == MOSQ_ERR_SUCCESS ? "" : std::string(" (not sent: ") + mosqpp::strerror(rc) + ")"));
    schedule_retry();
}

void PkiChannel::schedule_retry()
{
    const std::string id = m_outstanding;
    m_trigger.runtime().cancel(this);
    m_trigger.runtime().schedule(m_backoff, [this, id](vanetza::Clock::time_point) { on_retry(id); }, this);
    m_trigger.schedule();
}

void PkiChannel::on_retry(const std::string& request_id)
{
    if (m_outstanding != request_id) {
        return;
    }
    log("no answer to request " + request_id + " after " + std::to_string(m_backoff.count()) + " s, retrying");
    m_backoff = std::min(m_backoff * 2, std::chrono::seconds(120));
    send_request(m_control.unused_pseudonyms());
}

void PkiChannel::handle_batch(const std::string& payload)
{
    rapidjson::Document batch;
    if (batch.Parse(payload.c_str(), payload.size()).HasParseError() || !batch.IsObject()) {
        return log("ignoring a batch message that is not a JSON object");
    }
    const std::string id = batch.HasMember("request_id") && batch["request_id"].IsString()
        ? batch["request_id"].GetString() : std::string();
    if (id.empty() || id != m_outstanding) {
        return log("ignoring batch for request '" + id + "' (outstanding: '" + m_outstanding + "')");
    }
    if (batch.HasMember("error")) {
        // keep the request outstanding: the retry timer asks again
        return log("request " + id + " refused: "
            + std::string(batch["error"].IsString() ? batch["error"].GetString() : "?"));
    }
    if (!batch.HasMember("certificates") || !batch["certificates"].IsArray() || !batch.HasMember("keys")
            || !batch["keys"].IsArray() || batch["certificates"].Size() != batch["keys"].Size()) {
        return log("request " + id + ": malformed batch (certificates and keys must be arrays of equal length)");
    }

    // the loaders read files: stage the batch in a private directory, removed afterwards
    const fs::path dir = fs::path(m_options.batch_dir) / id;
    PseudonymBatch files;
    PseudonymBatchResult result;
    try {
        fs::create_directories(dir);
        fs::permissions(dir, fs::perms::owner_all, fs::perm_options::replace);
        const auto& certs = batch["certificates"];
        const auto& keys = batch["keys"];
        for (rapidjson::SizeType k = 0; k < certs.Size(); ++k) {
            std::string cert, key;
            if (!certs[k].IsString() || !keys[k].IsString() || !base64_decode(certs[k].GetString(), cert)
                    || !base64_decode(keys[k].GetString(), key)) {
                throw std::runtime_error("entry " + std::to_string(k) + " is not base64");
            }
            const auto cert_path = dir / (std::to_string(k) + ".cert");
            const auto key_path = dir / (std::to_string(k) + ".der");
            write_file(cert_path, cert, false);
            write_file(key_path, key, true);
            files.emplace_back(cert_path.string(), key_path.string());
        }
        result = m_loader(files);
    } catch (const std::exception& e) {
        log("request " + id + ": batch not installed: " + e.what());
        result = {};
    }
    std::error_code ignored;
    fs::remove_all(dir, ignored);

    for (const auto& reason : result.rejected) {
        log("batch certificate rejected: " + reason);
    }
    if (result.added == 0) {
        return; // still outstanding: the retry timer asks again
    }
    const auto refresh = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - m_first_request).count();
    const std::string issue = batch.HasMember("issue_ms") && batch["issue_ms"].IsNumber()
        ? std::to_string(static_cast<long>(batch["issue_ms"].GetDouble())) : std::string("?");
    const std::size_t unused = m_control.unused_pseudonyms();
    log("batch installed: request " + id + ", " + std::to_string(result.added) + " certificate(s), refresh "
        + std::to_string(refresh) + " ms (PKI issue " + issue + " ms), " + std::to_string(unused) + " unused");
    m_outstanding.clear();
    m_trigger.runtime().cancel(this);
    if (unused <= m_options.refill_at) {
        low(unused); // a small or partly rejected batch: ask for more
    }
}

void add_pki_channel_options(po::options_description& options)
{
    options.add_options()
        ("pki-refill-at", po::value<int>()->default_value(0),
            "Request a new certificate batch from the run's PKI service when this many unused pseudonyms "
            "are left (0: no refill; the pool wraps around). Uses the --pseudonym-control-broker connection "
            "settings; with refill no pseudonym is ever used twice.")
        ("pki-batch-size", po::value<int>()->default_value(8), "Certificates to request per batch.")
        ("pki-topic", po::value<std::string>(),
            "Topic prefix of the refill channel: requests on <prefix>/request, batches on <prefix>/batch "
            "(default vnap/pki/<station id>).")
        ("pki-retry", po::value<int>()->default_value(10),
            "Seconds before an unanswered request is repeated (doubling, up to 120).")
        ("pki-batch-dir", po::value<std::string>()->default_value("/tmp/vnap-pki"),
            "Private directory where received batches are staged while they are loaded.")
    ;
}

std::unique_ptr<PkiChannel> create_pki_channel(const po::variables_map& vm, PseudonymControl* control,
    std::function<PseudonymBatchResult(const PseudonymBatch&)> loader, TimeTrigger& trigger, int station_id)
{
    const int refill_at = vm["pki-refill-at"].as<int>();
    if (refill_at <= 0) {
        return nullptr;
    }
    if (!control || !loader) {
        log("WARNING: --pki-refill-at ignored, no pseudonym pool configured");
        return nullptr;
    }
    if (!vm.count("pseudonym-control-broker")) {
        throw std::runtime_error("--pki-refill-at needs --pseudonym-control-broker (the refill channel uses its broker)");
    }
    const int batch_size = vm["pki-batch-size"].as<int>();
    const int retry = vm["pki-retry"].as<int>();
    if (batch_size < 1 || batch_size > 256 || retry < 1) {
        throw std::runtime_error("--pki-batch-size must be 1..256 and --pki-retry at least 1 s");
    }
    PkiChannel::Options options;
    options.host = vm["pseudonym-control-broker"].as<std::string>();
    options.port = vm["pseudonym-control-port"].as<int>();
    if (vm.count("pseudonym-control-username")) {
        options.username = vm["pseudonym-control-username"].as<std::string>();
    }
    if (vm.count("pseudonym-control-password")) {
        options.password = vm["pseudonym-control-password"].as<std::string>();
    } else if (const char* password = std::getenv("PSEUDO_CONTROL_PASSWORD")) {
        options.password = password;
    }
    options.topic = vm.count("pki-topic") ? vm["pki-topic"].as<std::string>() : "vnap/pki/" + std::to_string(station_id);
    if (options.topic.empty() || options.topic.find_first_of("+#") != std::string::npos) {
        throw std::runtime_error("--pki-topic must be a non-empty topic without wildcards.");
    }
    options.refill_at = static_cast<std::size_t>(refill_at);
    options.batch_size = static_cast<std::size_t>(batch_size);
    options.retry = std::chrono::seconds(retry);
    options.batch_dir = vm["pki-batch-dir"].as<std::string>();
    return std::make_unique<PkiChannel>(options, "vanetza-pki-" + std::to_string(station_id), *control,
        std::move(loader), trigger, station_id);
}
