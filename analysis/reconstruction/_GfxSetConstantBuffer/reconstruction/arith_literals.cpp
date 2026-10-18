// RE1 test support: the two arithmetic paths written a second time, literally instruction by
// instruction, in a translation unit of their own. The whole-domain sweep in `harness.cpp`
// compares the reconstruction against these.
//
// The separate file is the point: the build uses no LTO, so the optimizer cannot see both
// formulations at once, prove them equal across the whole input space and fold the comparison
// (and the loop) away - which would make the sweep pass vacuously. The sweep also keeps a
// volatile sink so that the loop cannot be removed as dead code.

#include <cstdint>

namespace eu4::re::test_support {

// LEA EDX,[R9+3]; ADD R9D,6; TEST EDX,EDX; CMOVNS R9D,EDX; SAR R9D,2
std::uint32_t literal_selector_encoding(std::uint32_t selector) noexcept {
    const std::uint32_t edx = selector + 3u;
    std::uint32_t r9d = selector + 6u;
    if (static_cast<std::int32_t>(edx) >= 0) {
        r9d = edx;
    }
    return static_cast<std::uint32_t>(static_cast<std::int32_t>(r9d) >> 2);
}

// MOV R9D,[RSI]; ADD R9D,3; SHR R9D,2 - here the first dword is already in the register.
std::uint32_t literal_buffer_encoding(std::uint32_t first_dword) noexcept {
    return (first_dword + 3u) >> 2;
}

}  // namespace eu4::re::test_support
