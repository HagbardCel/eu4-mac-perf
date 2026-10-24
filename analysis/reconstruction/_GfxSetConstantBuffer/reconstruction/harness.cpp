// RE1 driver for the reconstruction: a deliberately dumb stdin/stdout executor. It knows
// nothing about scenarios, expectations or the emulator - the Python side owns the format,
// the corpus and every comparison. Kept dependency-free so that it can be built by the test
// on any host with a C++20 compiler. Scenario runs call the six-position adapter, so every
// run also exercises the ABI-shaped entry point with garbage in its two unused positions.
//
// Modes:
//   words selector            stdin: one hex selector per line    stdout: hex words per line
//   words buffer              stdin: one hex dword per line       stdout: hex words per line
//   scenario <slot> <selector-hex> <first-dword-hex|absent>
//                             stdin: the context bytes as hex; runs the function and prints
//                             the resulting context, the declared role values and whether the
//                             handle and buffer input regions were preserved
//   exhaustive                sweep all 2^32 inputs of both arithmetic paths against the
//                             literal CMOVNS/SAR and ADD/SHR encodings (opt-in, see README)
//
// Diagnostics go to stdout as `error=<name>` with a non-zero exit status so that the Python
// side can assert on a refusal instead of on a crash.

#include "gfx_set_constant_buffer.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include <sys/mman.h>
#include <unistd.h>

#ifndef MAP_ANONYMOUS
#define MAP_ANONYMOUS MAP_ANON
#endif

// Defined in `arith_literals.cpp`; declared here only. Used by the exhaustive sweep.
namespace eu4::re::test_support {
std::uint32_t literal_selector_encoding(std::uint32_t selector) noexcept;
std::uint32_t literal_buffer_encoding(std::uint32_t first_dword) noexcept;
}  // namespace eu4::re::test_support

namespace {

using eu4::re::gfx::Context;

constexpr std::size_t kContextSize = sizeof(Context);
constexpr std::size_t kWordCountBase = eu4::re::gfx::kWordCountBase;
constexpr std::size_t kBufferBase = eu4::re::gfx::kBufferBase;
constexpr std::size_t kSlotStride = eu4::re::gfx::kSlotStride;
constexpr std::size_t kDirtyOffset = eu4::re::gfx::kDirtyOffset;

int fail(const std::string &name) {
    std::cout << "error=" << name << '\n';
    return 2;
}

int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

// Accepts optional 0x prefixes and surrounding whitespace; returns false on a stray character
// or an odd digit count.
bool parse_hex(const std::string &text, std::vector<std::uint8_t> &out) {
    bool high = true;
    int pending = 0;
    for (std::size_t i = 0; i < text.size(); ++i) {
        const char c = text[i];
        if (c == ' ' || c == '\t' || c == '\r') continue;
        if (c == '0' && i + 1 < text.size() && (text[i + 1] == 'x' || text[i + 1] == 'X')) {
            ++i;
            continue;
        }
        const int digit = hex_digit(c);
        if (digit < 0) return false;
        if (high) {
            pending = digit << 4;
            high = false;
        } else {
            out.push_back(static_cast<std::uint8_t>(pending | digit));
            high = true;
        }
    }
    return high;
}

void print_hex(const std::uint8_t *data, std::size_t size) {
    static const char kDigits[] = "0123456789abcdef";
    for (std::size_t i = 0; i < size; ++i) {
        std::cout << kDigits[data[i] >> 4] << kDigits[data[i] & 0xF];
    }
}

std::uint32_t read_u32(const std::uint8_t *data, std::size_t offset) {
    std::uint32_t value = 0;
    std::memcpy(&value, data + offset, sizeof value);
    return value;
}

std::uint64_t read_u64(const std::uint8_t *data, std::size_t offset) {
    std::uint64_t value = 0;
    std::memcpy(&value, data + offset, sizeof value);
    return value;
}

std::string hex_u32(std::uint32_t value) {
    char buffer[16];
    std::snprintf(buffer, sizeof buffer, "%08x", static_cast<unsigned int>(value));
    return buffer;
}

std::string hex_u64(std::uint64_t value) {
    char buffer[32];
    std::snprintf(buffer, sizeof buffer, "%016llx", static_cast<unsigned long long>(value));
    return buffer;
}

// The literal instruction encodings live in `arith_literals.cpp` (no LTO in the build), so
// that the exhaustive sweep below cannot be folded away; see that file.
int run_exhaustive() {
    std::uint64_t selector_digest = 0;
    std::uint64_t buffer_digest = 0;
    volatile std::uint64_t sink = 0;  // every iteration must be observable
    for (std::uint64_t i = 0; i <= 0xFFFFFFFFull; ++i) {
        const auto value = static_cast<std::uint32_t>(i);
        const std::uint32_t reconstructed = eu4::re::gfx::words_from_selector(value);
        if (reconstructed != eu4::re::test_support::literal_selector_encoding(value)) {
            return fail("selector-mismatch");
        }
        const std::uint32_t reconstructed_buffer = eu4::re::gfx::words_from_buffer(value);
        if (reconstructed_buffer != eu4::re::test_support::literal_buffer_encoding(value)) {
            return fail("buffer-mismatch");
        }
        selector_digest += reconstructed;
        buffer_digest += reconstructed_buffer;
        sink = selector_digest ^ buffer_digest;
    }
    // Reading the sink back both keeps it "used" and checks the last iteration's value.
    const std::uint64_t observed = sink;
    if (observed != (selector_digest ^ buffer_digest)) return fail("sink-diverged");
    std::cout << "exhaustive=ok cases=4294967296 digest="
              << hex_u64(selector_digest ^ buffer_digest) << '\n';
    return 0;
}

int run_words(bool from_buffer) {
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        std::vector<std::uint8_t> bytes;
        if (!parse_hex(line, bytes) || bytes.size() > 4) return fail("bad-hex-input");
        std::uint32_t value = 0;
        for (std::uint8_t byte : bytes) value = (value << 8) | byte;  // input is big-endian text
        const std::uint32_t words = from_buffer ? eu4::re::gfx::words_from_buffer(value)
                                                : eu4::re::gfx::words_from_selector(value);
        std::cout << hex_u32(words) << '\n';
    }
    return 0;
}

