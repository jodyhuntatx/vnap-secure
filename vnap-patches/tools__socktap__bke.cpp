#include "bke.hpp"
#include <cryptopp/aes.h>
#include <cryptopp/eccrypto.h>
#include <cryptopp/files.h>
#include <cryptopp/oids.h>
#include <cryptopp/sha.h>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <vector>

namespace
{

const CryptoPP::Integer& p256_order()
{
    static const CryptoPP::Integer order =
        CryptoPP::DL_GroupParameters_EC<CryptoPP::ECP>(CryptoPP::ASN1::secp256r1()).GetSubgroupOrder();
    return order;
}

} // namespace

BkeSecrets load_bke_secrets(const std::string& caterpillar_key_path, const std::string& expansion_key_path)
{
    BkeSecrets secrets;
    CryptoPP::ECDSA<CryptoPP::ECP, CryptoPP::SHA256>::PrivateKey caterpillar;
    try {
        CryptoPP::FileSource source(caterpillar_key_path.c_str(), true);
        caterpillar.Load(source);
    } catch (const CryptoPP::Exception& e) {
        throw std::runtime_error("cannot load caterpillar key " + caterpillar_key_path + ": " + e.what());
    }
    secrets.caterpillar = caterpillar.GetPrivateExponent();

    std::ifstream file(expansion_key_path, std::ios::binary);
    std::vector<char> bytes((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (bytes.size() != secrets.expansion_key.size()) {
        throw std::runtime_error("expansion key " + expansion_key_path + " must be 16 bytes (AES-128)");
    }
    std::copy(bytes.begin(), bytes.end(), secrets.expansion_key.begin());
    return secrets;
}

CryptoPP::Integer bke_expansion(const std::array<uint8_t, 16>& expansion_key, uint32_t i, uint32_t j)
{
    // x = prefix (0: certificate/signing keys) | i | j | 0, big-endian
    std::array<uint8_t, 16> x {};
    for (int b = 0; b < 4; ++b) {
        x[4 + b] = static_cast<uint8_t>(i >> (24 - 8 * b));
        x[8 + b] = static_cast<uint8_t>(j >> (24 - 8 * b));
    }
    CryptoPP::AES::Encryption aes(expansion_key.data(), expansion_key.size());
    std::array<uint8_t, 48> out;
    for (unsigned t = 1; t <= 3; ++t) {
        std::array<uint8_t, 16> block = x;
        block[15] = static_cast<uint8_t>(t); // the low 32 bits of x are zero: x + t has no carry
        std::array<uint8_t, 16> cipher;
        aes.ProcessBlock(block.data(), cipher.data());
        for (std::size_t b = 0; b < 16; ++b) {
            out[16 * (t - 1) + b] = cipher[b] ^ block[b];
        }
    }
    return CryptoPP::Integer(out.data(), out.size()) % p256_order();
}

CryptoPP::Integer bke_butterfly_private_key(const BkeSecrets& secrets, uint32_t i, uint32_t j, const CryptoPP::Integer& offset)
{
    const CryptoPP::Integer key = (secrets.caterpillar + bke_expansion(secrets.expansion_key, i, j) + offset) % p256_order();
    if (key.IsZero()) {
        throw std::runtime_error("butterfly private key is zero");
    }
    return key;
}

void write_pkcs8_private_key(const CryptoPP::Integer& key, const std::string& path)
{
    CryptoPP::ECDSA<CryptoPP::ECP, CryptoPP::SHA256>::PrivateKey private_key;
    private_key.Initialize(CryptoPP::ASN1::secp256r1(), key);
    private_key.AccessGroupParameters().SetEncodeAsOID(true);
    CryptoPP::FileSink sink(path.c_str(), true);
    private_key.Save(sink);
}
