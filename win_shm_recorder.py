#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Record WIN blocks from System-V shared memory into WIN disk files."""

from __future__ import annotations

import argparse

from win_file import WinFileWriter
from win_shm_reader import WinShmReader


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record WIN blocks from shared memory into minute-based WIN files"
    )
    parser.add_argument(
        "--shm-key",
        type=lambda x: int(x, 0),
        required=True,
        help="Shared memory key (e.g. 15 or 0x0f)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for WIN files",
    )
    parser.add_argument(
        "--no-drop-if-behind",
        action="store_true",
        help="Do not skip to the current write position when the reader falls behind",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.1,
        help="Polling interval in seconds (default: 0.1)",
    )
    args = parser.parse_args()

    reader = WinShmReader(
        key=args.shm_key,
        sleep=args.sleep,
        drop_if_behind=not args.no_drop_if_behind,
    )

    try:
        with reader, WinFileWriter(args.output_dir) as writer:
            print(
                f"[rec] SHM key={hex(args.shm_key)} -> "
                f"WIN files in {args.output_dir}"
            )
            for block in reader.iter_raw_blocks():
                writer.write_block(block)
    except KeyboardInterrupt:
        print("\n[rec] Stopping recorder...")


if __name__ == "__main__":
    main()
