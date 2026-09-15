#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WIN format packet encoding and decoding utilities."""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

# Header Constants
TIME_HEADER_DATA_OFFSET = 4
TIME_HEADER_SIZE_14 = 14
TIME_HEADER_SIZE_10 = 10

CHANNEL_HEADER_LEN_NORMAL = 4
CHANNEL_HEADER_LEN_EXTENDED = 5
EXTENDED_HEADER_MASK = 0x80


class CompressionType(IntEnum):
    DIFF_4BIT = 0
    DIFF_8BIT = 1
    DIFF_16BIT = 2
    DIFF_24BIT = 3
    DIFF_32BIT = 4
    RAW_32BIT = 5


SAMPLE_BYTE_SIZE: Dict[CompressionType, Union[float, int]] = {
    CompressionType.DIFF_4BIT: 0.5,
    CompressionType.DIFF_8BIT: 1,
    CompressionType.DIFF_16BIT: 2,
    CompressionType.DIFF_24BIT: 3,
    CompressionType.DIFF_32BIT: 4,
    CompressionType.RAW_32BIT: 4,
}

_B2D_TABLE = {
    i: (i >> 4) * 10 + (i & 0x0F)
    for i in range(256)
    if (i >> 4) < 10 and (i & 0x0F) < 10
}


@dataclass(frozen=True)
class TimeHeaderInfo:
    timestamp: Optional[datetime]
    time_of_week: Optional[int]
    header_size: Optional[int]


@dataclass(frozen=True)
class ParsedChannelBlock:
    channel_code: int
    sample_rate: int
    compression_type: CompressionType
    samples: np.ndarray


# Type Alias for (channel_id, sample_rate, [samples])
ChannelDataTuple = Tuple[int, int, List[int]]


# ==========================================
# Encoding (Packet Construction) Logic
# ==========================================

def encode_bcd6(timestamp: datetime) -> bytes:
    """Convert datetime to 6-byte BCD WIN timestamp."""
    return bytes.fromhex(timestamp.strftime("%y%m%d%H%M%S"))


def _fits_ss(diffs: List[int], sample_size: int) -> bool:
    limits = {
        0: (-8, 7),
        1: (-128, 127),
        2: (-32768, 32767),
        3: (-8388608, 8388607),
        4: (-2147483648, 2147483647),
        5: (float("-inf"), float("inf")),
    }
    minv, maxv = limits.get(sample_size, (0, 0))
    return all(minv <= d <= maxv for d in diffs)


def choose_ss(samples: List[int], sample_rate: int, max_sample_size: int = 4) -> int:
    """Choose optimal sample size compression mode automatically."""
    if sample_rate <= 1:
        return 0
    samples = (samples + [0] * max(0, sample_rate - len(samples)))[:sample_rate]
    diffs = [samples[i] - samples[i - 1] for i in range(1, sample_rate)]

    for sample_size in range(min(max_sample_size, 4) + 1):
        if _fits_ss(diffs, sample_size):
            return sample_size
    return min(max_sample_size, 4)


def encode_channel_block(channel_id: int, sample_rate: int, samples: List[int], sample_size: int = 3) -> bytes:
    """Encode single channel sample block into WIN binary format."""
    channel_id &= 0xFFFF
    if sample_rate <= 0:
        raise ValueError("sample_rate must be >= 1")
    if sample_size not in (0, 1, 2, 3, 4, 5):
        raise ValueError("invalid sample_size")

    samples = (samples + [0] * max(0, sample_rate - len(samples)))[:sample_rate]

    if sample_rate <= 0x0FFF:
        flags_byte = ((sample_size & 0x7) << 4) | ((sample_rate >> 8) & 0x0F)
        header = bytes([(channel_id >> 8) & 0xFF, channel_id & 0xFF, flags_byte, sample_rate & 0xFF])
    elif sample_rate <= 0x0FFFFF:
        flags_byte = 0x80 | ((sample_size & 0x7) << 4) | ((sample_rate >> 16) & 0x0F)
        header = bytes([(channel_id >> 8) & 0xFF, channel_id & 0xFF, flags_byte, (sample_rate >> 8) & 0xFF, sample_rate & 0xFF])
    else:
        raise ValueError("sample_rate too large for WIN header")

    out = bytearray(header)
    out += struct.pack(">i", samples[0])

    if sample_rate == 1:
        return bytes(out)

    if sample_size == 5:
        return bytes(out + b"".join(struct.pack(">i", s) for s in samples[1:]))

    diffs = [samples[i] - samples[i - 1] for i in range(1, sample_rate)]
    if not _fits_ss(diffs, sample_size):
        raise ValueError(f"diff out of range for sample_size={sample_size}")

    if sample_size == 0:
        for i in range(0, len(diffs), 2):
            d1 = diffs[i] & 0x0F
            d2 = (diffs[i + 1] & 0x0F) if i + 1 < len(diffs) else 0
            out.append((d1 << 4) | d2)
        return bytes(out)

    ss_widths = {1: 1, 2: 2, 3: 3, 4: 4}
    width = ss_widths[sample_size]
    out += b"".join(d.to_bytes(width, "big", signed=True) for d in diffs)
    return bytes(out)


