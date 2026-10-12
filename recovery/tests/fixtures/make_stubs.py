#!/usr/bin/env python3
"""Turns a binary OpenPGP secret-key export into stub keys, as gpg writes them, and armors the result.

  make_stubs.py --main keep|dummy|card --sub keep|dummy|card IN OUT

keep  leaves the secret part as it is
dummy replaces it with a GNU gnu-dummy S2K (secret missing, as --export-secret-subkeys writes for the main key)
card  replaces it with a GNU divert-to-card S2K (the key lives on a smart card)
Only test fixtures use this; card stubs cannot be made with gpg alone without a card.
"""

import argparse
import base64
import sys

SECRET_KEY, SECRET_SUBKEY = 5, 7
SERIAL = bytes.fromhex("D2760001240103040006123456780000")


def read_packets(data: bytes):
    i = 0
    while i < len(data):
        first = data[i]
        i += 1
        if first & 0x40:  # new format
            tag, size = first & 0x3F, data[i]
            i += 1
            if size < 192:
                length = size
            elif size < 224:
                length = ((size - 192) << 8) + data[i] + 192
                i += 1
            elif size == 255:
                length = int.from_bytes(data[i:i + 4], "big")
                i += 4
            else:
                raise ValueError("partial body lengths do not occur in key exports")
        else:  # old format
            tag, kind = (first >> 2) & 0x0F, first & 3
            width = {0: 1, 1: 2, 2: 4}[kind]
            length = int.from_bytes(data[i:i + width], "big")
            i += width
        yield tag, data[i:i + length]
        i += length


def write_packet(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 192:
        header = bytes([n])
    elif n < 8384:
        header = bytes([((n - 192) >> 8) + 192, (n - 192) & 0xFF])
    else:
        header = b"\xff" + n.to_bytes(4, "big")
    return bytes([0xC0 | tag]) + header + body


def mpi_size(body: bytes, offset: int) -> int:
    bits = int.from_bytes(body[offset:offset + 2], "big")
    return 2 + (bits + 7) // 8


def public_end(body: bytes) -> int:
    """Offset where the secret part of a v4 secret-key packet starts."""
    if body[0] != 4:
        raise ValueError(f"key packet version {body[0]} is not supported")
    algo, offset = body[5], 6
    counts = {1: 2, 2: 2, 3: 2, 16: 3, 17: 4, 18: 1, 19: 1, 22: 1}
    if algo not in counts:
        raise ValueError(f"public-key algorithm {algo} is not supported")
    if algo in (18, 19, 22):  # curve OID
        offset += 1 + body[offset]
    for _ in range(counts[algo]):
        offset += mpi_size(body, offset)
    if algo == 18:  # ECDH KDF parameters
        offset += 1 + body[offset]
    return offset


def stub(body: bytes, how: str) -> bytes:
    if how == "keep":
        return body
    tail = b"\xff\x00\x65\x00GNU"  # S2K usage 255, cipher 0, S2K type 101, hash 0, "GNU"
    tail += b"\x01" if how == "dummy" else b"\x02" + bytes([len(SERIAL)]) + SERIAL
    return body[:public_end(body)] + tail


def crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(data: bytes) -> str:
    text = base64.b64encode(data).decode()
    lines = [text[i:i + 64] for i in range(0, len(text), 64)]
    check = base64.b64encode(crc24(data).to_bytes(3, "big")).decode()
    return ("-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" + "\n".join(lines) + f"\n={check}\n"
            "-----END PGP PRIVATE KEY BLOCK-----\n")


def main(argv) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--main", choices=("keep", "dummy", "card"), required=True)
    p.add_argument("--sub", choices=("keep", "dummy", "card"), required=True)
    p.add_argument("source")
    p.add_argument("target")
    args = p.parse_args(argv)
    with open(args.source, "rb") as f:
        data = f.read()
    out = b""
    for tag, body in read_packets(data):
        if tag == SECRET_KEY:
            body = stub(body, args.main)
        elif tag == SECRET_SUBKEY:
            body = stub(body, args.sub)
        out += write_packet(tag, body)
    with open(args.target, "w", encoding="ascii") as f:
        f.write(armor(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
