"""Private lossless transport frames; logical evidence schemas/hashes are unchanged."""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
import zlib

# Per-record safety boundary, independent of the unchanged physical per-game caps.
NATIVE_MAX_RECORD_BYTES = 64 * 1024 * 1024
# Gym wraps an exact native body, parsed seat evidence and transition summaries.
# Four times the native boundary covers escaping/duplication plus envelope metadata.
MAX_RECORD_BYTES = 4 * NATIVE_MAX_RECORD_BYTES
COMPRESS_THRESHOLD = 4096

class ScanCancelled(Exception):
    """Cooperative stop, distinct from corrupt or incomplete evidence."""


def check_cancelled(cancel=None):
    if cancel is not None and cancel():
        raise ScanCancelled()


class RecordCodecError(ValueError):
    pass


def encode_record(logical: bytes, *, compress: bool = True) -> bytes:
    if not logical.endswith(b"\n") or len(logical) > MAX_RECORD_BYTES:
        raise RecordCodecError("record_size_or_framing_limit")
    if not compress or len(logical) < COMPRESS_THRESHOLD:
        return logical
    frame = {"recordCodec": 1, "encoding": "gzip-base64", "decodedBytes": len(logical),
             "data": base64.b64encode(gzip.compress(logical, compresslevel=6, mtime=0)).decode("ascii")}
    physical = json.dumps(frame, separators=(",", ":")).encode() + b"\n"
    return physical if len(physical) < len(logical) else logical


def decode_record(physical: bytes, *, max_bytes: int | None = None, cancel=None) -> bytes:
    limit = MAX_RECORD_BYTES if max_bytes is None else max_bytes
    if len(physical) > limit or not physical.endswith(b"\n"):
        raise RecordCodecError("record_size_or_framing_limit")
    check_cancelled(cancel)
    frame = json.loads(physical)
    check_cancelled(cancel)
    if not isinstance(frame, dict) or "recordCodec" not in frame:
        return physical
    if (set(frame) != {"recordCodec", "encoding", "decodedBytes", "data"} or
            type(frame["recordCodec"]) is not int or frame["recordCodec"] != 1 or
            frame["encoding"] != "gzip-base64" or type(frame["decodedBytes"]) is not int or
            not 0 < frame["decodedBytes"] <= limit or not isinstance(frame["data"], str)):
        raise RecordCodecError("unsupported_record_frame")
    try:
        compressed = base64.b64decode(frame["data"], validate=True)
        check_cancelled(cancel)
        if base64.b64encode(compressed).decode() != frame["data"] or len(compressed) < 18 or compressed[3] != 0:
            raise RecordCodecError("invalid_record_frame")
        decoder = zlib.decompressobj(31)
        logical = decoder.decompress(compressed, frame["decodedBytes"] + 1)
        check_cancelled(cancel)
        if (len(logical) != frame["decodedBytes"] or not decoder.eof or decoder.unused_data or
                decoder.unconsumed_tail or not logical.endswith(b"\n")):
            raise RecordCodecError("record_decode_length_or_checksum")
        logical.decode("utf-8", errors="strict")
        return logical
    except (ValueError, zlib.error, UnicodeError) as error:
        raise RecordCodecError("invalid_record_frame") from error


def physical_lines(stream, *, max_bytes: int | None = None, cancel=None):
    """Bound a physical line before parsing/decompression; preserve partial live tails."""
    limit = MAX_RECORD_BYTES if max_bytes is None else max_bytes
    while True:
        check_cancelled(cancel)
        if cancel is None:
            physical = stream.readline(limit + 1)
        else:
            # Legacy physical records may be large; stop between bounded reads.
            parts, size = [], 0
            while True:
                check_cancelled(cancel)
                block = stream.readline(min(1024 * 1024, limit + 1 - size))
                parts.append(block)
                size += len(block)
                if not block or block.endswith(b'\n') or size > limit:
                    break
            physical = b''.join(parts)
        check_cancelled(cancel)
        if not physical:
            return
        if len(physical) > limit:
            raise RecordCodecError("record_size_or_framing_limit")
        yield physical


def file_identity(path, *, cancel=None):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            check_cancelled(cancel)
            digest.update(chunk); size += len(chunk)
    return size, digest.hexdigest()
