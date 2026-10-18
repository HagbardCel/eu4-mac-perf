// RE1 ABI probe (x86-64 only): calls the six-position adapter through a trampoline that
// places the arguments in the registers the original consumed - RDI handle, RSI buffer,
// RDX unused, RCX slot, R8 unused, R9D selector - and asserts the resulting effects.
//
// The point is not that the compiler emits the original's instructions (it does not, and it
// is not required to): it is that the adapter's *interface* is the original's interface, so
// an RE3 redirection can call it with the original's registers.
//
// Prints one `check=<name> <ok|fail>` line per check plus a final `abi=ok|fail`; exits 1 on
// any failure. The Python test asserts on that summary.

#include "gfx_set_constant_buffer.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <iostream>

extern "C" std::uint64_t abi_call6(void *fn, std::uint64_t v0, std::uint64_t v1,
                                   std::uint64_t v2, std::uint64_t v3, std::uint64_t v4,
                                   std::uint64_t v5);

namespace {

using eu4::re::gfx::Context;

bool g_ok = true;

void check(const char *name, bool ok) {
    std::cout << "check=" << name << ' ' << (ok ? "ok" : "fail") << '\n';
    if (!ok) g_ok = false;
}

Context seeded() {
    Context ctx{};
    // The same linear byte pattern the pilot declares for its constructed context, so that an
    // unexpected write anywhere in the object is visible rather than sitting on a zero.
    auto *bytes = reinterpret_cast<std::uint8_t *>(&ctx);
    for (std::size_t i = 0; i < sizeof ctx; ++i) {
        bytes[i] = static_cast<std::uint8_t>((i * 55 + 17) & 0xFF);
    }
    return ctx;
}

std::uint32_t read_u32(const Context &ctx, std::size_t offset) {
    std::uint32_t value = 0;
    std::memcpy(&value, reinterpret_cast<const std::uint8_t *>(&ctx) + offset, sizeof value);
    return value;
}

std::uint64_t read_u64(const Context &ctx, std::size_t offset) {
    std::uint64_t value = 0;
    std::memcpy(&value, reinterpret_cast<const std::uint8_t *>(&ctx) + offset, sizeof value);
    return value;
}

// Runs the adapter through the trampoline with garbage in the two unused positions.
std::uint64_t call_via_registers(Context &ctx, std::uint32_t slot, std::uint32_t selector,
                                 const void *buffer, std::uint64_t unused3,
                                 std::uint64_t unused5) {
    void *ctx_pointer = &ctx;
    void **handle = &ctx_pointer;
    return abi_call6(reinterpret_cast<void *>(&eu4::re::gfx::gfx_set_constant_buffer_abi),
                     reinterpret_cast<std::uint64_t>(handle),
                     reinterpret_cast<std::uint64_t>(buffer), unused3, slot, unused5, selector);
}

}  // namespace

int main() {
    const std::uint32_t buffer_dword = 0x0000000Du;  // 13 bytes -> 4 words
    const void *buffer = &buffer_dword;

    constexpr std::size_t kSlot3 = 3u * eu4::re::gfx::kSlotStride;
    constexpr std::size_t kWordCount = eu4::re::gfx::kWordCountBase + kSlot3;
    constexpr std::size_t kBuffer = eu4::re::gfx::kBufferBase + kSlot3;

    Context first = seeded();
    const std::uint64_t rax = call_via_registers(first, 3, 13, buffer, 0xDEADBEEFDEADBEEFull,
                                                 0xFEEDFACEFEEDFACEull);
    check("slot_from_rcx", read_u32(first, kWordCount) == 4u);
    check("buffer_from_rsi", read_u64(first, kBuffer) == reinterpret_cast<std::uint64_t>(buffer));
    check("dirty_written", first.dirty == eu4::re::gfx::kDirtyValue);
    check("rax_is_context", rax == reinterpret_cast<std::uint64_t>(&first));

    // Same call, different garbage in the unused positions: the effects must not change, which
    // is what "the original never reads RDX or R8" means at this boundary.
    Context second = seeded();
    call_via_registers(second, 3, 13, buffer, 0x0123456789ABCDEFull, 0x1111111111111111ull);
    check("unused_positions_ignored",
          std::memcmp(&first, &second, sizeof(Context)) == 0);

    // Two frozen selector cases, through the same register positions: the selector must arrive
    // whole in R9D and take the explicit-length path (RE0 scenarios s11 -> 0xe0000001, where the
    // +3 wraps into the sign bit, and s12 -> 0xffffffff).
    Context third = seeded();
    call_via_registers(third, 3, 0x7FFFFFFFu, buffer, 0, 0);
    check("selector_r9d_overflow_case", read_u32(third, kWordCount) == 0xE0000001u);

    Context fourth = seeded();
    call_via_registers(fourth, 3, 0xFFFFFFF8u, buffer, 0, 0);
    check("selector_r9d_negative_case", read_u32(fourth, kWordCount) == 0xFFFFFFFFu);

    // Buffer-selected path, reached through the same register positions.
    Context fifth = seeded();
    call_via_registers(fifth, 0, eu4::re::gfx::kSelectorReadFromBuffer, buffer, 0, 0);
    const std::uint32_t expected_buffer_words = eu4::re::gfx::words_from_buffer(buffer_dword);
    check("buffer_path_words",
          read_u32(fifth, eu4::re::gfx::kWordCountBase) == expected_buffer_words);

    // Position sensitivity, with in-domain values throughout: a plausible-looking value in the
    // *unused* register must not be consumed. If the adapter read its slot from RDX (the
    // rejected four-argument layout) this write would land in slot 3 instead of slot 5.
    Context sixth = seeded();
    const std::uint32_t slot3_before = read_u32(sixth, eu4::re::gfx::kWordCountBase);
    call_via_registers(sixth, 5, 0, buffer, /*unused3*/ 3, /*unused5*/ 0xFFFFFFF8u);
    check("slot_reads_rcx_not_rdx",
          read_u32(sixth, eu4::re::gfx::kWordCountBase) == slot3_before);
    check("selector_reads_r9d_not_r8",
          read_u32(sixth, eu4::re::gfx::kWordCountBase + (5u * eu4::re::gfx::kSlotStride)) == 0u);

    std::cout << "abi=" << (g_ok ? "ok" : "fail") << '\n';
    return g_ok ? 0 : 1;
}