def build_block(
    timestamp: datetime,
    channels: List[ChannelDataTuple],
    add_eob_size: bool = True,
    ss_mode: str = "auto",
    with_tow: bool = False,
    time_offset: int = 0,
    ss_strict: bool = False,
) -> bytes:
    """Build a complete WIN format block byte payload."""
    body = bytearray(encode_bcd6(timestamp))

    for channel_id, sample_rate, samples in channels:
        enc_sample_size = choose_ss(samples, sample_rate) if ss_mode == "auto" else int(ss_mode)
        try:
            body += encode_channel_block(channel_id, sample_rate, samples, sample_size=enc_sample_size)
        except ValueError:
            if ss_mode != "auto" and not ss_strict and enc_sample_size < 4:
                body += encode_channel_block(channel_id, sample_rate, samples, sample_size=4)
            else:
                raise

    extra = 4 if with_tow else 0
    block_size = 4 + extra + len(body) + (4 if add_eob_size else 0)

    out = bytearray(struct.pack(">I", block_size))
    if with_tow:
        tow = int(time.mktime(timestamp.timetuple())) - time_offset
        out += struct.pack(">I", tow & 0xFFFFFFFF)
    out += body
    if add_eob_size:
        out += struct.pack(">I", block_size)

    return bytes(out)


# ==========================================
# Decoding (Packet Parsing) Logic
# ==========================================

def decode_bcd_time(bcd_bytes: Union[bytes, memoryview]) -> Optional[datetime]:
    """Parse 6-byte BCD array into standard datetime."""
    parts = [_B2D_TABLE.get(b) for b in bcd_bytes]
    if any(p is None for p in parts):
        return None
    year, month, day, hour, minute, second = parts
    if not (1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 60):
        return None
    year += 2000 if year < 80 else 1900
    try:
        return datetime(year, month, day, hour, minute, second)
    except Exception:
        return None


def parse_time_header(block_bytes: Union[bytes, memoryview], data_offset: int = TIME_HEADER_DATA_OFFSET) -> TimeHeaderInfo:
    """Parse WIN time header block (10-byte or 14-byte format)."""
    if data_offset >= len(block_bytes):
        return TimeHeaderInfo(timestamp=None, time_of_week=None, header_size=None)

    first_byte = block_bytes[data_offset]
    prefer_14_byte_header = 0x38 < first_byte < 0x90

    def _parse_14byte_header() -> Optional[TimeHeaderInfo]:
        if data_offset + 10 > len(block_bytes):
            return None
        time_of_week = int.from_bytes(block_bytes[data_offset:data_offset + 4], byteorder='big')
        timestamp = decode_bcd_time(block_bytes[data_offset + 4:data_offset + 10])
        if timestamp is None:
            return None
        return TimeHeaderInfo(timestamp=timestamp, time_of_week=time_of_week, header_size=TIME_HEADER_SIZE_14)

    def _parse_10byte_header() -> Optional[TimeHeaderInfo]:
        if data_offset + 6 > len(block_bytes):
            return None
        timestamp = decode_bcd_time(block_bytes[data_offset:data_offset + 6])
        if timestamp is None:
            return None
        return TimeHeaderInfo(timestamp=timestamp, time_of_week=None, header_size=TIME_HEADER_SIZE_10)

    if prefer_14_byte_header:
        res = _parse_14byte_header() or _parse_10byte_header()
    else:
        res = _parse_10byte_header() or _parse_14byte_header()

    return res or TimeHeaderInfo(timestamp=None, time_of_week=None, header_size=None)


def get_channel_block_size(header_bytes: Union[bytes, memoryview]) -> int:
    """Compute the total byte length of a channel data block from header."""
    if len(header_bytes) < 5:
        return 0

    is_extended = bool(header_bytes[2] & EXTENDED_HEADER_MASK)
    channel_header_length = CHANNEL_HEADER_LEN_EXTENDED if is_extended else CHANNEL_HEADER_LEN_NORMAL

    if is_extended:
        sample_rate_bytes = bytes([header_bytes[2] & 0x0F, header_bytes[3], header_bytes[4]])
    else:
        sample_rate_bytes = bytes([header_bytes[2] & 0x0F, header_bytes[3]])

    sample_rate = int.from_bytes(sample_rate_bytes, byteorder='big')

    try:
        compression_type = CompressionType((header_bytes[2] >> 4) & 0x07)
    except ValueError:
        return 0

    sample_size = SAMPLE_BYTE_SIZE.get(compression_type)
    if sample_size is None or sample_rate == 0:
        return 0

    if compression_type == CompressionType.DIFF_4BIT:
        diff_bytes_count = (sample_rate - 1 + 1) // 2 if sample_rate > 1 else 0
        block_length = int(channel_header_length + 4 + diff_bytes_count)
    else:
        block_length = int(channel_header_length + 4 + (sample_rate - 1) * sample_size)

    return block_length


