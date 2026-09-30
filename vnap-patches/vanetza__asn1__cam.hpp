#ifndef CAM_HPP_WXYNEKFN
#define CAM_HPP_WXYNEKFN

#include <vanetza/asn1/asn1c_conversion.hpp>
#include <vanetza/asn1/asn1c_wrapper.hpp>
#include <vanetza/asn1/its/CAM.h>
#include <vanetza/asn1/its/r2/CAM.h>
#include <vanetza/asn1/its/r2/LowFrequencyContainer.h>
#include <vanetza/asn1/its/r2/BasicVehicleContainerLowFrequency.h>
#include <mutex>

namespace vanetza
{
namespace asn1
{

namespace r1
{

class Cam : public asn1c_per_wrapper<CAM_t>
{
public:
    using wrapper = asn1c_per_wrapper<CAM_t>;
    Cam() : wrapper(asn_DEF_CAM) {}
};

} // namespace r1

namespace r2
{

class Cam : public asn1c_per_wrapper<Vanetza_ITS2_CAM_t>
{
public:
    using wrapper = asn1c_per_wrapper<Vanetza_ITS2_CAM_t>;
    Cam() : wrapper(asn_DEF_Vanetza_ITS2_CAM) {}
};

/**
 * Lock for every use of the R2 CAM type descriptors (encode, decode, JSON conversion,
 * validation, free). CompactR2DecodeGuard changes a member descriptor of
 * asn_DEF_Vanetza_ITS2_CAM process-wide, so code relying on the standard (strict)
 * descriptors must hold this lock as well. Recursive: guards may nest in one thread.
 */
inline std::recursive_mutex& cam_descriptor_mutex()
{
    static std::recursive_mutex mutex;
    return mutex;
}

/**
 * RAII guard to temporarily swap the BasicVehicleContainerLowFrequency type descriptor
 * in LowFrequencyContainer to use the compact (6-bit) variant for decoding.
 *
 * This allows decoding CAMs from VW-style encoders that use R1's pathHistory
 * SIZE(0..40) = 6 bits even when protocolVersion=2.
 *
 * Holds cam_descriptor_mutex() for its lifetime: the swap is global, and without the lock
 * two overlapping guards could leave the compact descriptor installed permanently.
 */
class CompactR2DecodeGuard
{
public:
    CompactR2DecodeGuard() : lock_(cam_descriptor_mutex())
    {
        // Save original type descriptor pointer
        original_type_descriptor_ = asn_MBR_Vanetza_ITS2_LowFrequencyContainer_1[0].type;
        // Swap to compact variant (6-bit pathHistory)
        asn_MBR_Vanetza_ITS2_LowFrequencyContainer_1[0].type =
            &asn_DEF_Vanetza_ITS2_BasicVehicleContainerLowFrequency_Compact;
    }

    ~CompactR2DecodeGuard()
    {
        // Restore original type descriptor
        asn_MBR_Vanetza_ITS2_LowFrequencyContainer_1[0].type = original_type_descriptor_;
    }

    // Non-copyable
    CompactR2DecodeGuard(const CompactR2DecodeGuard&) = delete;
    CompactR2DecodeGuard& operator=(const CompactR2DecodeGuard&) = delete;

private:
    std::lock_guard<std::recursive_mutex> lock_; // first member: released after the restore
    asn_TYPE_descriptor_t* original_type_descriptor_;
};

} // namespace r2

// alias for backward compatibility
using Cam = r1::Cam;

} // namespace asn1
} // namespace vanetza

#endif /* CAM_HPP_WXYNEKFN */
