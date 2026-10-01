#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WIN disk-file writer compatible with the stock WIN wdisk format."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional, Union

from win_packet import TIME_HEADER_DATA_OFFSET, parse_time_header

PathLike = Union[str, Path]


class WinFileWriter:
    """Write raw WIN blocks to minute-based WIN disk files.

    The input block may be either:

    * a WIN block with an EOB size at the end (as commonly found in SHM), or
    * a WIN block without the EOB size.

    On disk, the format is::

        4-byte big-endian block size + WIN second block

    where the block size includes those 4 bytes.  If the input has an EOB
    size, the trailing 4-byte EOB field is omitted from the disk file, as in
    the stock ``wdisk`` implementation.
    """

    def __init__(self, output_dir: PathLike):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._fp: Optional[BinaryIO] = None
        self._current_minute: Optional[datetime] = None
        self._current_path: Optional[Path] = None

    @property
    def current_path(self) -> Optional[Path]:
        """Path of the currently open minute file, if any."""
        return self._current_path

    @staticmethod
    def _prepare_disk_block(block: bytes) -> bytes:
        """Convert an input WIN block to the on-disk representation."""
        if len(block) < 8:
            raise ValueError("WIN block is too short")

        declared_size = int.from_bytes(block[:4], byteorder="big")
        if declared_size != len(block):
            raise ValueError(
                f"WIN block size mismatch: header={declared_size}, actual={len(block)}"
            )

        # SHM blocks may contain the EOB size at the end.  wdisk removes it
        # before writing the disk record.
        tail_size = int.from_bytes(block[-4:], byteorder="big")
        if tail_size == declared_size:
            disk_size = declared_size - 4
            if disk_size < 8:
                raise ValueError("invalid WIN block size after removing EOB")
            payload = block[:-4]
            return disk_size.to_bytes(4, byteorder="big") + payload[4:]

        return block

    @staticmethod
    def _get_timestamp(block: bytes) -> datetime:
        time_info = parse_time_header(block, TIME_HEADER_DATA_OFFSET)
        if time_info.timestamp is None:
            raise ValueError("WIN block has no valid timestamp")
        return time_info.timestamp

    def _switch_file(self, timestamp: datetime) -> None:
        minute = timestamp.replace(second=0, microsecond=0)
        if self._current_minute == minute:
            return

        self.close()
        filename = minute.strftime("%y%m%d%H.%M")
        path = self.output_dir / filename
        self._fp = path.open("ab")
        self._current_minute = minute
        self._current_path = path

    def write_block(self, block: bytes) -> None:
        """Append one WIN second block to its minute file."""
        if not isinstance(block, (bytes, bytearray, memoryview)):
            raise TypeError("block must be bytes-like")

        block = bytes(block)
        timestamp = self._get_timestamp(block)
        disk_block = self._prepare_disk_block(block)
        self._switch_file(timestamp)

        assert self._fp is not None
        self._fp.write(disk_block)
        self._fp.flush()

    def flush(self) -> None:
        if self._fp is not None:
            self._fp.flush()

    def close(self) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None
        self._current_minute = None
        self._current_path = None

    def __enter__(self) -> "WinFileWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
