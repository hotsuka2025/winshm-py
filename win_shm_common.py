#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WIN shared-memory common structures, constants, and utilities."""

from __future__ import annotations

import ctypes
import ctypes.util
from typing import Optional

# System V Shared Memory IPC Constants
IPC_CREAT = 0o1000
IPC_EXCL = 0o2000
IPC_STAT = 2
SHM_RDONLY = 0x1000
DEFAULT_SHM_PERMS = 0o666

# Header & Buffer Bitmasks / Special Values
UINT64_MASK = 0xFFFFFFFFFFFFFFFF
INVALID_READ_POINTERS = (0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF)
DEFAULT_LOGICAL_FILL_RATIO = 0.9


class _ShmHeader(ctypes.Structure):
    """WIN shared-memory ring buffer header layout."""

    _fields_ = [
        ("write_pointer", ctypes.c_ulong),    # logical write offset
        ("ring_capacity", ctypes.c_ulong),    # last valid logical offset
        ("read_pointer", ctypes.c_ulong),     # logical read offset
        ("sequence_counter", ctypes.c_ulong), # packet counter
    ]


def get_libc() -> ctypes.CDLL:
    """Load system C library."""
    libc_name = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(libc_name)


def get_shm_segsz(shmid: int, libc: Optional[ctypes.CDLL] = None) -> int:
    """Return System V shared-memory segment size in bytes."""
    if shmid < 0:
        return 0

    # 1. /proc/sysvipc/shm の解析
    try:
        with open("/proc/sysvipc/shm", "r", encoding="ascii", errors="ignore") as f:
            lines = f.read().splitlines()
        if lines:
            header = lines[0].split()
            shmid_idx = next((i for i, c in enumerate(header) if c.lower() in ("shmid", "id")), 1)
            segsz_idx = next((i for i, c in enumerate(header) if c.lower() in ("segsz", "size", "bytes")), 3)

            for row in lines[1:]:
                cols = row.split()
                if len(cols) > max(shmid_idx, segsz_idx) and int(cols[shmid_idx]) == int(shmid):
                    val = int(cols[segsz_idx])
                    if 4096 <= val <= (1 << 40):
                        return val
    except Exception:
        pass

    # 2. shmctl によるフォールバック
    if libc is not None:
        shmctl = libc.shmctl
        shmctl.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        shmctl.restype = ctypes.c_int

        buf = ctypes.create_string_buffer(256)
        if shmctl(shmid, IPC_STAT, ctypes.byref(buf)) == 0:
            candidates = []
            for off in (48, 56, 64):
                try:
                    val = ctypes.c_size_t.from_buffer(buf, off).value
                    if 4096 <= int(val) <= (1 << 40):
                        candidates.append(int(val))
                except (ValueError, OSError):
                    continue
            if candidates:
                return max(candidates)

    return 0