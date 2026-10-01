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
        self._shmget.argtypes = [
            ctypes.c_int,
            ctypes.c_size_t,
            ctypes.c_int,
        ]
        self._shmget.restype = ctypes.c_int

        self._shmat = self._libc.shmat
        self._shmat.argtypes = [
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        self._shmat.restype = ctypes.c_void_p

        self._shmdt = self._libc.shmdt
        self._shmdt.argtypes = [ctypes.c_void_p]
        self._shmdt.restype = ctypes.c_int

        self._addr: Optional[int] = None
        self._shmid: Optional[int] = None
        self._shm_header_p: Optional[ctypes._Pointer[_ShmHeader]] = None
        self._ring_base: Optional[int] = None

        # 物理的に存在するデータ領域の最大サイズ。
        #
        # WIN の pl (= ring_capacity) より大きい。
        # native WIN は pl を越えても、1ブロックを連続して書き、
        # その後 p を 0 に戻すことがある。
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
            shmid = self._shmget(
                key,
                requested,
                IPC_CREAT | IPC_EXCL | DEFAULT_SHM_PERMS,
            )

            if shmid >= 0:
                self._shmid = shmid
                created_new = True
            else:
                self._shmid = self._shmget(key, 0, 0)
        else:
            self._shmid = self._shmget(key, 0, 0)

        if self._shmid is None or self._shmid < 0:
            raise RuntimeError(
                f"shmget failed (key={hex(key)}). "
                "Use --create SIZE if needed."
            )

        self._addr = self._shmat(self._shmid, None, 0)

        if self._addr is None or self._addr == ctypes.c_void_p(-1).value:
            raise RuntimeError("shmat failed")

        self._shm_header_p = ctypes.cast(
            self._addr,
            ctypes.POINTER(_ShmHeader),
        )

        header_size = ctypes.sizeof(_ShmHeader)
        self._ring_base = int(self._addr) + header_size

        seg_size = get_shm_segsz(self._shmid, self._libc)

        if requested and seg_size and seg_size < requested:
            sys.stderr.write(
                f"[win_shm_writer] WARNING: "
                f"shm key={hex(key)} exists with segsize={seg_size}, "
                f"smaller than requested --create {requested}.\n"
            )

        if seg_size <= header_size:
            raise RuntimeError("invalid shm segment size")

        # 物理的なpayload領域。
        #
        # native WIN の shm_init() では、この領域の一部を
        # logical ring capacity (pl) の先に残している。
        phys_len = seg_size - header_size

        fill_ratio = max(
            0.1,
            min(1.0, float(self.shm_config.logical_fill_ratio)),
        )

        ring_capacity = int(phys_len * fill_ratio)

        if ring_capacity <= 0:
            raise RuntimeError("invalid shm ring capacity")

        # pl == physical payload size では、
        # plを越えて1ブロックを書けなくなるので、
        # native WIN と同じく安全余裕を必ず残す。
        if ring_capacity >= phys_len:
            raise RuntimeError(
                "invalid shm layout: ring capacity must be "
                "smaller than physical payload size "
                f"(ring_capacity={ring_capacity}, "
                f"payload={phys_len})"
            )

        self._payload_max = phys_len

        hdr = self._shm_header_p[0]

        if created_new:
            ctypes.memset(
                self._ring_base,
                0,
                phys_len,
            )

            hdr.write_pointer = 0
            hdr.read_pointer = 0
            hdr.sequence_counter = 0
            hdr.ring_capacity = ring_capacity

            return

        # 既存SHMの場合も、このPython実装が管理する
        # logical capacityを設定する。
        hdr.ring_capacity = ring_capacity

        if self.shm_config.repair_header:

            # native WIN の p は pl を越えた状態で保持されない。
            # p == pl は許容される。
            if (
                hdr.write_pointer < 0
                or hdr.write_pointer > ring_capacity
            ):
                sys.stderr.write(
                    "[win_shm_writer] WARNING: "
                    "shm header invalid "
                    f"(write_pointer={hdr.write_pointer}, "
                    f"ring_capacity={ring_capacity}); "
                    "resetting write_pointer.\n"
                )

                hdr.write_pointer = 0
                hdr.sequence_counter = 0

            if self.shm_config.init_on_start:
                ctypes.memset(
                    self._ring_base,
                    0,
                    phys_len,
                )

                hdr.write_pointer = 0
                hdr.read_pointer = 0
                hdr.sequence_counter = 0
                hdr.ring_capacity = ring_capacity

    def write_block(self, block: bytes) -> None:
        """
        Write one complete WIN block to shared memory.

        Important:
            A WIN block is never split at ring_capacity.

        native WIN semantics are:

            memcpy(p, block, block_len);

            p += block_len;

            if (p > pl)
                p = 0;

        Therefore a block may physically extend beyond pl,
        provided that it still fits inside the physical SHM
        payload area.
        """

        if not all(
            (
                self._addr,
                self._shm_header_p,
                self._ring_base,
                self._payload_max,
            )
        ):
            raise RuntimeError("writer not attached")

        block_len = len(block)

        if block_len == 0:
            return

        payload_max = int(self._payload_max)

        # ブロック全体が物理payloadに収まる必要がある。
        if block_len > payload_max:
            raise RuntimeError(
                "WIN block is larger than physical SHM payload: "
                f"block_len={block_len}, "
                f"payload_max={payload_max}"
            )

        hdr = self._shm_header_p[0]

        ring_capacity = int(hdr.ring_capacity)
        write_pointer = int(hdr.write_pointer)

        # ------------------------------------------------------------
        # Header sanity checks
        # ------------------------------------------------------------

        if ring_capacity <= 0:
            raise RuntimeError(
                f"invalid shm ring capacity: {ring_capacity}"
            )

        if ring_capacity > payload_max:
            raise RuntimeError(
                "invalid shm layout: "
                f"ring_capacity={ring_capacity} "
                f"> payload_max={payload_max}"
            )

        # native WIN の p は logical boundary (pl) を越えない。
        # p == pl は有効。
        if write_pointer < 0 or write_pointer > ring_capacity:
            if self.shm_config.repair_header:
                sys.stderr.write(
                    "[win_shm_writer] WARNING: "
                    f"invalid write_pointer={write_pointer}; "
                    "resetting to 0.\n"
                )

                write_pointer = 0
                hdr.write_pointer = 0
            else:
                raise RuntimeError(
                    "invalid shm pointer: "
                    f"write_pointer={write_pointer}"
                )

        # ------------------------------------------------------------
        # Physical boundary check
        # ------------------------------------------------------------

        physical_end = write_pointer + block_len

        # ここで0へリセットしてはいけない。
        #
        # native WINでは
        #
        #     memcpy(p, block, block_len)
        #
        #     if (p > pl)
        #         p = d;
        #
        # なので、plを越えること自体は正常。
        #
        # ただし、物理payloadの末端を越える場合は、
        # native WINと同じ一括書き込みができないため、
        # データ破壊を避けるためエラーにする。
        if physical_end > payload_max:
            raise RuntimeError(
                "WIN block would exceed physical SHM payload: "
                f"write_pointer={write_pointer}, "
                f"block_len={block_len}, "
                f"physical_end={physical_end}, "
                f"payload_max={payload_max}, "
                f"ring_capacity={ring_capacity}"
            )

        # ------------------------------------------------------------
        # Complete block write
        # ------------------------------------------------------------

        # ブロックを分割せず、一度に連続して書く。
        ctypes.memmove(
            self._ring_base + write_pointer,
            block,
            block_len,
        )

        # ------------------------------------------------------------
        # Native WIN compatible pointer update
        # ------------------------------------------------------------
        hdr.read_pointer = write_pointer         # UPDATE: native WIN semantics
        next_pointer = write_pointer + block_len

        # native WIN:
        #
        #     if (ptw > shm_out->d + shm_out->pl)
        #         ptw = shm_out->d;
        #
        # したがって == pl はそのまま保持する。
        if next_pointer > ring_capacity:
            next_pointer = 0

        hdr.write_pointer = next_pointer

        # ------------------------------------------------------------
        # Sequence counter
        # ------------------------------------------------------------

        hdr.sequence_counter = (
            hdr.sequence_counter + 1
        ) & UINT64_MASK

    def close(self) -> None:
        if self._addr:
            try:
                self._shmdt(self._addr)
            except Exception:
                pass

        self._addr = None
        self._shmid = None
        self._shm_header_p = None
        self._ring_base = None
        self._payload_max = None