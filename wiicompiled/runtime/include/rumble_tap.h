#pragma once

#include <cstdint>

// Rumble tap: every motor request the game makes is also sent, as one JSON line per datagram, to a local Unix
// datagram socket, so an outside program can react to it (see tools/rumble-listener.py).
//
//   {"seq":12,"t_ms":1731190000123,"dev":"pad","chan":0,"cmd":1,"applied":1}
//
//   seq      counts requests since the game started; a gap means requests were missed (no listener yet, or its
//            receive buffer was full)
//   t_ms     wall clock, milliseconds since the Unix epoch
//   dev      "pad"  GameCube controller, PADControlMotor: cmd 0 = stop, 1 = rumble, 2 = stop hard
//            "wpad" Wii Remote, WPADControlMotor:         cmd 0 = off,  1 = on
//   chan     the controller port / remote channel, 0 to 3
//   cmd      what the game asked for
//   applied  what the runtime did with it (differs from cmd only when "Controller vibration" is off in the options)
//
// Where it goes: $BRAWL_RUMBLE_SOCKET if set (an empty value or "off" switches the tap off), else
// $XDG_RUNTIME_DIR/brawl-rumble.sock, else /tmp/brawl-rumble-<uid>.sock.
//
// The tap is fire-and-forget: it never blocks the game, never needs a listener (with none running it makes one
// failed attempt a second), and never touches guest state, so save states and netplay lockstep are unaffected.
// It does nothing on Windows.
namespace RumbleTap {

enum class Device : uint8_t { GcPad, Wpad };

void Emit(Device device, uint32_t chan, uint32_t requested, uint32_t applied);

// Overrides the destination (tests). An empty path switches the tap off; also forgets any failure backoff.
void SetPath(const char* path);

} // namespace RumbleTap
