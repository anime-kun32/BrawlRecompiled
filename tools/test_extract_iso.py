#!/usr/bin/env python3
"""Self-test for extract_wbfs.py: builds a tiny synthetic Wii .iso (fake data, same encrypted-partition layout) and
checks that extraction, --only filtering and the bad-input errors work. Needs no real disc.  python tools/test_extract_iso.py"""
import os, struct, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract_wbfs as ex
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def enc(key, iv, data):
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return e.update(data) + e.finalize()

def build_iso(path, files):
    """files: {path in the FST: bytes}. Returns the main.dol bytes."""
    gid, magic = b"RSBE01", bytes.fromhex("5D1C9EA3")
    # --- the decrypted data stream of the partition
    dol = bytearray(0x200); dol[0:4] = struct.pack(">I", 0x100); dol[0x90:0x94] = struct.pack(">I", 0x100)
    dol[0x100:0x200] = b"D" * 0x100
    boot = bytearray(0x440); boot[0:6] = gid; boot[0x18:0x1C] = magic
    names, entries, dirs = bytearray(), [], {}
    def name_off(n):
        o = len(names); names.extend(n.encode() + b"\0"); return o
    layout = []  # (kind, name, parent_dir_index, payload)
    fst_entries = [None]
    order = sorted(files)
    dirnames = sorted({p.split("/")[0] for p in order if "/" in p})
    idx = 1; plan = []
    for d in dirnames:
        members = [p for p in order if p.startswith(d + "/")]
        plan.append(("dir", d, idx, len(members))); idx += 1 + len(members)
        for m in members: plan.append(("file", m.split("/", 1)[1], m, None))
    for p in order:
        if "/" not in p: plan.append(("file", p, p, None))
    total = 1 + len(plan)
    data_start = 0x2440 + 0x20 + 0x100            # apploader, then main.dol, then fst, then the files
    dol_off = (data_start + 0x1F) & ~0x1F
    fst_off = dol_off + 0x200
    fst_size = total * 12 + 512
    cursor = (fst_off + fst_size + 0x1F) & ~0x1F
    fst = bytearray(total * 12); fst[0] = 1; struct.pack_into(">I", fst, 8, total)
    payloads = {}
    for i, (kind, nm, a, b) in enumerate(plan, start=1):
        o = i * 12
        if kind == "dir":
            fst[o] = 1; fst[o + 1:o + 4] = name_off(nm).to_bytes(3, "big"); struct.pack_into(">II", fst, o + 4, 0, a + b + 1)   # a dir's size field is the index just past its last entry
        else:
            body = files[a]; fst[o + 1:o + 4] = name_off(nm).to_bytes(3, "big")
            struct.pack_into(">II", fst, o + 4, cursor >> 2, len(body)); payloads[cursor] = body
            cursor = (cursor + len(body) + 0x1F) & ~0x1F
    fst = bytes(fst) + bytes(names)
    fst = fst.ljust(fst_size, b"\0")
    struct.pack_into(">III", boot, 0x420, dol_off >> 2, fst_off >> 2, fst_size >> 2)
    apl = bytearray(0x20); struct.pack_into(">II", apl, 0x14, 0x40, 0)
    stream = bytearray(cursor + 0x100)
    stream[0:0x440] = boot; stream[0x440:0x2440] = b"\1" * 0x2000
    stream[0x2440:0x2460] = apl; stream[0x2460:0x24A0] = b"A" * 0x40
    stream[dol_off:dol_off + 0x200] = dol; stream[fst_off:fst_off + len(fst)] = fst
    for off, body in payloads.items(): stream[off:off + len(body)] = body
    # --- encrypt it into 0x8000-byte clusters (0x400 bytes of hashes, 0x7C00 of data)
    title_key, title_id = bytes(range(16)), b"\0\1\0\0RSBE"
    enc_key = enc(ex.COMMON_KEY, title_id + bytes(8), title_key)
    clusters = bytearray()
    for c in range((len(stream) + 0x7BFF) // 0x7C00):
        plain = bytes(stream[c * 0x7C00:(c + 1) * 0x7C00]).ljust(0x7C00, b"\0")
        iv = bytes([c & 0xFF]) * 16
        hashes = bytearray(0x400); hashes[0x3D0:0x3E0] = iv
        clusters += hashes + enc(title_key, iv, plain)
    part_off, data_off = 0x50000, 0x20000
    hdr = bytearray(0x2C0); hdr[0x1BF:0x1CF] = enc_key; hdr[0x1DC:0x1E4] = title_id; hdr[0x1F1] = 0
    struct.pack_into(">I", hdr, 0x2B8, data_off >> 2)
    disc = bytearray(part_off + data_off + len(clusters))
    disc[0:6] = gid; disc[0x18:0x1C] = magic
    struct.pack_into(">II", disc, 0x40000, 1, 0x40020 >> 2)         # group 0: one partition, its table at 0x40020
    struct.pack_into(">II", disc, 0x40020, part_off >> 2, 0)        # type 0 = the data partition
    disc[part_off:part_off + 0x2C0] = hdr; disc[part_off + data_off:] = clusters
    open(path, "wb").write(disc)
    return bytes(dol)

def run(*args):
    return subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "extract_wbfs.py"), *args],
                          capture_output=True, text=True)

def main():
    files = {"module/a.rel": b"REL-A" * 3000, "module/b.rel": b"REL-B" * 10, "opening.bnr": b"BANNER" * 100}
    with tempfile.TemporaryDirectory() as tmp:
        iso = os.path.join(tmp, "fake.iso"); dol = build_iso(iso, files)
        r = run(iso, os.path.join(tmp, "all")); assert r.returncode == 0, r.stderr
        assert "game RSBE01" in r.stdout and "files 3" in r.stdout, r.stdout
        rd = lambda *p: open(os.path.join(tmp, *p), "rb").read()
        assert rd("all", "sys", "main.dol") == dol, "main.dol differs"
        assert rd("all", "files", "module", "a.rel") == files["module/a.rel"], "multi-cluster file differs"
        assert rd("all", "files", "opening.bnr") == files["opening.bnr"]
        assert rd("all", "files", "module", "b.rel") == files["module/b.rel"], "module/b.rel not in module/"
        r = run(iso, os.path.join(tmp, "part"), "--only", "sys/", "--only", "files/module/"); assert r.returncode == 0, r.stderr
        assert "files 2" in r.stdout, r.stdout
        assert rd("part", "sys", "main.dol") == dol and rd("part", "files", "module", "b.rel") == files["module/b.rel"]
        assert not os.path.exists(os.path.join(tmp, "part", "files", "opening.bnr")), "--only did not filter"
        junk = os.path.join(tmp, "junk.iso"); open(junk, "wb").write(os.urandom(4096))
        r = run(junk, os.path.join(tmp, "x")); assert r.returncode != 0 and "not a Wii" in r.stderr, r.stderr
    print("test_extract_iso: ok")

main()
