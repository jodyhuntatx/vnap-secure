#include "security.hpp"
#include <vanetza/security/delegating_security_entity.hpp>
#include <vanetza/security/straight_verify_service.hpp>
#include <vanetza/security/v2/certificate_cache.hpp>
#include "vanetza/security/v2/certificate_provider.hpp"
#include <vanetza/security/v2/default_certificate_validator.hpp>
#include <vanetza/security/v2/naive_certificate_provider.hpp>
#include <vanetza/security/v2/persistence.hpp>
#include <vanetza/security/v2/pseudonym_certificate_provider.hpp>
#include <vanetza/security/v2/sign_service.hpp>
#include <vanetza/security/v2/static_certificate_provider.hpp>
#include <vanetza/security/v2/trust_store.hpp>
#include <vanetza/security/v3/certificate_cache.hpp>
#include <vanetza/security/v3/certificate_chain.hpp>
#include <vanetza/security/v3/certificate_validator.hpp>
#include <vanetza/security/v3/naive_certificate_provider.hpp>
#include <vanetza/security/v3/persistence.hpp>
#include <vanetza/security/v3/pseudonym_certificate_provider.hpp>
#include <vanetza/security/v3/sign_header_policy.hpp>
#include <vanetza/security/v3/sign_service.hpp>
#include <vanetza/security/v3/static_certificate_provider.hpp>

#include <stdexcept>
#include <iostream>

using namespace vanetza;
namespace po = boost::program_options;

class SecurityContextV2 : public security::SecurityEntity
{
public:
    SecurityContextV2(const Runtime& runtime, PositionProvider& positioning) :
        runtime(runtime), positioning(positioning),
        backend(security::create_backend("default")),
        sign_header_policy(runtime, positioning),
        cert_cache(runtime),
        cert_validator(*backend, cert_cache, trust_store)
    {
    }

    security::EncapConfirm encapsulate_packet(security::EncapRequest&& request) override
    {
        if (!entity) {
            throw std::runtime_error("security entity is not ready");
        }
        return entity->encapsulate_packet(std::move(request));
    }

    security::DecapConfirm decapsulate_packet(security::DecapRequest&& request) override
    {
        if (!entity) {
            throw std::runtime_error("security entity is not ready");
        }
        return entity->decapsulate_packet(std::move(request));
    }

    void build_entity()
    {
        if (!cert_provider) {
            throw std::runtime_error("certificate provider is missing");
        }
        std::unique_ptr<security::SignService> sign_service { new
            security::v2::StraightSignService(*cert_provider, *backend, sign_header_policy) };
        std::unique_ptr<security::StraightVerifyService> verify_service { new
            security::StraightVerifyService(runtime, *backend, positioning) };
        verify_service->use_certificate_provider(cert_provider.get());
        verify_service->use_certificate_cache(&cert_cache);
        verify_service->use_certificate_validator(&cert_validator);
        verify_service->use_sign_header_policy(&sign_header_policy);
        entity.reset(new security::DelegatingSecurityEntity { std::move(sign_service), std::move(verify_service) });
    }

    const Runtime& runtime;
    PositionProvider& positioning;
    std::unique_ptr<security::Backend> backend;
    std::unique_ptr<security::SecurityEntity> entity;
    std::unique_ptr<security::v2::CertificateProvider> cert_provider;
    security::v2::DefaultSignHeaderPolicy sign_header_policy;
    security::v2::TrustStore trust_store;
    security::v2::CertificateCache cert_cache;
    security::v2::DefaultCertificateValidator cert_validator;
};

class SecurityContextV3 : public security::SecurityEntity
{
public:
    SecurityContextV3(const Runtime& runtime, PositionProvider& positioning) :
        runtime(runtime), positioning(positioning),
        backend(security::create_backend("default"))
    {
        cert_validator.use_runtime(&runtime);
        cert_validator.use_position_provider(&positioning);
        cert_validator.use_location_checker(&location_checker);
    }

    security::EncapConfirm encapsulate_packet(security::EncapRequest&& request) override
    {
        if (!entity) {
            throw std::runtime_error("security entity is not ready");
        }
        return entity->encapsulate_packet(std::move(request));
    }