def parse_channel_block(channel_bytes: Union[bytes, memoryview]) -> ParsedChannelBlock:
    """Decode compressed WIN channel byte block into array of samples."""
    is_extended = bool(channel_bytes[2] & EXTENDED_HEADER_MASK)
    header_length = CHANNEL_HEADER_LEN_EXTENDED if is_extended else CHANNEL_HEADER_LEN_NORMAL

    channel_code = int.from_bytes(channel_bytes[0:2], byteorder='big')

    if is_extended:
        sample_rate_bytes = bytes([channel_bytes[2] & 0x0F, channel_bytes[3], channel_bytes[4]])
    else:
        sample_rate_bytes = bytes([channel_bytes[2] & 0x0F, channel_bytes[3]])

    sample_rate = int.from_bytes(sample_rate_bytes, byteorder='big')
    compression_type = CompressionType((channel_bytes[2] >> 4) & 0x07)

    ptr = header_length
    if ptr + 4 > len(channel_bytes) or sample_rate == 0:
        return ParsedChannelBlock(
            channel_code=channel_code,
            sample_rate=sample_rate,
            compression_type=compression_type,
            samples=np.array([], dtype=np.int32),
        )

    first_sample = int.from_bytes(channel_bytes[ptr:ptr + 4], byteorder='big', signed=True)

    if sample_rate == 1:
        return ParsedChannelBlock(
            channel_code=channel_code,
            sample_rate=sample_rate,
            compression_type=compression_type,
            samples=np.array([first_sample], dtype=np.int32),
        )

    ptr += 4
    needed_diffs = sample_rate - 1

    if compression_type == CompressionType.DIFF_4BIT:
        diff_bytes_count = (needed_diffs + 1) // 2
        raw_bytes = np.frombuffer(channel_bytes[ptr:ptr + diff_bytes_count], dtype=np.uint8)
        nibbles = np.empty(len(raw_bytes) * 2, dtype=np.int8)
        nibbles[0::2] = raw_bytes >> 4
        nibbles[1::2] = raw_bytes & 0x0F
        diffs = nibbles[:needed_diffs].astype(np.int32)
        diffs = np.where(diffs >= 8, diffs - 16, diffs)

        samples = np.empty(sample_rate, dtype=np.int32)
        samples[0] = first_sample
        samples[1:] = first_sample + np.cumsum(diffs)

    elif compression_type == CompressionType.DIFF_8BIT:
        diffs = np.frombuffer(channel_bytes[ptr:ptr + needed_diffs], dtype='>i1').astype(np.int32)
        samples = np.empty(sample_rate, dtype=np.int32)
        samples[0] = first_sample
        samples[1:] = first_sample + np.cumsum(diffs)

    elif compression_type == CompressionType.DIFF_16BIT:
        diffs = np.frombuffer(channel_bytes[ptr:ptr + needed_diffs * 2], dtype='>i2').astype(np.int32)
        samples = np.empty(sample_rate, dtype=np.int32)
        samples[0] = first_sample
        samples[1:] = first_sample + np.cumsum(diffs)

    elif compression_type == CompressionType.DIFF_24BIT:
        raw_bytes = np.frombuffer(channel_bytes[ptr:ptr + needed_diffs * 3], dtype=np.uint8).reshape(-1, 3)
        diffs = (raw_bytes[:, 0].astype(np.int32) << 16) | (raw_bytes[:, 1].astype(np.int32) << 8) | raw_bytes[:, 2].astype(np.int32)
        diffs = np.where(diffs >= 0x800000, diffs - 0x1000000, diffs)
        samples = np.empty(sample_rate, dtype=np.int32)
        samples[0] = first_sample
        samples[1:] = first_sample + np.cumsum(diffs)

    elif compression_type == CompressionType.DIFF_32BIT:
        diffs = np.frombuffer(channel_bytes[ptr:ptr + needed_diffs * 4], dtype='>i4').astype(np.int32)
        samples = np.empty(sample_rate, dtype=np.int32)
        samples[0] = first_sample
        samples[1:] = first_sample + np.cumsum(diffs)

    elif compression_type == CompressionType.RAW_32BIT:
        samples = np.frombuffer(channel_bytes[ptr - 4:ptr - 4 + sample_rate * 4], dtype='>i4').astype(np.int32)

    else:
        samples = np.array([first_sample], dtype=np.int32)

    return ParsedChannelBlock(
        channel_code=channel_code,
        sample_rate=sample_rate,
        compression_type=compression_type,
        samples=samples,
    )