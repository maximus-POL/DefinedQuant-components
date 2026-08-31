"""Bounded binary LF framing for CLI and future local STDIO transports."""

from __future__ import annotations

import os
from collections.abc import Callable
from enum import StrEnum

MAX_BINARY_FRAME_BYTES = 2 * 1024 * 1024
MAX_BINARY_READ_BYTES = 64 * 1024


class BinaryFrameErrorCode(StrEnum):
    """Closed internal framing failures that contain no platform exception text."""

    FRAME_TOO_LARGE = "frame_too_large"
    INCOMPLETE_FRAME = "incomplete_frame"
    INVALID_UTF8 = "invalid_utf8"
    READ_FAILED = "read_failed"
    WRITE_FAILED = "write_failed"


class BinaryFrameError(Exception):
    """Stable binary framing error suitable for transport-level redaction."""

    def __init__(self, code: BinaryFrameErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class BoundedBinaryFrameReader:
    """Extract strict UTF-8 frames only after a raw LF has been received."""

    def __init__(
        self,
        read: Callable[[int], bytes],
        *,
        maximum_bytes: int = MAX_BINARY_FRAME_BYTES,
    ) -> None:
        if not 0 < maximum_bytes <= MAX_BINARY_FRAME_BYTES:
            raise ValueError("binary frame limit is outside the frozen range")
        self._read = read
        self._maximum_bytes = maximum_bytes
        self._buffer = bytearray()
        self._eof = False

    @classmethod
    def from_descriptor(
        cls,
        descriptor: int,
        *,
        maximum_bytes: int = MAX_BINARY_FRAME_BYTES,
    ) -> BoundedBinaryFrameReader:
        """Read directly from one binary descriptor without a text wrapper."""

        if descriptor < 0:
            raise ValueError("binary descriptor must be non-negative")
        configure_binary_descriptors(descriptor)

        def read(maximum: int) -> bytes:
            try:
                return os.read(descriptor, maximum)
            except OSError:
                raise BinaryFrameError(BinaryFrameErrorCode.READ_FAILED) from None

        return cls(read, maximum_bytes=maximum_bytes)

    def read_frame(self) -> str | None:
        """Return one LF-delimited frame without its LF, or None at clean EOF."""

        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                frame_bytes = newline + 1
                if frame_bytes > self._maximum_bytes:
                    raise BinaryFrameError(BinaryFrameErrorCode.FRAME_TOO_LARGE)
                content = bytes(self._buffer[:newline])
                del self._buffer[:frame_bytes]
                try:
                    return content.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    raise BinaryFrameError(BinaryFrameErrorCode.INVALID_UTF8) from None

            if len(self._buffer) >= self._maximum_bytes:
                raise BinaryFrameError(BinaryFrameErrorCode.FRAME_TOO_LARGE)
            if self._eof:
                if self._buffer:
                    raise BinaryFrameError(BinaryFrameErrorCode.INCOMPLETE_FRAME)
                return None

            requested = min(
                MAX_BINARY_READ_BYTES,
                self._maximum_bytes - len(self._buffer),
            )
            try:
                chunk = self._read(requested)
            except BinaryFrameError:
                raise
            except Exception:
                raise BinaryFrameError(BinaryFrameErrorCode.READ_FAILED) from None
            if not isinstance(chunk, bytes) or len(chunk) > requested:
                raise BinaryFrameError(BinaryFrameErrorCode.READ_FAILED)
            if not chunk:
                self._eof = True
            else:
                self._buffer.extend(chunk)


def configure_binary_descriptors(*descriptors: int) -> None:
    """Disable Windows CRT text translation without a platform-name branch."""

    binary_flag = getattr(os, "O_BINARY", 0)
    if binary_flag == 0:
        return
    try:
        import msvcrt

        for descriptor in descriptors:
            if descriptor < 0:
                raise OSError
            msvcrt.setmode(descriptor, binary_flag)  # type: ignore[attr-defined]
    except (ImportError, OSError):
        raise BinaryFrameError(BinaryFrameErrorCode.READ_FAILED) from None


def read_binary_to_eof(descriptor: int) -> bytes:
    """Read raw bytes through true descriptor EOF; byte 0x1a has no special meaning."""

    chunks: list[bytes] = []
    try:
        configure_binary_descriptors(descriptor)
        while True:
            chunk = os.read(descriptor, MAX_BINARY_READ_BYTES)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError:
        raise BinaryFrameError(BinaryFrameErrorCode.READ_FAILED) from None


def write_binary_bytes(descriptor: int, content: bytes) -> None:
    """Write every byte without text encoding or newline translation."""

    view = memoryview(content)
    try:
        configure_binary_descriptors(descriptor)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
    except OSError:
        raise BinaryFrameError(BinaryFrameErrorCode.WRITE_FAILED) from None


def utf8_lf_frame(text: str) -> bytes:
    """Encode one future transport frame as strict UTF-8 with exactly one LF."""

    if "\r" in text or "\n" in text:
        raise ValueError("binary transport frames must be single-line text")
    return text.encode("utf-8", errors="strict") + b"\n"


def write_utf8_lf_frame(descriptor: int, text: str) -> None:
    """Write one exact future transport frame through a binary descriptor."""

    write_binary_bytes(descriptor, utf8_lf_frame(text))


__all__ = [
    "MAX_BINARY_FRAME_BYTES",
    "MAX_BINARY_READ_BYTES",
    "BinaryFrameError",
    "BinaryFrameErrorCode",
    "BoundedBinaryFrameReader",
    "configure_binary_descriptors",
    "read_binary_to_eof",
    "utf8_lf_frame",
    "write_binary_bytes",
    "write_utf8_lf_frame",
]
