#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WIN System-V shared memory reader utilities."""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, List, Optional, Set

import numpy as np

from win_packet import (
    TIME_HEADER_DATA_OFFSET,
    get_channel_block_size,
    parse_channel_block,
    parse_time_header,
)
from win_shm_common import (
    INVALID_READ_POINTERS,
    SHM_RDONLY,
    UINT64_MASK,
    _ShmHeader,
    get_libc,
    get_shm_segsz,
)

DEFAULT_MAX_PAYLOAD = 64 * 1024 * 1024
MAX_INVALID_STREAK = 2000
MAX_WAIT_LOOPS = 50
UNREAD_DROP_RATIO = 0.8


@dataclass(frozen=True)
class ChannelData:
    channel_id: str
    sample_rate: int
    samples: np.ndarray


@dataclass(frozen=True)
class WinBlock:
    timestamp: datetime
    sub_msec: int
    channels: List[ChannelData]


def physical_read(base_addr: int, payload_max: int, pos: int, n: int) -> bytes:
    """Read one contiguous WIN block from the physical SHM payload.

    ``ring_capacity`` (native WIN's ``pl``) is a logical boundary for the
    *next block pointer*, not a boundary at which a block is split.
    A complete variable-length WIN block may extend beyond ``pl``.
    """
    if n <= 0 or payload_max <= 0:
        return b""

    payload_max = int(payload_max)
    pos = int(pos)
    n = int(n)

    if pos < 0 or pos > payload_max:
        raise ValueError("invalid physical read position")

    if n > payload_max - pos:
        raise ValueError("physical read exceeds SHM payload")

    return ctypes.string_at(base_addr + pos, n)


