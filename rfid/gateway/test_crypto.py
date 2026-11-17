#!/usr/bin/env python3
"""Crypto checks for the LoRa protocol. Run with: python test_crypto.py

The known-answer vector below is duplicated byte-for-byte in transmitter.ino's
selfTest(). If mbedtls and `cryptography` ever disagree about nonce layout, AAD,
or tag placement, both sides fail loudly instead of looking like a flaky radio.
"""

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gateway import (
    HDR_LEN,
    ST_COMMITTED,
    TYPE_ACK,
    TYPE_DATA,
    make_nonce,
    open_data,
    seal_ack,
)

KEY = bytes(range(32))


def test_known_answer():
    """Fixed key/nonce/AAD/plaintext -> exactly these bytes. Shared with the ESP32."""
    nonce = make_nonce(TYPE_DATA, 7, 1234)
    aad = bytes([0x01, TYPE_DATA]) + (7).to_bytes(4, "little") + (1234).to_bytes(8, "little")
    out = AESGCM(KEY).encrypt(nonce, bytes.fromhex("deadbeef"), aad)

    assert nonce.hex() == "0107000000d2040000000000", nonce.hex()
    assert aad.hex() == "010107000000d204000000000000", aad.hex()
    assert out[:-16].hex() == "ff6e466b", out[:-16].hex()
    assert out[-16:].hex() == "e96f39d40ce7991df9d677d64c7531df", out[-16:].hex()


def test_nonce_differs_by_direction():
    """The whole point of the type byte: DATA and its ACK must not share a nonce."""
    assert make_nonce(TYPE_DATA, 1, 99) != make_nonce(TYPE_ACK, 1, 99)
    assert len(make_nonce(TYPE_DATA, 1, 99)) == 12
    # Distinct device or counter must also give a distinct nonce.
    assert make_nonce(TYPE_DATA, 1, 99) != make_nonce(TYPE_DATA, 2, 99)
    assert make_nonce(TYPE_DATA, 1, 99) != make_nonce(TYPE_DATA, 1, 100)


def build_data(key, device_id, counter, uid):
    """Mirror of sealData() in transmitter.ino, for tests only."""
    header = (
        bytes([0x01, TYPE_DATA])
        + device_id.to_bytes(4, "little")
        + counter.to_bytes(8, "little")
    )
    return header + AESGCM(key).encrypt(make_nonce(TYPE_DATA, device_id, counter), uid, header)


def test_data_roundtrip():
    frame = build_data(KEY, 1, 42, bytes.fromhex("a1b2c3d4"))
    assert len(frame) == HDR_LEN + 4 + 16
    device_id, counter, uid = open_data(KEY, frame)
    assert (device_id, counter, uid.hex()) == (1, 42, "a1b2c3d4")


def test_7_byte_uid_roundtrip():
    uid = bytes.fromhex("04a1b2c3d4e5f6")
    _, _, got = open_data(KEY, build_data(KEY, 2, 7, uid))
    assert got == uid


def test_tampered_ciphertext_rejected():
    frame = bytearray(build_data(KEY, 1, 42, bytes.fromhex("a1b2c3d4")))
    frame[HDR_LEN] ^= 0x01
    try:
        open_data(KEY, bytes(frame))
    except InvalidTag:
        return
    raise AssertionError("tampered ciphertext was accepted")


def test_tampered_header_rejected():
    """The header is AAD, so flipping a counter bit must also fail the tag."""
    frame = bytearray(build_data(KEY, 1, 42, bytes.fromhex("a1b2c3d4")))
    frame[6] ^= 0x01
    try:
        open_data(KEY, bytes(frame))
    except InvalidTag:
        return
    raise AssertionError("tampered header was accepted")


def test_wrong_key_rejected():
    frame = build_data(KEY, 1, 42, bytes.fromhex("a1b2c3d4"))
    try:
        open_data(bytes(range(1, 33)), frame)
    except InvalidTag:
        return
    raise AssertionError("frame decrypted under the wrong key")


def test_ack_shape_and_echo():
    ack = seal_ack(KEY, 1, 42, ST_COMMITTED)
    assert len(ack) == HDR_LEN + 1 + 16 == 31, len(ack)
    assert ack[1] == TYPE_ACK
    # The transmitter matches on these to reject replayed ACKs.
    assert int.from_bytes(ack[2:6], "little") == 1
    assert int.from_bytes(ack[6:14], "little") == 42

    status = AESGCM(KEY).decrypt(make_nonce(TYPE_ACK, 1, 42), ack[HDR_LEN:], ack[:HDR_LEN])
    assert status == bytes([ST_COMMITTED])


def test_ack_not_openable_as_data():
    """An ACK must never be mistaken for a DATA frame."""
    try:
        open_data(KEY, seal_ack(KEY, 1, 42, ST_COMMITTED))
    except ValueError:
        return
    raise AssertionError("ACK was accepted as DATA")


def test_short_frame_rejected():
    try:
        open_data(KEY, b"\x01\x01\x00")
    except ValueError:
        return
    raise AssertionError("undersized frame was accepted")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