    security::DecapConfirm decapsulate_packet(security::DecapRequest&& request) override
    {
        if (!entity) {
            throw std::runtime_error("security entity is not ready");
        }
        return entity->decapsulate_packet(std::move(request));
    }

    void build_entity()
    {
        if (!cert_provider) {
            throw std::runtime_error("certificate provider is missing");
        }
        sign_header_policy.reset(new security::v3::DefaultSignHeaderPolicy(runtime, positioning, *cert_provider));
        std::unique_ptr<security::SignService> sign_service { new 
            security::v3::StraightSignService(*cert_provider, *backend, *sign_header_policy, cert_validator) };
        std::unique_ptr<security::StraightVerifyService> verify_service { new
            security::StraightVerifyService(runtime, *backend, positioning) };
        verify_service->use_certificate_provider(cert_provider.get());
        verify_service->use_certificate_validator(&cert_validator);
        verify_service->use_sign_header_policy(sign_header_policy.get());
        // full-chain verification is always on for v3: without trusted roots nothing verifies
        verify_service->use_trust_store(&trust_store);
        entity.reset(new security::DelegatingSecurityEntity { std::move(sign_service), std::move(verify_service) });
    }

    const Runtime& runtime;
    PositionProvider& positioning;
    std::unique_ptr<security::Backend> backend;
    std::unique_ptr<security::SecurityEntity> entity;
    std::unique_ptr<security::v3::CertificateProvider> cert_provider;
    security::v3::CertificateCache trust_store; // trusted root CAs (--trusted-certificate)
    std::unique_ptr<security::v3::DefaultSignHeaderPolicy> sign_header_policy;
    security::v3::DefaultCertificateValidator cert_validator;
    security::v3::DefaultLocationChecker location_checker;
};

std::unique_ptr<security::SecurityEntity>
create_dummy_v2_security_entity(const Runtime& runtime)
{
    std::unique_ptr<security::SignService> sign_service { new security::v2::DummySignService { runtime, nullptr } };
    std::unique_ptr<security::VerifyService> verify_service { new security::DummyVerifyService {
        security::VerificationReport::Success, security::CertificateValidity::valid() } };
    return std::make_unique<security::DelegatingSecurityEntity>(std::move(sign_service), std::move(verify_service));
}

std::unique_ptr<security::SecurityEntity>
create_dummy_v3_security_entity(const Runtime& runtime)
{
    std::unique_ptr<security::SignService> sign_service { new security::v3::DummySignService { runtime } };
    std::unique_ptr<security::VerifyService> verify_service { new security::DummyVerifyService {
        security::VerificationReport::Success, security::CertificateValidity::valid() } };
    return std::make_unique<security::DelegatingSecurityEntity>(std::move(sign_service), std::move(verify_service));
}

std::unique_ptr<security::v2::CertificateProvider>
load_v2_certificates(const std::string& cert_path, const std::string& cert_key_path, const std::vector<std::string> cert_chain_path, security::v2::CertificateCache& cert_cache)
{
    auto authorization_ticket = security::v2::load_certificate_from_file(cert_path);
    auto authorization_ticket_key = security::v2::load_private_key_from_file(cert_key_path);

    std::list<security::v2::Certificate> chain;
    for (auto& chain_path : cert_chain_path) {
        auto chain_certificate = security::v2::load_certificate_from_file(chain_path);
        chain.push_back(chain_certificate);
        cert_cache.insert(chain_certificate);
    }

    return std::make_unique<security::v2::StaticCertificateProvider>(authorization_ticket, authorization_ticket_key.private_key, chain);
}

