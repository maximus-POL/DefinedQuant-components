"""Codepage-independent binary framing tests for CLI and future MCP STDIO."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable

import pytest
from defined_quant.stdio_framing import (
    MAX_BINARY_FRAME_BYTES,
    MAX_BINARY_READ_BYTES,
    BinaryFrameError,
    BinaryFrameErrorCode,
    BoundedBinaryFrameReader,
    configure_binary_descriptors,
    read_binary_to_eof,
    utf8_lf_frame,
    write_binary_bytes,
    write_utf8_lf_frame,
)


class _Chunks:
    def __init__(self, chunks: Iterable[bytes]) -> None:
        self._chunks = iter(chunks)
        self.requests: list[int] = []

    def __call__(self, maximum: int) -> bytes:
        self.requests.append(maximum)
        try:
            chunk = next(self._chunks)
        except StopIteration:
            return b""
        assert len(chunk) <= maximum
        return chunk


def test_lf_and_crlf_are_equivalent_json_whitespace() -> None:
    source = _Chunks([b'{"value":1}\n{"value":1}\r\n'])
    reader = BoundedBinaryFrameReader(source)

    lf = reader.read_frame()
    crlf = reader.read_frame()

    assert lf == '{"value":1}'
    assert crlf == '{"value":1}\r'
    assert json.loads(lf) == json.loads(crlf) == {"value": 1}
    assert reader.read_frame() is None
    assert max(source.requests) <= MAX_BINARY_READ_BYTES


def test_split_utf8_codepoint_is_decoded_only_after_frame_extraction() -> None:
    reader = BoundedBinaryFrameReader(
        _Chunks([b'{"currency":"\xe2', b"\x82", b'\xac"}\n'])
    )

    assert reader.read_frame() == '{"currency":"€"}'
    assert reader.read_frame() is None


def test_invalid_utf8_is_a_closed_framing_failure() -> None:
    reader = BoundedBinaryFrameReader(_Chunks([b'{"value":"\xff"}\n']))

    with pytest.raises(BinaryFrameError) as caught:
        reader.read_frame()

    assert caught.value.code is BinaryFrameErrorCode.INVALID_UTF8
    assert str(caught.value) == "invalid_utf8"


def test_ctrl_z_is_data_and_never_descriptor_eof() -> None:
    reader = BoundedBinaryFrameReader(_Chunks([b'{"value":"\x1a"}\n']))

    assert reader.read_frame() == '{"value":"\x1a"}'
    assert reader.read_frame() is None

    read_fd, write_fd = os.pipe()
    try:
        configure_binary_descriptors(read_fd, write_fd)
        write_binary_bytes(write_fd, b"before\x1aafter")
        os.close(write_fd)
        write_fd = -1
        assert read_binary_to_eof(read_fd) == b"before\x1aafter"
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def test_exact_two_mib_frame_is_accepted() -> None:
    content = b" " * (MAX_BINARY_FRAME_BYTES - 1) + b"\n"
    reader = BoundedBinaryFrameReader(
        _Chunks(
            content[index : index + MAX_BINARY_READ_BYTES]
            for index in range(0, len(content), MAX_BINARY_READ_BYTES)
        )
    )

    frame = reader.read_frame()

    assert frame is not None
    assert len(frame.encode("utf-8")) + 1 == MAX_BINARY_FRAME_BYTES
    assert reader.read_frame() is None


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (
            b" " * MAX_BINARY_FRAME_BYTES + b"\n",
            BinaryFrameErrorCode.FRAME_TOO_LARGE,
        ),
        (b"unterminated", BinaryFrameErrorCode.INCOMPLETE_FRAME),
    ],
)
def test_over_limit_and_unterminated_frames_fail_closed(
    content: bytes,
    code: BinaryFrameErrorCode,
) -> None:
    reader = BoundedBinaryFrameReader(
        _Chunks(
            content[index : index + MAX_BINARY_READ_BYTES]
            for index in range(0, len(content), MAX_BINARY_READ_BYTES)
        )
    )

    with pytest.raises(BinaryFrameError) as caught:
        reader.read_frame()

    assert caught.value.code is code


def test_future_output_frame_is_exact_utf8_with_lf_only() -> None:
    content = utf8_lf_frame('{"label":"Zażółć €"}')

    assert content == b'{"label":"Za\xc5\xbc\xc3\xb3\xc5\x82\xc4\x87 \xe2\x82\xac"}\n'
    assert content.endswith(b"\n")
    assert b"\r" not in content

    with pytest.raises(ValueError):
        utf8_lf_frame("two\nframes")

    read_fd, write_fd = os.pipe()
    try:
        write_utf8_lf_frame(write_fd, '{"label":"€"}')
        os.close(write_fd)
        write_fd = -1
        assert read_binary_to_eof(read_fd) == b'{"label":"\xe2\x82\xac"}\n'
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