@dataclass
class WinShmReader:
    key: int
    peek: bool = True
    sleep: float = 0.1
    drop_if_behind: bool = True
    target_channels: Optional[Set[int]] = None

    def __post_init__(self):
        self.key = int(self.key)
        if self.target_channels is not None:
            self.target_channels = set(self.target_channels)

        self._libc = get_libc()

        self._shmget = self._libc.shmget
        self._shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
        self._shmget.restype = ctypes.c_int

        self._shmat = self._libc.shmat
        self._shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self._shmat.restype = ctypes.c_void_p

        self._shmdt = self._libc.shmdt
        self._shmdt.argtypes = [ctypes.c_void_p]
        self._shmdt.restype = ctypes.c_int

        self._addr = None
        self._shmid = None
        self._shm_header_p = None
        self._ring_base = None
        self._seg_size = None
        self._read_offset_local = None

    def __enter__(self):
        shmid = self._shmget(self.key, 0, 0)
        if shmid < 0:
            raise RuntimeError(f'shared memory key not found: {self.key}')
        self._shmid = shmid

        self._seg_size = int(get_shm_segsz(shmid, self._libc))
        if self._seg_size <= 0:
            self._seg_size = 0

        addr = self._shmat(shmid, None, SHM_RDONLY)
        if addr is None or addr == ctypes.c_void_p(-1).value:
            raise RuntimeError('shmat failed')
        self._addr = addr

        shm_struct_size = ctypes.sizeof(_ShmHeader)
        self._shm_header_p = ctypes.cast(addr, ctypes.POINTER(_ShmHeader))
        self._ring_base = addr + shm_struct_size

        self._payload_max = max(0, int(self._seg_size) - int(shm_struct_size)) if self._seg_size else DEFAULT_MAX_PAYLOAD
        
        # Read-Only対応: 共有メモリ上のread_pointer(0)は参照せず、最新のwrite_pointer位置から開始
        ring_cap = int(self._shm_header_p[0].ring_capacity)
        if ring_cap > 0:
            self._read_offset_local = int(self._shm_header_p[0].write_pointer) % ring_cap
        else:
            self._read_offset_local = 0
            
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._addr is not None and self._addr != ctypes.c_void_p(-1).value:
                self._shmdt(self._addr)
        finally:
            self._addr = None

    def _reset_sync(self, ring_capacity: int):
        write_offset = int(self._shm_header_p[0].write_pointer)

        if ring_capacity > 0:
            write_offset %= ring_capacity
        else:
            write_offset = 0

        # Read-Only対応: 共有メモリのread_pointerではなく、
        # 最新のwrite_pointerへ同期する。
        self._read_offset_local = write_offset
        self._saved_sequence = int(self._shm_header_p[0].sequence_counter)
        self._eobsize_mode = None
        self._eobsize_count = 0
        self._invalid_streak = 0

        # 同期直後のデータを推測してモード判定しない。
        # 次の実ブロックを通常の読出し経路で取得する。

    def _advance(self, current_read_offset: int, ring_capacity: int, step_bytes: int):
        # Native WIN semantics:
        #
        #     p += block_len;
        #     if (p > pl)
        #         p = 0;
        #
        # The block itself is never split at pl.
        next_offset = int(current_read_offset) + int(step_bytes)

        if next_offset > int(ring_capacity):
            next_offset = 0

        self._read_offset_local = next_offset

    def _iter_raw_blocks(self) -> Iterator[bytes]:
        """共有メモリのリングバッファから生WINブロック(bytes)を安全に取り出す内部ジェネレータ。"""
        if self._addr is None:
            raise RuntimeError('WinShmReader must be used as a context manager')

        if not hasattr(self, "_saved_sequence"):
            self._saved_sequence = int(self._shm_header_p[0].sequence_counter)
        if not hasattr(self, "_eobsize_mode"):
            self._eobsize_mode = None
        if not hasattr(self, "_eobsize_count"):
            self._eobsize_count = 0
        if not hasattr(self, "_invalid_streak"):
            self._invalid_streak = 0

        wait_loops = 0

        while True:
            raw_write_pointer = int(self._shm_header_p[0].write_pointer)
            ring_capacity = int(self._shm_header_p[0].ring_capacity)

            if ring_capacity <= 0 or ring_capacity > getattr(self, "_payload_max", 0):
                time.sleep(self.sleep)
                continue

            # write_pointer is a logical WIN pointer.  It is allowed to
            # become zero after a block has physically extended beyond
            # ring_capacity (pl).
            if raw_write_pointer < 0 or raw_write_pointer > ring_capacity:
                self._reset_sync(ring_capacity)
                continue

            write_offset = raw_write_pointer

            current_sequence = int(self._shm_header_p[0].sequence_counter)
            sequence_delta = (
                current_sequence - int(self._saved_sequence)
            ) & UINT64_MASK

            if not (0 <= sequence_delta < 1_000_000):
                self._reset_sync(ring_capacity)
                continue

            raw_read_pointer = int(self._shm_header_p[0].read_pointer)
            if raw_read_pointer in INVALID_READ_POINTERS:
                time.sleep(0.2)
                continue

            # Read-Only対応: 常にローカル変数で保持している読み出し位置を使用
            read_offset = int(self._read_offset_local)

            if read_offset < 0 or read_offset > ring_capacity:
                self._reset_sync(ring_capacity)
                continue

            # Calculate the physically available byte span.
            #
            # If write_offset < read_offset, the writer has crossed pl:
            #
            #     [read_offset ... payload_end] [0 ... write_offset]
            #
            # The first part is NOT wrapped at ring_capacity.
            if read_offset == write_offset:
                if sequence_delta == 0:
                    time.sleep(self.sleep)
                    continue

                # The pointer is identical but the sequence changed.
                # This can only be treated as non-empty after a completed
                # write; the physical tail is the possible unread region.
                unread_bytes = getattr(self, "_payload_max", 0) - read_offset

            elif write_offset > read_offset:
                unread_bytes = write_offset - read_offset

            else:
                unread_bytes = (
                    getattr(self, "_payload_max", 0)
                    - read_offset
                    + write_offset
                )

            if self.drop_if_behind and unread_bytes > int(ring_capacity * UNREAD_DROP_RATIO):
                self._read_offset_local = write_offset
                self._saved_sequence = current_sequence
                continue

            payload_max = int(getattr(self, "_payload_max", 0))

            try:
                # Read the WIN size field contiguously.  Do not use
                # ring_capacity as a physical wrap boundary.
                size_a = int.from_bytes(
                    physical_read(
                        self._ring_base,
                        payload_max,
                        read_offset,
                        4,
                    ),
                    byteorder='big',
                )

                # WIN blocks are variable length.  The physical payload,
                # not ring_capacity, is the upper bound.
                if not (10 <= size_a <= payload_max - read_offset):
                    raise ValueError("invalid block size")

                size_b = int.from_bytes(
                    physical_read(
                        self._ring_base,
                        payload_max,
                        read_offset,
                        4,
                    ),
                    byteorder='big',
                )

                if size_b != size_a:
                    self._reset_sync(ring_capacity)
                    continue

                block_size = size_a

            except Exception:
                self._advance(read_offset, ring_capacity, 1)
                self._invalid_streak += 1

                if self._invalid_streak >= MAX_INVALID_STREAK:
                    self._reset_sync(ring_capacity)

                continue

            # The complete block must already have been written.
            if block_size > unread_bytes:
                wait_loops += 1

                if wait_loops >= MAX_WAIT_LOOPS:
                    self._advance(read_offset, ring_capacity, 1)
                    self._invalid_streak += 1
                    wait_loops = 0
                else:
                    time.sleep(0.001)

                continue

            wait_loops = 0

            try:
                # Read the complete variable-length block in one contiguous
                # physical read.  A block may cross pl, but never the actual
                # physical end of the SHM payload.
                block_bytes = bytes(
                    physical_read(
                        self._ring_base,
                        payload_max,
                        read_offset,
                        block_size,
                    )
                )

                if len(block_bytes) != block_size:
                    raise ValueError("short block read")

            except Exception:
                self._advance(read_offset, ring_capacity, 1)
                self._invalid_streak += 1
                continue

            try:
                tail_size = int.from_bytes(block_bytes[-4:], byteorder='big') if block_size >= 4 else 0
                tail_matches = (tail_size == block_size)
            except Exception:
                tail_matches = False

            if self._eobsize_mode is None:
                self._eobsize_mode = tail_matches
                self._eobsize_count = 1 if tail_matches else 0
            else:
                if self._eobsize_mode:
                    if tail_matches:
                        self._eobsize_count = (self._eobsize_count + 1) & 0xFFFFFFFF
                        if self._eobsize_count == 0:
                            self._eobsize_count = 1
                    else:
                        self._eobsize_count = 0
                        self._reset_sync(ring_capacity)
                        continue
                else:
                    if tail_matches:
                        self._eobsize_count += 1
                        if self._eobsize_count > 3:
                            self._reset_sync(ring_capacity)
                            continue
                    else:
                        self._eobsize_count = 0

            # 生ブロック切り出し成功時：読出し位置を進めて生バイナリを返却
            self._advance(read_offset, ring_capacity, block_size)
            self._saved_sequence = current_sequence
            self._invalid_streak = 0

            yield block_bytes

    def iter_raw_blocks(self) -> Iterator[bytes]:
        """WINパースを行わず、共有メモリ上の生のWINブロック(bytes)を順次返却する。"""
        yield from self._iter_raw_blocks()

    def iter_blocks(self) -> Iterator[WinBlock]:
        """生WINブロックを取り出し、時刻ヘッダーおよびチャネルデータを解読して WinBlock オブジェクトとして返却する。"""
        for block_bytes in self._iter_raw_blocks():
            block_size = len(block_bytes)

            time_info = parse_time_header(block_bytes, TIME_HEADER_DATA_OFFSET)
            if time_info.timestamp is None or time_info.header_size is None:
                continue

            sub_msec = (time_info.time_of_week & 0xFF) if time_info.time_of_week is not None else 0

            decoded_channels: List[ChannelData] = []
            channel_offset = time_info.header_size
            while channel_offset < block_size:
                if channel_offset + 5 > block_size:
                    break
                block_length = get_channel_block_size(block_bytes[channel_offset:])
                if block_length == 0 or channel_offset + block_length > block_size:
                    break
                
                ch_data = block_bytes[channel_offset: channel_offset + block_length]

                # 高速チャンネルフィルタリング
                ch_code = int.from_bytes(ch_data[0:2], byteorder='big')
                if self.target_channels is not None and ch_code not in self.target_channels:
                    channel_offset += block_length
                    continue

                parsed_channel = parse_channel_block(ch_data)
                channel_hex = f"{parsed_channel.channel_code:04X}"
                decoded_channels.append(
                    ChannelData(
                        channel_id=channel_hex,
                        sample_rate=parsed_channel.sample_rate,
                        samples=parsed_channel.samples,
                    )
                )
                channel_offset += block_length

            if (block_size - channel_offset) > 16:
                continue

            # フィルタリングの結果、対象チャンネルが存在する場合のみ yield
            if decoded_channels:
                yield WinBlock(
                    timestamp=time_info.timestamp,
                    sub_msec=int(sub_msec),
                    channels=decoded_channels,
                )