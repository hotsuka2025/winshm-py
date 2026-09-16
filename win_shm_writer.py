#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WIN shared-memory writer compatible with stock WIN tools."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Optional

from win_shm_common import (
    DEFAULT_LOGICAL_FILL_RATIO,
    DEFAULT_SHM_PERMS,
    IPC_CREAT,
    IPC_EXCL,
    UINT64_MASK,
    _ShmHeader,
    get_libc,
    get_shm_segsz,
)


@dataclass(slots=True)
class ShmConfig:
    key: int
    create_size: int = 0
    overwrite: bool = True
    add_eob_size: bool = True
    init_on_start: bool = False
    repair_header: bool = True
    logical_fill_ratio: float = DEFAULT_LOGICAL_FILL_RATIO


class WinShmWriter:
    def __init__(self, shm_config: ShmConfig):
        self.shm_config = shm_config
        self._libc = get_libc()

        self._shmget = self._libc.shmget
        self._shmget.argtypes = [ctypes.c_int, ctypes.c_size_t, ctypes.c_int]
        self._shmget.restype = ctypes.c_int

        self._shmat = self._libc.shmat
        self._shmat.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self._shmat.restype = ctypes.c_void_p

        self._shmdt = self._libc.shmdt
        self._shmdt.argtypes = [ctypes.c_int]
        self._shmdt.restype = ctypes.c_int

        self._addr: Optional[int] = None
        self._shmid: Optional[int] = None
        self._shm_header_p: Optional[ctypes._Pointer[_ShmHeader]] = None
        self._ring_base: Optional[int] = None
        self._payload_max: Optional[int] = None

        self._attach()

    def __enter__(self) -> WinShmWriter:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _attach(self) -> None:
        key = int(self.shm_config.key)
        requested = int(self.shm_config.create_size or 0)
        created_new = False

        if requested > 0:
            shmid = self._shmget(key, requested, IPC_CREAT | IPC_EXCL | DEFAULT_SHM_PERMS)
            if shmid >= 0:
                self._shmid = shmid
                created_new = True
            else:
                self._shmid = self._shmget(key, 0, 0)
        else:
            self._shmid = self._shmget(key, 0, 0)

        if self._shmid is None or self._shmid < 0:
            raise RuntimeError(f"shmget failed (key={hex(key)}). Use --create SIZE if needed.")

        self._addr = self._shmat(self._shmid, None, 0)
        if self._addr is None or self._addr == ctypes.c_void_p(-1).value:
            raise RuntimeError("shmat failed")

        self._shm_header_p = ctypes.cast(self._addr, ctypes.POINTER(_ShmHeader))
        header_size = ctypes.sizeof(_ShmHeader)
        self._ring_base = int(self._addr) + header_size

        seg_size = get_shm_segsz(self._shmid, self._libc)
        if requested and seg_size and seg_size < requested:
            sys.stderr.write(
                f"[win_shm_writer] WARNING: shm key={hex(key)} exists with segsize={seg_size}, "
                f"smaller than requested --create {requested}.\n"
            )

        if seg_size <= header_size:
            raise RuntimeError("invalid shm segment size")

        phys_len = seg_size - header_size
        fill_ratio = max(0.1, min(1.0, float(self.shm_config.logical_fill_ratio)))
        ring_capacity_max = int(phys_len * fill_ratio)

        if ring_capacity_max <= 0:
            raise RuntimeError("invalid shm ring capacity")

        self._payload_max = phys_len
        hdr = self._shm_header_p[0]

        if created_new:
            ctypes.memset(self._ring_base, 0, phys_len)
            hdr.write_pointer, hdr.read_pointer, hdr.sequence_counter, hdr.ring_capacity = 0, 0, 0, ring_capacity_max
            return

        hdr.ring_capacity = ring_capacity_max

        if self.shm_config.repair_header:
            if (
                hdr.write_pointer < 0
                or hdr.write_pointer > self._payload_max
            ):
                sys.stderr.write(
                    f"[win_shm_writer] WARNING: shm header invalid "
                    f"(write_pointer={hdr.write_pointer}); resetting write_pointer.\n"
                )
                hdr.write_pointer, hdr.sequence_counter = 0, 0

            if self.shm_config.init_on_start:
                ctypes.memset(self._ring_base, 0, phys_len)
                hdr.write_pointer, hdr.read_pointer, hdr.sequence_counter, hdr.ring_capacity = (
                    0,
                    0,
                    0,
                    ring_capacity_max,
                )

    def write_block(self, block: bytes) -> None:
        if not all((self._addr, self._shm_header_p, self._ring_base, self._payload_max)):
            raise RuntimeError("writer not attached")

        block_len = len(block)
        if block_len == 0:
            return
        if block_len > self._payload_max:
            raise RuntimeError(
                f"block too large for shm area (block={block_len}, phys={self._payload_max})"
            )

        hdr = self._shm_header_p[0]
        ring_cap = hdr.ring_capacity

        if hdr.write_pointer < 0 or hdr.write_pointer > self._payload_max:
            if self.shm_config.repair_header:
                hdr.write_pointer = 0
            else:
                raise RuntimeError(
                    f"invalid shm pointer write_pointer={hdr.write_pointer}"
                )

        # 1. 物理領域サイズを超過する場合の安全保護リセット
        if hdr.write_pointer + block_len > self._payload_max:
            hdr.write_pointer = 0

        # 2. 現在位置への一括連続書き込み (wintowin.c と同様の処理)
        ctypes.memmove(self._ring_base + hdr.write_pointer, block, block_len)

        # 3. 書き込み後ポインタ算定と pl (ring_capacity) による折返し判定
        next_p = hdr.write_pointer + block_len
        hdr.write_pointer = 0 if next_p > ring_cap else next_p

        # 4. シーケンスカウンター更新
        hdr.sequence_counter = (hdr.sequence_counter + 1) & UINT64_MASK

    def close(self) -> None:
        if self._addr:
            try:
                self._shmdt(self._addr)
            except Exception:
                pass
        self._addr = self._shmid = self._shm_header_p = self._ring_base = self._payload_max = None