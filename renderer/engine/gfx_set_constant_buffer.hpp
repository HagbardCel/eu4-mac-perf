// RE1: handwritten functional reimplementation of `_GfxSetConstantBuffer`
// (entry RVA 0x15ec36a, pinned image sha256 b3d38876...141d).
//
// Written from the documented contract (`../contract.md`) and the reviewed instruction
// inventory (`../instructions.json`): offsets, widths, the two arithmetic paths and the
// three writes. No decompiler output was copied; the field names are the contract's
// hypotheses. The instruction-level encoding of each expression is quoted in a comment.
//
// What this is: a faithful semantic model of the decoded instructions on the declared
// observable domain, `slot in [0, 6)`. What it is not: a claim that it compiles to the
// original bytes, or that its incidental register traffic matches (see README.md).

#ifndef EU4_RE_RECONSTRUCTION_GFX_SET_CONSTANT_BUFFER_HPP
#define EU4_RE_RECONSTRUCTION_GFX_SET_CONSTANT_BUFFER_HPP

#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace eu4::re::gfx {

// Object layout, evidence-derived (contract.md "Object layout"). Offsets are relative to
// the context base the original loads from *RDI; what precedes 0xc8 is not recovered, so it
// is an opaque prefix here.
inline constexpr std::size_t kSlotCount = 6;
inline constexpr std::size_t kSlotStride = 0x10;
inline constexpr std::size_t kWordCountBase = 0xc8;
inline constexpr std::size_t kBufferBase = 0xd0;
inline constexpr std::size_t kDirtyOffset = 0x128;
inline constexpr std::uint8_t kDirtyValue = 1;

// `R9D == -1` selects the path that reads the length from the buffer itself.
inline constexpr std::uint32_t kSelectorReadFromBuffer = 0xFFFFFFFFu;

struct Slot final {
    std::uint32_t word_count;
    std::uint32_t pad;
    void *buffer;
};

struct Context final {
    std::byte unknown_prefix[kWordCountBase];
    Slot slots[kSlotCount];
    std::uint8_t dirty;
};

static_assert(sizeof(Slot) == kSlotStride, "Slot must occupy one 16-byte stride");
static_assert(offsetof(Slot, word_count) == 0x00, "Slot.word_count offset");
static_assert(offsetof(Slot, pad) == 0x04, "Slot.pad offset");
static_assert(offsetof(Slot, buffer) == 0x08, "Slot.buffer offset");
static_assert(offsetof(Context, slots) == kWordCountBase, "slot array base must be 0xc8");
static_assert(offsetof(Context, slots[1].word_count) - offsetof(Context, slots[0].word_count)
                  == kSlotStride,
              "slot stride");
static_assert(offsetof(Context, dirty) == kDirtyOffset, "dirty byte must be at 0x128");
static_assert(sizeof(Context) == 0x130, "Context size");
static_assert(std::is_standard_layout_v<Context>, "the contract's offsets need standard layout");
static_assert(std::is_trivially_copyable_v<Context>,
              "the harness seeds and reads the context as bytes");

// Explicit-length path, `R9D != -1` (0x15ec379-0x15ec38b):
//   LEA EDX,[R9+3]; ADD R9D,6; TEST EDX,EDX; CMOVNS R9D,EDX; SAR R9D,2
// The addition wraps in 32 bits before the sign test, so the signed conversion is of the
// *wrapped* sum: converting an out-of-range unsigned value to a signed one is well-defined
// modulo 2^32 in C++20 (it is not an overflowing *signed* addition, which would be UB).
// The CMOVNS/SAR pair is the compiler's signed divide-by-four-truncating-toward-zero idiom,
// so the whole sequence equals truncating signed division of the wrapped sum by four.
[[nodiscard]] constexpr std::uint32_t words_from_selector(std::uint32_t selector) noexcept {
    const std::uint32_t sum = selector + 3u;
    const std::int32_t signed_sum = static_cast<std::int32_t>(sum);
    return static_cast<std::uint32_t>(signed_sum / 4);
}

// Length-from-buffer path, `R9D == -1` (0x15ec38d-0x15ec394):
//   MOV R9D,[RSI]; ADD R9D,3; SHR R9D,2
// SHR is logical, so this is an unsigned round-up-then-truncate, unlike the path above.
[[nodiscard]] constexpr std::uint32_t words_from_buffer(std::uint32_t first_dword) noexcept {
    return (first_dword + 3u) >> 2;
}

// Semantic entry point: only the meaningful inputs, no ABI padding. Preconditions mirror
// the original's unchecked reads: `handle` must point to a readable context pointer,
// `buffer` must be readable as a dword when `selector == kSelectorReadFromBuffer`, and
// `slot` must be in [0, kSlotCount) - the original checks nothing, and outside that domain
// a C++ implementation through `slots[slot]` would be undefined behaviour.
// Returns the context base, which is what the original leaves in RAX (a recorded outcome,
// not a declared return type - contract.md).
void *gfx_set_constant_buffer(void *const *handle, const void *buffer, std::uint32_t slot,
                              std::uint32_t selector) noexcept;

// Six-position System V adapter, byte-compatible with the original's argument registers:
// handle=RDI, buffer=RSI, unused3=RDX, slot=RCX, unused5=R8, selector=R9D. Positions 3 and 5
// are placeholders: the original never reads RDX or R8, but they belong to its call shape.
extern "C" void *gfx_set_constant_buffer_abi(void **handle, const void *buffer,
                                             std::uint64_t unused3, std::uint32_t slot,
                                             std::uint64_t unused5,
                                             std::uint32_t selector) noexcept;

}  // namespace eu4::re::gfx

#endif  // EU4_RE_RECONSTRUCTION_GFX_SET_CONSTANT_BUFFER_HPP
