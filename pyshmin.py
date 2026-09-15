#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read "1 line per channel" text stream from stdin and write WIN blocks into shm."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from typing import Iterable, Iterator, List, Optional, Tuple

from win_packet import ChannelDataTuple, build_block
from win_shm_writer import ShmConfig, WinShmWriter

PacketData = Tuple[datetime, List[ChannelDataTuple]]

_TIME_RE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")


def parse_stream(stream: Iterable[str]) -> Iterator[PacketData]:
    """Yield (datetime, channels) packets parsed from stdin stream generators."""
    current_timestamp: Optional[datetime] = None
    expected_num_channels: int = 0
    channels: List[ChannelDataTuple] = []

    for raw in stream:
        line = raw.strip()
        if not line:
            continue

        match = _TIME_RE.match(line)
        if match:
            if current_timestamp and channels:
                yield current_timestamp, channels
            yy, month, day, hour, minute, second, num_channels = map(int, match.groups())
            year = 2000 + yy if yy < 70 else 1900 + yy
            current_timestamp = datetime(year, month, day, hour, minute, second)
            expected_num_channels = num_channels
            channels = []
            continue

        parts = line.split()
        if len(parts) >= 2 and current_timestamp is not None:
            if expected_num_channels > 0 and len(channels) >= expected_num_channels:
                continue

            channel_id_str, sample_rate_str = parts[0], parts[1]
            try:
                channel_id = int(channel_id_str, 16)
                sample_rate = int(sample_rate_str)
                if sample_rate <= 0:
                    continue

                samples = [int(round(float(s))) if ("." in s or "e" in s.lower()) else int(s) for s in parts[2:]]
                channels.append((channel_id, sample_rate, samples))

                if expected_num_channels > 0 and len(channels) >= expected_num_channels:
                    yield current_timestamp, channels
                    current_timestamp = None
                    channels = []
            except ValueError:
                continue

    if current_timestamp and channels:
        yield current_timestamp, channels


def main() -> int:
    parser = argparse.ArgumentParser(description="Text -> WIN -> shared memory writer")
    parser.add_argument("shm_key", help="System V shm key (e.g., 11 or 0x000B)")
    parser.add_argument("--create", type=int, default=0, help="Create shm segment if missing, with SIZE bytes.")
    parser.add_argument("--no-overwrite", action="store_true", help="Compatibility flag.")
    parser.add_argument("--no-eob-size", action="store_true", help="Do not append end-of-block size trailer.")
    parser.add_argument("--init", action="store_true", help="Initialize shm header pointers on start.")
    parser.add_argument("--no-repair-header", action="store_true", help="Disable header pointer sanity repairs.")
    parser.add_argument(
        "--ss",
        default="auto",
        choices=["auto", "0", "1", "2", "3", "4", "5"],
        help="Sample encoding mode.",
    )
    parser.add_argument("--ss-strict", action="store_true", help="Do not auto-upgrade ss on overflow.")
    parser.add_argument("--with-tow", action="store_true", help="Insert 4-byte TOW after block size.")
    parser.add_argument("--time-offset", type=int, default=0, help="Value subtracted from TOW.")
    parser.add_argument("--debug", action="store_true", help="Print debug info to stderr.")
    args = parser.parse_args()

    shm_config = ShmConfig(
        key=int(args.shm_key, 0),
        create_size=args.create,
        overwrite=(not args.no_overwrite),
        add_eob_size=(not args.no_eob_size),
        init_on_start=args.init,
        repair_header=(not args.no_repair_header),
    )

    try:
        with WinShmWriter(shm_config) as writer:
            for timestamp, channels in parse_stream(sys.stdin):
                block = build_block(
                    timestamp,
                    channels,
                    add_eob_size=shm_config.add_eob_size,
                    ss_mode=args.ss,
                    with_tow=args.with_tow,
                    time_offset=args.time_offset,
                    ss_strict=args.ss_strict,
                )
                writer.write_block(block)

                if args.debug:
                    sys.stderr.write(f"[pmudmhs] wrote block {timestamp} ch={len(channels)} bytes={len(block)}\n")
                    sys.stderr.flush()
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())