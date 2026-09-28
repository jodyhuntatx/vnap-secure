// vnap-msgcheck: verify a signed message file with vanetza's own security stack.
//
// Decodes the message with vanetza's parser (v2 SecuredMessage or v3 EtsiTs103097Data)
// and runs it through StraightVerifyService configured like socktap: AA certificate in
// the certificate cache, root CA in the trust store, v3 full-chain verification on.
// The runtime clock is set just after the message's generation time, so files signed
// earlier pass the generation-time window; certificate validity is still checked.
// The AT is preloaded into the certificate cache, as a receiver would have learnt it
// from an earlier certificate-signed message, so digest-signed messages resolve too
// (v3 still checks the AT's full chain; v2 trusts cached ATs, as vanetza does).
//
// usage: vnap-msgcheck v2|v3 <message> <at.cert> <aa.cert> <root.cert>
// exit code 0 only if the verification report is Success.
//
// usage: vnap-msgcheck decode-v2|decode-v3 <message>
// parses any secured message (incl. encrypted ones, which vanetza cannot decrypt)
// with vanetza's decoder and prints its structure; exit code 0 if it parses.

#include <vanetza/common/manual_runtime.hpp>
#include <vanetza/common/serialization.hpp>
#include <vanetza/common/stored_position_provider.hpp>
#include <vanetza/security/backend.hpp>
#include <vanetza/security/straight_verify_service.hpp>
#include <vanetza/security/v2/certificate_cache.hpp>
#include <vanetza/security/v2/default_certificate_validator.hpp>
#include <vanetza/security/v2/persistence.hpp>
#include <vanetza/security/v2/secured_message.hpp>
#include <vanetza/security/v2/sign_header_policy.hpp>
#include <vanetza/security/v2/static_certificate_provider.hpp>
#include <vanetza/security/v2/trust_store.hpp>
#include <vanetza/security/v3/certificate_cache.hpp>
#include <vanetza/security/v3/certificate_validator.hpp>
#include <vanetza/security/v3/persistence.hpp>
#include <vanetza/security/v3/secured_message.hpp>
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

using namespace vanetza;
using namespace vanetza::security;