// [V2-CHAIN]/[V3-CHAIN] lines are written with a single insertion each: other threads (e.g. the
// position channel) log to std::cerr concurrently, and vnapctl parses these lines.
const char* chain_result(const security::CertificateValidity& validity)
{
    if (validity) {
        return "OK";
    }
    switch (validity.reason()) {
        case security::CertificateInvalidReason::Off_Time_Period: return "FAIL (expired or not yet valid)";
        case security::CertificateInvalidReason::Unknown_Signer: return "FAIL (issuer not found / root not trusted)";
        case security::CertificateInvalidReason::Missing_Signature: return "FAIL (signature does not verify)";
        case security::CertificateInvalidReason::Invalid_Signer: return "FAIL (invalid issuer)";
        case security::CertificateInvalidReason::Inconsistent_With_Signer: return "FAIL (inconsistent with issuer: validity, ITS-AIDs, assurance or region)";
        case security::CertificateInvalidReason::Broken_Time_Period: return "FAIL (broken validity period)";
        case security::CertificateInvalidReason::Missing_Public_Key: return "FAIL (missing public key)";
        case security::CertificateInvalidReason::Missing_Subject_Assurance: return "FAIL (missing assurance level)";
        case security::CertificateInvalidReason::Invalid_Name: return "FAIL (invalid subject name)";
        case security::CertificateInvalidReason::Insufficient_ITS_AID: return "FAIL (insufficient ITS-AID)";
        case security::CertificateInvalidReason::Off_Region: return "FAIL (outside region)";
        default: return "FAIL";
    }
}

// Load trusted root CAs and check the configured chain once at startup, so that a broken
// PKI setup is visible in the log instead of only as rejected messages at runtime.
void setup_v3_trust(const po::variables_map& vm, SecurityContextV3& context, const std::vector<std::string>& chain_paths, const std::vector<std::string>& at_paths)
{
    if (vm.count("trusted-certificate")) {
        for (auto& path : vm["trusted-certificate"].as<std::vector<std::string>>()) {
            auto root = security::v3::load_certificate_from_file(path);
            security::v3::CertificateView view { root.content() };
            if (!view.issuer_is_self() || !security::v3::verify_certificate_signature(*context.backend, *root, *root)) {
                throw std::runtime_error("trusted certificate is not a valid self-signed root CA: " + path);
            }
            context.trust_store.store(root);
        }
    }
    if (context.trust_store.size() == 0) {
        std::cerr << "[V3-CHAIN] WARNING: no --trusted-certificate given, all received v3 messages will be rejected\n";
    }

    security::v3::CertificateChainVerifier verifier(*context.backend, context.trust_store);
    const auto now = context.runtime.now();
    for (auto& path : chain_paths) {
        auto cert = security::v3::load_certificate_from_file(path);
        std::cerr << (std::string("[V3-CHAIN] chain certificate ") + path + ": " + chain_result(verifier.verify_ca(*cert, now)) + "\n");
    }
    for (auto& at_path : at_paths) {
        auto at = security::v3::load_certificate_from_file(at_path);
        std::cerr << (std::string("[V3-CHAIN] own authorization ticket ") + at_path + ": "
            + chain_result(verifier.verify(*at, &context.cert_provider->cache(), now)) + "\n");
    }
}

security::PrivateKey load_v3_private_key(const security::v3::Certificate& certificate, const std::string& key_path)
{
    auto key_pair = security::v3::load_private_key_from_file(key_path);
    security::PrivateKey priv_key;
    priv_key.type = certificate.get_verification_key_type();
    std::copy(key_pair.private_key.key.begin(), key_pair.private_key.key.end(), std::back_inserter(priv_key.key));
    return priv_key;
}

std::unique_ptr<security::v3::CertificateProvider>
load_v3_certificates(const std::string& cert_path, const std::string& cert_key_path, const std::vector<std::string> cert_chain_path)
{
    auto authorization_ticket = security::v3::load_certificate_from_file(cert_path);
    auto priv_key = load_v3_private_key(authorization_ticket, cert_key_path);

    auto provider = std::make_unique<security::v3::StaticCertificateProvider>(authorization_ticket, priv_key);
    for (auto& chain_path : cert_chain_path) {
        auto chain_certificate = security::v3::load_certificate_from_file(chain_path);
        provider->cache().store(chain_certificate);
    }
    return provider;
}

