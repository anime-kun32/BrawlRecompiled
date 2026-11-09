#!/usr/bin/env python3
"""Receives the rumble requests Brawl Recompiled sends to a local Unix datagram socket.

  python tools/rumble-listener.py                  one readable line per request
  python tools/rumble-listener.py --changes        only when a controller's motor state changes
  python tools/rumble-listener.py --raw | jq .     the game's JSON lines untouched, for piping into another program

Start it before or after the game; the game only sends while a listener is there. The socket is
$BRAWL_RUMBLE_SOCKET, else $XDG_RUNTIME_DIR/brawl-rumble.sock, else /tmp/brawl-rumble-<uid>.sock (same rule as the
game, see wiicompiled/runtime/include/rumble_tap.h for the message format). Stop with Ctrl+C.

To build your own receiver, copy handle() below: `msg` is the decoded JSON object
  {"seq", "t_ms", "dev": "pad"|"wpad", "chan": 0-3, "cmd": int, "applied": int}
"""
import argparse
import json
import os
import signal
import socket
import stat
import sys
import time

COMMANDS = {"pad": {0: "stop", 1: "rumble", 2: "stop-hard"}, "wpad": {0: "off", 1: "on"}}


def default_path():
    env = os.environ.get("BRAWL_RUMBLE_SOCKET")
    if env is not None:
        return env
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "brawl-rumble.sock")
    return "/tmp/brawl-rumble-%d.sock" % os.getuid()


def bind(path):
    """Binds the datagram socket, replacing a stale socket file but never one a live listener holds."""
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            sys.exit("%s exists and is not a socket, not touching it" % path)
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            probe.connect(path)
        except (ConnectionRefusedError, FileNotFoundError):
            os.unlink(path)  # nobody is bound to it: left over from a listener that was killed
        else:
            sys.exit("another listener is already running on %s" % path)
        finally:
            probe.close()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(path)
    os.chmod(path, 0o600)  # only you can feed it
    return sock


def handle(msg, state, changes_only):
    """Prints one request. This is the part to replace with your own reaction."""
    name = COMMANDS.get(msg["dev"], {}).get(msg["cmd"], "cmd %d" % msg["cmd"])
    key = (msg["dev"], msg["chan"])
    if changes_only and state.get(key) == msg["applied"]:
        return
    state[key] = msg["applied"]
    suppressed = "  (vibration is off in the game's options)" if msg["applied"] != msg["cmd"] else ""
    stamp = time.strftime("%H:%M:%S", time.localtime(msg["t_ms"] / 1000)) + ".%03d" % (msg["t_ms"] % 1000)
    print("%s  #%-6d %-4s[%d]  %s%s" % (stamp, msg["seq"], msg["dev"], msg["chan"], name, suppressed), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--path", default=default_path(), help="socket to listen on (default: %(default)s)")
    ap.add_argument("--raw", action="store_true", help="print each datagram as received (JSON lines)")
    ap.add_argument("--changes", action="store_true", help="skip requests that repeat a controller's current state")
    args = ap.parse_args()
    if not args.path or args.path == "off":
        sys.exit("the socket is switched off (BRAWL_RUMBLE_SOCKET is empty or 'off')")

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sock = bind(args.path)
    print("listening on %s" % args.path, file=sys.stderr, flush=True)
    state, last_seq = {}, None
    try:
        while True:
            data = sock.recv(4096)
            if args.raw:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
                continue
            try:
                msg = json.loads(data)
                msg["dev"], msg["chan"], msg["cmd"], msg["applied"], msg["seq"], msg["t_ms"]
            except (ValueError, KeyError):
                print("ignoring a malformed message: %r" % data[:80], file=sys.stderr)
                continue
            if last_seq is not None and msg["seq"] > last_seq + 1:
                print("-- missed %d request(s)" % (msg["seq"] - last_seq - 1), file=sys.stderr, flush=True)
            last_seq = msg["seq"]
            handle(msg, state, args.changes)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        try:
            os.unlink(args.path)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