namespace
{

ByteBuffer read_file(const std::string& path)
{
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("cannot open " + path);
    }
    return ByteBuffer((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

Clock::time_point just_after(std::uint64_t generation_time_us)
{
    return Clock::time_point { std::chrono::microseconds(generation_time_us) + std::chrono::milliseconds(100) };
}

int report(const VerifyConfirm& confirm)
{
    std::cout << "report=" << to_string(confirm.report);
    if (confirm.certificate_validity) {
        std::cout << " certificate=valid";
    } else {
        std::cout << " certificate_invalid_reason=" << static_cast<int>(confirm.certificate_validity.reason());
    }
    std::cout << "\n";
    return confirm.report == VerificationReport::Success ? 0 : 1;
}

int check_v3(const std::string& msg_path, const std::string& at_path, const std::string& aa_path, const std::string& root_path)
{
    v3::SecuredMessage message;
    if (!message.decode(read_file(msg_path))) {
        std::cout << "decode=FAIL (not a valid EtsiTs103097Data)\n";
        return 2;
    }
    auto generation_time = message.generation_time();
    if (!generation_time) {
        std::cout << "decode=OK but generationTime missing\n";
        return 2;
    }
    std::cout << "decode=OK protocol_version=" << int(message.protocol_version())
        << " its_aid=" << message.its_aid() << "\n";

    ManualRuntime runtime(just_after(*generation_time));
    StoredPositionProvider positioning;
    auto backend = create_backend("default");

    v3::CertificateCache cache;
    cache.store(v3::load_certificate_from_file(aa_path));
    cache.store(v3::load_certificate_from_file(at_path));
    v3::CertificateCache trust_store;
    trust_store.store(v3::load_certificate_from_file(root_path));

    v3::DefaultCertificateValidator validator;
    validator.use_runtime(&runtime);
    validator.disable_location_checks(true);

    StraightVerifyService verify(runtime, *backend, positioning);
    verify.use_certificate_cache(&cache);
    verify.use_certificate_validator(&validator);
    verify.use_trust_store(&trust_store);
    return report(verify.verify(message));
}

int check_v2(const std::string& msg_path, const std::string& at_path, const std::string& aa_path, const std::string& root_path)
{
    v2::SecuredMessage message;
    {
        std::ifstream in(msg_path, std::ios::binary);
        InputArchive archive(in);
        deserialize(archive, message);
        if (!archive.is_good()) {
            std::cout << "decode=FAIL (not a valid v2 SecuredMessage)\n";
            return 2;
        }
    }
    auto generation_time = message.header_field<v2::HeaderFieldType::Generation_Time>();
    if (!generation_time) {
        std::cout << "decode=OK but generation_time missing\n";
        return 2;
    }
    std::cout << "decode=OK protocol_version=" << int(message.protocol_version())
        << " its_aid=" << v2::get_its_aid(message) << "\n";

    ManualRuntime runtime(just_after(*generation_time));
    StoredPositionProvider positioning;
    auto backend = create_backend("default");

    v2::CertificateCache cache(runtime);
    cache.insert(v2::load_certificate_from_file(aa_path));
    cache.insert(v2::load_certificate_from_file(at_path));
    v2::TrustStore trust_store;
    trust_store.insert(v2::load_certificate_from_file(root_path));
    v2::DefaultCertificateValidator validator(*backend, cache, trust_store);
    // the provider only feeds the sign header policy (certificate requests); its key is unused
    v2::StaticCertificateProvider provider(v2::load_certificate_from_file(at_path), ecdsa256::PrivateKey {});
    v2::DefaultSignHeaderPolicy sign_policy(runtime, positioning);

    StraightVerifyService verify(runtime, *backend, positioning);
    verify.use_certificate_cache(&cache);
    verify.use_certificate_provider(&provider);
    verify.use_certificate_validator(&validator);
    verify.use_sign_header_policy(&sign_policy);
    return report(verify.verify(message));
}

int decode_v3(const std::string& msg_path)
{
    v3::SecuredMessage message;
    if (!message.decode(read_file(msg_path))) {
        std::cout << "decode=FAIL (not a valid EtsiTs103097Data)\n";
        return 2;
    }
    std::cout << "decode=OK protocol_version=" << int(message.protocol_version())
        << " signed=" << message.is_signed() << " encrypted=" << message.is_encrypted();
    if (message.is_encrypted()) {
        const auto& enc = message->content->choice.encryptedData;
        std::cout << " recipients=" << enc.recipients.list.count;
        for (int i = 0; i < enc.recipients.list.count; ++i) {
            const auto* info = enc.recipients.list.array[i];
            std::cout << " recipient[" << i << "]="
                << (info->present == Vanetza_Security_RecipientInfo_PR_certRecipInfo ? "certRecipInfo" : "other");
            if (info->present == Vanetza_Security_RecipientInfo_PR_certRecipInfo) {
                const auto& enc_key = info->choice.certRecipInfo.encKey;
                std::cout << (enc_key.present == Vanetza_Security_EncryptedDataEncryptionKey_PR_eciesNistP256 ?
                    "/eciesNistP256" : "/other-key");
            }
        }
        std::cout << " ciphertext=" << (enc.ciphertext.present == Vanetza_Security_SymmetricCiphertext_PR_aes128ccm ?
            "aes128ccm" : "other");
    }
    std::cout << "\n";
    return 0;
}

int decode_v2(const std::string& msg_path)
{
    v2::SecuredMessage message;
    std::ifstream in(msg_path, std::ios::binary);
    InputArchive archive(in);
    std::size_t size = deserialize(archive, message);
    if (!archive.is_good()) {
        std::cout << "decode=FAIL (not a valid v2 SecuredMessage)\n";
        return 2;
    }
    in.peek();
    std::cout << "decode=OK protocol_version=" << int(message.protocol_version())
        << " bytes=" << size << (in.eof() ? "" : " TRAILING-DATA")
        << " payload_type=" << static_cast<int>(message.payload.type) << " header_fields=";
    for (auto& field : message.header_fields) {
        std::cout << static_cast<int>(v2::get_type(field)) << ",";
    }
    auto* recipients = message.header_field<v2::HeaderFieldType::Recipient_Info>();
    std::cout << " recipients=" << (recipients ? recipients->size() : 0);
    auto* params = message.header_field<v2::HeaderFieldType::Encryption_Parameters>();
    std::cout << " encryption_parameters=" << (params ? "present" : "absent")
        << " trailer_fields=" << message.trailer_fields.size() << "\n";
    return in.eof() ? 0 : 2;
}

} // namespace

int main(int argc, char** argv)
{
    if (argc == 3) {
        const std::string mode = argv[1];
        try {
            if (mode == "decode-v3") {
                return decode_v3(argv[2]);
            } else if (mode == "decode-v2") {
                return decode_v2(argv[2]);
            }
        } catch (const std::exception& e) {
            std::cout << "error=" << e.what() << "\n";
            return 2;
        }
    }
    if (argc != 6) {
        std::cerr << "usage: " << argv[0] << " v2|v3 <message> <at.cert> <aa.cert> <root.cert>\n"
                  << "       " << argv[0] << " decode-v2|decode-v3 <message>\n";
        return 2;
    }
    try {
        const std::string version = argv[1];
        if (version == "v3") {
            return check_v3(argv[2], argv[3], argv[4], argv[5]);
        } else if (version == "v2") {
            return check_v2(argv[2], argv[3], argv[4], argv[5]);
        }
        std::cerr << "unknown version " << version << "\n";
    } catch (const std::exception& e) {
        std::cout << "error=" << e.what() << "\n";
    }
    return 2;
}