std::unique_ptr<security::SecurityEntity>
create_security_entity(const po::variables_map& vm, Runtime& runtime, PositionProvider& positioning, config_t config_s)
{
    std::unique_ptr<security::SecurityEntity> security;
    // const std::string name = vm["security"].as<std::string>();
    const std::string name = config_s.security;

    if (name.empty() || name == "none") {
        // no operation
        std::cout << "No security entity" << std::endl;
    } else if (name == "dummy" || name == "dummy-v3") {
        security = create_dummy_v3_security_entity(runtime);
        std::cout << "Dummy v3 security entity" << std::endl;
    } else if (name == "dummy-v2") {
        security = create_dummy_v2_security_entity(runtime);
        std::cout << "Dummy v2 security entity" << std::endl;
    } else if (name == "certs" || name == "certs-v3" || name == "certs-v2") {
        const unsigned version = name == "certs-v2" ? 2 : 3;

        if (vm.count("certificate") ^ vm.count("certificate-key")) {
            throw std::runtime_error("Either --certificate and --certificate-key must be present or none.");
        }

        const bool pseudonym_pool = vm.count("pseudonym-certificate") || vm.count("pseudonym-certificate-key");
        if (pseudonym_pool && vm.count("certificate")) {
            throw std::runtime_error("--pseudonym-certificate cannot be combined with --certificate.");
        }

        if (pseudonym_pool) {
            // pre-provisioned pool of authorization tickets (e.g. a butterfly batch), changed on events
            const auto cert_paths = vm.count("pseudonym-certificate")
                ? vm["pseudonym-certificate"].as<std::vector<std::string>>() : std::vector<std::string> {};
            const auto key_paths = vm.count("pseudonym-certificate-key")
                ? vm["pseudonym-certificate-key"].as<std::vector<std::string>>() : std::vector<std::string> {};
            if (cert_paths.empty() || cert_paths.size() != key_paths.size()) {
                throw std::runtime_error("--pseudonym-certificate and --pseudonym-certificate-key must be given "
                    "the same number of times (matching certificate/key pairs, in the same order).");
            }
            std::vector<std::string> chain_paths;
            if (vm.count("certificate-chain")) {
                chain_paths = vm["certificate-chain"].as<std::vector<std::string>>();
            }

            if (version == 3) {
                std::vector<security::v3::PseudonymCertificateProvider::Pseudonym> pool;
                for (std::size_t i = 0; i < cert_paths.size(); ++i) {
                    auto certificate = security::v3::load_certificate_from_file(cert_paths[i]);
                    auto key = load_v3_private_key(certificate, key_paths[i]);
                    pool.push_back({ std::move(certificate), std::move(key) });
                }
                auto provider = std::make_unique<security::v3::PseudonymCertificateProvider>(std::move(pool));
                for (auto& chain_path : chain_paths) {
                    provider->cache().store(security::v3::load_certificate_from_file(chain_path));
                }
                auto* pseudonyms = provider.get();
                auto context = std::make_unique<SecurityContextV3>(runtime, positioning);
                context->cert_provider = std::move(provider);
                setup_v3_trust(vm, *context, chain_paths, cert_paths);
                context->build_entity();
                pseudonyms->set_sign_header_policy(context->sign_header_policy.get());
                security = std::move(context);
            } else {
                auto context = std::make_unique<SecurityContextV2>(runtime, positioning);
                std::vector<security::v2::PseudonymCertificateProvider::Pseudonym> pool;
                for (std::size_t i = 0; i < cert_paths.size(); ++i) {
                    auto certificate = security::v2::load_certificate_from_file(cert_paths[i]);
                    auto key_pair = security::v2::load_private_key_from_file(key_paths[i]);
                    pool.push_back({ std::move(certificate), key_pair.private_key });
                }
                std::list<security::v2::Certificate> chain;
                for (auto& chain_path : chain_paths) {
                    auto chain_certificate = security::v2::load_certificate_from_file(chain_path);
                    chain.push_back(chain_certificate);
                    context->cert_cache.insert(chain_certificate);
                }
                auto provider = std::make_unique<security::v2::PseudonymCertificateProvider>(
                    std::move(pool), std::move(chain));
                provider->set_sign_header_policy(&context->sign_header_policy);
                context->cert_provider = std::move(provider);
                if (vm.count("trusted-certificate")) {
                    for (auto& trusted_path : vm["trusted-certificate"].as<std::vector<std::string> >()) {
                        context->trust_store.insert(security::v2::load_certificate_from_file(trusted_path));
                    }
                }
                for (auto& chain_path : chain_paths) {
                    auto chain_cert = security::v2::load_certificate_from_file(chain_path);
                    std::cerr << (std::string("[V2-CHAIN] chain certificate ") + chain_path + ": "
                        + chain_result(context->cert_validator.check_certificate(chain_cert)) + "\n");
                }
                for (auto& at_path : cert_paths) {
                    std::cerr << (std::string("[V2-CHAIN] own authorization ticket ") + at_path + ": "
                        + chain_result(context->cert_validator.check_certificate(
                               security::v2::load_certificate_from_file(at_path))) + "\n");
                }
                context->build_entity();
                security = std::move(context);
            }
        } else if (vm.count("certificate") && vm.count("certificate-key")) {
            const std::string& cert_path = vm["certificate"].as<std::string>();
            const std::string& cert_key_path = vm["certificate-key"].as<std::string>();
            std::vector<std::string> chain_paths;
            if (vm.count("certificate-chain")) {
                chain_paths = vm["certificate-chain"].as<std::vector<std::string>>();
            }

            if (version == 3) {
                auto context = std::make_unique<SecurityContextV3>(runtime, positioning);
                context->cert_provider = load_v3_certificates(cert_path, cert_key_path, chain_paths);
                setup_v3_trust(vm, *context, chain_paths, { cert_path });
                context->build_entity();
                security = std::move(context);
            } else {
                auto context = std::make_unique<SecurityContextV2>(runtime, positioning);
                context->cert_provider = load_v2_certificates(cert_path, cert_key_path, chain_paths, context->cert_cache);
                if (vm.count("trusted-certificate")) {
                    for (auto& cert_path : vm["trusted-certificate"].as<std::vector<std::string> >()) {
                        auto trusted_certificate = security::v2::load_certificate_from_file(cert_path);
                        context->trust_store.insert(trusted_certificate);
                    }
                }
                // log the configured chain once at startup (same checks as for received certificates)
                for (auto& chain_path : chain_paths) {
                    auto chain_cert = security::v2::load_certificate_from_file(chain_path);
                    std::cerr << (std::string("[V2-CHAIN] chain certificate ") + chain_path + ": "
                        + chain_result(context->cert_validator.check_certificate(chain_cert)) + "\n");
                }
                std::cerr << (std::string("[V2-CHAIN] own authorization ticket ") + cert_path + ": "
                    + chain_result(context->cert_validator.check_certificate(context->cert_provider->own_certificate())) + "\n");
                context->build_entity();
                security = std::move(context);
            }
        } else {
            if (version == 3) {
                auto context = std::make_unique<SecurityContextV3>(runtime, positioning);
                context->cert_provider = std::make_unique<security::v3::NaiveCertificateProvider>(runtime);
                setup_v3_trust(vm, *context, {}, {});
                context->build_entity();
                security = std::move(context);
            } else {
                auto context = std::make_unique<SecurityContextV2>(runtime, positioning);
                context->cert_provider = std::make_unique<security::v2::NaiveCertificateProvider>(runtime);
                context->build_entity();
                security = std::move(context);
            }
        }

        if (!security) {
            throw std::runtime_error("internal failure setting up security entity");
        }
    } else {
        throw std::runtime_error("Unknown security entity requested");
    }

    return security;
}

