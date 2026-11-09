#include "rumble_tap.h"

#if defined(_WIN32)

namespace RumbleTap {
void Emit(Device, uint32_t, uint32_t, uint32_t) {}
void SetPath(const char*) {}
} // namespace RumbleTap

#else

#include <sys/socket.h>
#include <sys/un.h>
#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

namespace RumbleTap {
namespace detail { // named on purpose: the runtime builds as a unity build, anonymous-namespace names could collide

constexpr auto kRetryAfterFailure = std::chrono::seconds(1);

std::mutex g_lock;
bool g_resolved = false;
bool g_enabled = false;
sockaddr_un g_address{};
socklen_t g_addressLength = 0;
int g_fd = -1;
uint64_t g_seq = 0;
std::chrono::steady_clock::time_point g_retryAt{};

std::string DefaultPath()
{
    if (const char* env = std::getenv("BRAWL_RUMBLE_SOCKET")) {
        return env;
    }
    if (const char* runtimeDir = std::getenv("XDG_RUNTIME_DIR"); runtimeDir != nullptr && runtimeDir[0] != '\0') {
        return std::string(runtimeDir) + "/brawl-rumble.sock";
    }
    return "/tmp/brawl-rumble-" + std::to_string(static_cast<unsigned long>(::getuid())) + ".sock";
}

void Resolve(const std::string& path)
{
    g_resolved = true;
    g_enabled = false;
    g_retryAt = {};
    if (g_fd >= 0) {
        ::close(g_fd);
        g_fd = -1;
    }
    if (path.empty() || path == "off" || path.size() >= sizeof(g_address.sun_path)) {
        return;
    }
    std::memset(&g_address, 0, sizeof(g_address));
    g_address.sun_family = AF_UNIX;
    std::memcpy(g_address.sun_path, path.c_str(), path.size() + 1);
    g_addressLength = static_cast<socklen_t>(offsetof(sockaddr_un, sun_path) + path.size() + 1);
    g_enabled = true;
}

bool OpenSocket()
{
    const int fd = ::socket(AF_UNIX, SOCK_DGRAM, 0);
    if (fd < 0) {
        return false;
    }
    ::fcntl(fd, F_SETFD, FD_CLOEXEC);
    ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL, 0) | O_NONBLOCK);
    g_fd = fd;
    return true;
}

} // namespace detail

void SetPath(const char* path)
{
    std::lock_guard<std::mutex> guard(detail::g_lock);
    detail::Resolve(path != nullptr ? path : "");
}

void Emit(Device device, uint32_t chan, uint32_t requested, uint32_t applied)
{
    std::lock_guard<std::mutex> guard(detail::g_lock);
    if (!detail::g_resolved) {
        detail::Resolve(detail::DefaultPath());
    }
    if (!detail::g_enabled) {
        return;
    }
    const uint64_t seq = detail::g_seq++;
    const auto now = std::chrono::steady_clock::now();
    if (now < detail::g_retryAt) {
        return;
    }
    if (detail::g_fd < 0 && !detail::OpenSocket()) {
        detail::g_retryAt = now + detail::kRetryAfterFailure;
        return;
    }

    const long long wallMs = std::chrono::duration_cast<std::chrono::milliseconds>(
                                 std::chrono::system_clock::now().time_since_epoch())
                                 .count();
    char line[192];
    const int length = std::snprintf(line, sizeof(line),
        "{\"seq\":%llu,\"t_ms\":%lld,\"dev\":\"%s\",\"chan\":%u,\"cmd\":%u,\"applied\":%u}\n",
        static_cast<unsigned long long>(seq), wallMs, device == Device::GcPad ? "pad" : "wpad",
        static_cast<unsigned>(chan), static_cast<unsigned>(requested), static_cast<unsigned>(applied));
    if (length <= 0 || length >= static_cast<int>(sizeof(line))) {
        return;
    }

    const ssize_t sent = ::sendto(detail::g_fd, line, static_cast<size_t>(length), MSG_DONTWAIT,
        reinterpret_cast<const sockaddr*>(&detail::g_address), detail::g_addressLength);
    if (sent < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
        // No listener (ENOENT / ECONNREFUSED) or something stranger: stop trying for a second, so a game
        // that spams motor commands every frame does not turn into a syscall every frame.
        detail::g_retryAt = now + detail::kRetryAfterFailure;
    }
    // EAGAIN means the listener is alive but behind: the datagram is dropped and `seq` shows the gap.
}

} // namespace RumbleTap

#endif