int run_scenario(int argc, char **argv) {
    if (argc != 5) return fail("usage");
    const std::uint32_t slot = static_cast<std::uint32_t>(std::strtoul(argv[2], nullptr, 10));
    const std::uint32_t selector =
        static_cast<std::uint32_t>(std::strtoul(argv[3], nullptr, 16));
    const bool buffer_absent = std::strcmp(argv[4], "absent") == 0;

    if (slot >= eu4::re::gfx::kSlotCount) return fail("slot-out-of-domain");
    if (buffer_absent && selector == eu4::re::gfx::kSelectorReadFromBuffer) {
        return fail("missing-buffer-input");
    }

    std::string prestate;
    std::getline(std::cin, prestate);
    std::vector<std::uint8_t> bytes;
    if (!parse_hex(prestate, bytes)) return fail("bad-hex-input");
    if (bytes.size() != kContextSize) return fail("prestate-size");

    Context ctx{};
    std::memcpy(&ctx, bytes.data(), kContextSize);

    std::uint32_t buffer_dword = 0;
    const void *buffer = nullptr;
    void *guard = nullptr;
    std::size_t guard_size = 0;
    if (buffer_absent) {
        // `absent` means genuinely unreadable contents, not a readable zero: the explicit-length
        // path must not depend on bytes the capture never declared. The pointer remains non-null
        // so the stored-pointer role is still observable.
        const long page_size = sysconf(_SC_PAGESIZE);
        if (page_size <= 0) return fail("absent-buffer-unavailable");
        guard_size = static_cast<std::size_t>(page_size);
        guard = mmap(nullptr, guard_size, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (guard == MAP_FAILED || guard == nullptr) return fail("absent-buffer-unavailable");
        buffer = guard;
    } else {
        std::vector<std::uint8_t> dword_bytes;
        if (!parse_hex(argv[4], dword_bytes) || dword_bytes.empty() || dword_bytes.size() > 4) {
            return fail("bad-buffer-dword");
        }
        // The argument is a number in hex, as in the words modes - not a byte sequence.
        for (std::uint8_t byte : dword_bytes) {
            buffer_dword = (buffer_dword << 8) | byte;
        }
        buffer = &buffer_dword;
    }

    void *ctx_pointer = &ctx;
    void **handle = &ctx_pointer;
    const std::uint64_t handle_before = reinterpret_cast<std::uint64_t>(ctx_pointer);
    const std::uint32_t buffer_before = buffer_dword;

    // Call the six-position adapter, with garbage in the two positions the original ignores:
    // the same call shape an RE3 redirection will use, and every run re-checks that the unused
    // positions are not consumed.
    void *returned = eu4::re::gfx::gfx_set_constant_buffer_abi(
        handle, buffer, 0xDEADBEEFDEADBEEFull, slot, 0xFEEDFACEFEEDFACEull, selector);

    const bool handle_unchanged =
        reinterpret_cast<std::uint64_t>(ctx_pointer) == handle_before;
    const bool buffer_unchanged = buffer_absent || buffer_dword == buffer_before;
    if (guard != nullptr) {
        (void)munmap(guard, guard_size);
    }

    std::uint8_t post[kContextSize];
    std::memcpy(post, &ctx, kContextSize);

    const std::uint64_t stored_pointer = read_u64(post, kBufferBase + (slot * kSlotStride));

    std::cout << "size=" << hex_u64(kContextSize) << '\n';
    std::cout << "post=";
    print_hex(post, kContextSize);
    std::cout << '\n';
    std::cout << "rax=";
    if (returned == static_cast<void *>(&ctx)) {
        std::cout << "ctx\n";
    } else {
        std::cout << hex_u64(reinterpret_cast<std::uint64_t>(returned)) << '\n';
    }
    std::cout << "word_count=" << hex_u32(read_u32(post, kWordCountBase + (slot * kSlotStride)))
              << '\n';
    std::cout << "buffer=";
    if (stored_pointer == reinterpret_cast<std::uint64_t>(buffer)) {
        std::cout << "ARG\n";
    } else {
        std::cout << hex_u64(stored_pointer) << '\n';
    }
    std::cout << "dirty=" << hex_u32(read_u32(post, kDirtyOffset) & 0xFFu) << '\n';
    std::cout << "handle_preserved=" << (handle_unchanged ? "unchanged" : "changed") << '\n';
    std::cout << "buffer_preserved=";
    if (buffer_absent) {
        std::cout << "absent\n";
    } else {
        std::cout << (buffer_unchanged ? "unchanged\n" : "changed\n");
    }
    return 0;
}

}  // namespace

int main(int argc, char **argv) {
    if (argc < 2) return fail("usage");
    const std::string mode = argv[1];
    if (mode == "words") {
        if (argc != 3) return fail("usage");
        const std::string path = argv[2];
        if (path == "selector") return run_words(false);
        if (path == "buffer") return run_words(true);
        return fail("usage");
    }
    if (mode == "scenario") return run_scenario(argc, argv);
    if (mode == "exhaustive") return run_exhaustive();
    return fail("usage");
}