security::PseudonymControl* pseudonym_control(security::SecurityEntity* entity)
{
    if (auto* v3 = dynamic_cast<SecurityContextV3*>(entity)) {
        return dynamic_cast<security::PseudonymControl*>(v3->cert_provider.get());
    } else if (auto* v2 = dynamic_cast<SecurityContextV2*>(entity)) {
        return dynamic_cast<security::PseudonymControl*>(v2->cert_provider.get());
    }
    return nullptr;
}

std::function<PseudonymBatchResult(const PseudonymBatch&)> pseudonym_batch_loader(security::SecurityEntity* entity)
{
    if (auto* v3 = dynamic_cast<SecurityContextV3*>(entity)) {
        auto* provider = dynamic_cast<security::v3::PseudonymCertificateProvider*>(v3->cert_provider.get());
        if (!provider) {
            return {};
        }
        return [v3, provider](const PseudonymBatch& batch) {
            PseudonymBatchResult result;
            std::vector<security::v3::PseudonymCertificateProvider::Pseudonym> valid;
            security::v3::CertificateChainVerifier verifier(*v3->backend, v3->trust_store);
            const auto now = v3->runtime.now();
            for (const auto& files : batch) {
                try {
                    auto certificate = security::v3::load_certificate_from_file(files.first);
                    auto key = load_v3_private_key(certificate, files.second);
                    const auto validity = verifier.verify(*certificate, &provider->cache(), now);
                    std::cerr << (std::string("[V3-CHAIN] batch authorization ticket ") + files.first + ": "
                                  + chain_result(validity) + "\n");
                    if (!validity) {
                        result.rejected.push_back(files.first + ": " + chain_result(validity));
                        continue;
                    }
                    valid.push_back({ std::move(certificate), std::move(key) });
                } catch (const std::exception& e) {
                    result.rejected.push_back(files.first + ": " + e.what());
                }
            }
            result.added = valid.size();
            if (!valid.empty()) {
                provider->add_pseudonyms(std::move(valid));
            }
            return result;
        };
    } else if (auto* v2 = dynamic_cast<SecurityContextV2*>(entity)) {
        auto* provider = dynamic_cast<security::v2::PseudonymCertificateProvider*>(v2->cert_provider.get());
        if (!provider) {
            return {};
        }
        return [v2, provider](const PseudonymBatch& batch) {
            PseudonymBatchResult result;
            std::vector<security::v2::PseudonymCertificateProvider::Pseudonym> valid;
            for (const auto& files : batch) {
                try {
                    auto certificate = security::v2::load_certificate_from_file(files.first);
                    auto key_pair = security::v2::load_private_key_from_file(files.second);
                    const auto validity = v2->cert_validator.check_certificate(certificate);
                    std::cerr << (std::string("[V2-CHAIN] batch authorization ticket ") + files.first + ": "
                                  + chain_result(validity) + "\n");
                    if (!validity) {
                        result.rejected.push_back(files.first + ": " + chain_result(validity));
                        continue;
                    }
                    valid.push_back({ std::move(certificate), key_pair.private_key });
                } catch (const std::exception& e) {
                    result.rejected.push_back(files.first + ": " + e.what());
                }
            }
            result.added = valid.size();
            if (!valid.empty()) {
                provider->add_pseudonyms(std::move(valid));
            }
            return result;
        };
    }
    return {};
}

void add_security_options(po::options_description& options)
{
    options.add_options()
        ("security", po::value<std::string>()->default_value("dummy"), "Security entity [none,dummy,certs] (with optional -v2 or -v3 suffix)")
        ("certificate", po::value<std::string>(), "Certificate to use for secured messages.")
        ("certificate-key", po::value<std::string>(), "Certificate key to use for secured messages.")
        ("certificate-chain", po::value<std::vector<std::string> >()->multitoken(), "Certificate chain to use, use as often as needed.")
        ("trusted-certificate", po::value<std::vector<std::string> >()->multitoken(), "Trusted certificate, use as often as needed.")
        ("pseudonym-certificate", po::value<std::vector<std::string> >()->multitoken(),
            "Pseudonym pool certificate (authorization ticket), use as often as needed, paired in order with "
            "--pseudonym-certificate-key. The pseudonym changes on events from --pseudonym-control-broker; "
            "cannot be combined with --certificate.")
        ("pseudonym-certificate-key", po::value<std::vector<std::string> >()->multitoken(),
            "Private key of a --pseudonym-certificate entry, given the same number of times and in the same order.")
    ;
}

