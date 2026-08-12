import pytest

from client.transport import (
    FrameDecoder,
    MAX_FRAME_SIZE,
    build_proof,
    canonical_auth_input,
    decode_json_payload,
    derive_key,
    encode_json_payload,
    encode_message,
    verify_proof,
)


def test_frame_decoder_keeps_partial_data_across_poll_ticks():
    header, payload = encode_message(b"PONG")
    decoder = FrameDecoder()
    assert decoder.feed(header[:2]) == []
    assert decoder.feed(header[2:] + payload[:1]) == []
    assert decoder.feed(payload[1:]) == [b"PONG"]


def test_frame_decoder_rejects_oversized_frame():
    decoder = FrameDecoder()
    oversized = (MAX_FRAME_SIZE + 1).to_bytes(4, "big")
    with pytest.raises(ValueError, match="frame too large"):
        decoder.feed(oversized)


def test_frame_decoder_returns_all_complete_frames():
    first = b"".join(encode_message(b"one"))
    second = b"".join(encode_message(b"two"))
    assert FrameDecoder().feed(first + second) == [b"one", b"two"]


def test_legacy_key_matches_controller():
    from C2.crypto import derive_key as controller_derive_key

    assert derive_key("pw") == controller_derive_key("pw")


def test_json_payload_is_canonical_and_requires_an_object():
    assert encode_json_payload({"z": 1, "a": "ไทย"}) == (
        b'{"a":"\xe0\xb9\x84\xe0\xb8\x97\xe0\xb8\xa2","z":1}'
    )
    assert decode_json_payload(b'{"z":1,"a":2}') == {"a": 2, "z": 1}
    with pytest.raises(ValueError, match="JSON object"):
        decode_json_payload(b"[]")


def test_json_payload_rejects_invalid_utf8_and_oversized_values():
    with pytest.raises(UnicodeDecodeError):
        decode_json_payload(b"\xff")
    with pytest.raises(ValueError, match="too large"):
        decode_json_payload(b"{" + b'\"x\":\"' + b"a" * 65536 + b'\"}')


def test_auth_proof_uses_one_canonical_nonce_bound_input():
    secret = b"s" * 32
    expected_input = canonical_auth_input(1, "agent", "key", b"nonce-a")
    assert expected_input != canonical_auth_input(1, "agent", "key", b"nonce-b")
    proof = build_proof(secret, 1, "agent", "key", b"nonce-a")
    assert verify_proof(secret, 1, "agent", "key", b"nonce-a", proof)
    assert not verify_proof(secret, 1, "agent", "key", b"nonce-b", proof)
