// RE1: implementation of `_GfxSetConstantBuffer`. See the header for provenance and for the
// instruction-level reading of each expression.

#include "gfx_set_constant_buffer.hpp"

#include <cstring>

namespace eu4::re::gfx {

namespace {

// `MOV R9D,dword ptr [RSI]` - memcpy so that an unaligned buffer is defined behaviour and no
// aliasing assumption is introduced.
[[nodiscard]] std::uint32_t load_first_dword(const void *buffer) noexcept {
    std::uint32_t dword = 0;
    std::memcpy(&dword, buffer, sizeof dword);
    return dword;
}

}  // namespace

void *gfx_set_constant_buffer(void *const *handle, const void *buffer, std::uint32_t slot,
                              std::uint32_t selector) noexcept {
    auto *ctx = static_cast<Context *>(*handle);  // MOV RAX,[RDI]

    const std::uint32_t words = selector == kSelectorReadFromBuffer
                                    ? words_from_buffer(load_first_dword(buffer))
                                    : words_from_selector(selector);

    // SHL RCX,4 then the three writes, in the original's order.
    ctx->slots[slot].word_count = words;                  // MOV [RAX+RCX+0xc8],R9D
    ctx->slots[slot].buffer = const_cast<void *>(buffer);  // MOV [RAX+RCX+0xd0],RSI
    ctx->dirty = kDirtyValue;                              // MOV byte [RAX+0x128],1

    return ctx;  // what the original leaves in RAX
}

extern "C" void *gfx_set_constant_buffer_abi(void **handle, const void *buffer,
                                             std::uint64_t unused3, std::uint32_t slot,
                                             std::uint64_t unused5,
                                             std::uint32_t selector) noexcept {
    static_cast<void>(unused3);
    static_cast<void>(unused5);
    return gfx_set_constant_buffer(handle, buffer, slot, selector);
}

}  // namespace eu4::re::gfx
