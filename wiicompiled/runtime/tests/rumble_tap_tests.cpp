// The rumble tap sends one JSON line per motor request to a Unix datagram socket. This binds its own socket,
// so it needs no running listener.
#include "rumble_tap.h"

#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {

int g_failures = 0;

#define CHECK(cond)                                                                       \
    do {                                                                                  \
        if (!(cond)) {                                                                    \
            std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);         \
            ++g_failures;                                                                 \
        }                                                                                 \
    } while (0)

int Listen(const std::string& path)
{
    const int fd = ::socket(AF_UNIX, SOCK_DGRAM, 0);
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, path.c_str(), sizeof(addr.sun_path) - 1);
    ::unlink(path.c_str());
    if (fd < 0 || ::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        std::perror("bind");
        std::exit(2);
    }
    timeval timeout{1, 0};
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    return fd;
}

std::string Receive(int fd)
{
    char buffer[512];
    const ssize_t n = ::recv(fd, buffer, sizeof(buffer) - 1, 0);
    return n > 0 ? std::string(buffer, static_cast<size_t>(n)) : std::string();
}

} // namespace

int main()
{
    char dirTemplate[] = "/tmp/rumble-tap-test-XXXXXX";
    const char* dir = ::mkdtemp(dirTemplate);
    if (dir == nullptr) {
        std::perror("mkdtemp");
        return 2;
    }
    const std::string path = std::string(dir) + "/t.sock";

    // With nobody listening Emit must return at once, and a second call inside the backoff must too.
    RumbleTap::SetPath(path.c_str());
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 1000; ++i) {
        RumbleTap::Emit(RumbleTap::Device::GcPad, 0, 1, 1);
    }
    CHECK(std::chrono::steady_clock::now() - start < std::chrono::milliseconds(500));

    // A listener appears: SetPath forgets the backoff, and the first datagram is the next request.
    const int fd = Listen(path);
    RumbleTap::SetPath(path.c_str());
    RumbleTap::Emit(RumbleTap::Device::GcPad, 2, 1, 0);   // rumble asked for, vibration off in options
    RumbleTap::Emit(RumbleTap::Device::Wpad, 3, 0, 0);

    const std::string first = Receive(fd);
    const std::string second = Receive(fd);
    // seq counts every request since start, including the 1000 nobody heard: consecutive from here on.
    const unsigned long long firstSeq = std::strtoull(first.c_str() + first.find("\"seq\":") + 6, nullptr, 10);
    const unsigned long long secondSeq = std::strtoull(second.c_str() + second.find("\"seq\":") + 6, nullptr, 10);
    CHECK(firstSeq == 1000);
    CHECK(secondSeq == firstSeq + 1);
    CHECK(first.find("\"dev\":\"pad\",\"chan\":2,\"cmd\":1,\"applied\":0}\n") != std::string::npos);
    CHECK(first.find("\"t_ms\":") != std::string::npos);
    CHECK(second.find("\"dev\":\"wpad\",\"chan\":3,\"cmd\":0,\"applied\":0}\n") != std::string::npos);

    // Switched off: nothing is sent.
    RumbleTap::SetPath("");
    RumbleTap::Emit(RumbleTap::Device::GcPad, 0, 1, 1);
    CHECK(Receive(fd).empty());

    // A path longer than sun_path is refused instead of overflowing.
    RumbleTap::SetPath(std::string(300, 'x').c_str());
    RumbleTap::Emit(RumbleTap::Device::GcPad, 0, 1, 1);

    ::close(fd);
    ::unlink(path.c_str());
    ::rmdir(dir);
    if (g_failures == 0) {
        std::puts("rumble_tap_tests: ok");
    }
    return g_failures == 0 ? 0 : 1;
}
