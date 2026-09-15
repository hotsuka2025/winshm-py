#!/usr/bin/env python3
"""
shmdump.py

WIN shared-memory dumper aligned with object-oriented WinShmReader (Vectorized version).

- Default : debug print
- -c      : channel filter (16-hex codes, e.g., -c 0101 0102)
- -t      : text mode
- -p      : peek mode
- --plot  : btm-like sparkline display (1-3 channels)
"""

import argparse
import curses
import math
import re
import sys
from collections import deque
from datetime import datetime
from typing import Optional, Set

import numpy as np
from win_shm_reader import WinShmReader

# =========================
# Channel normalization
# =========================
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def norm_ch_to_4hex_no0x(ch) -> str:
    """Normalize channel id to 4-digit HEX string WITHOUT '0x' prefix."""
    s = str(ch).strip()
    if s.lower().startswith("0x"):
        s = s[2:]

    if _HEX_RE.match(s):
        s2 = s.upper()
        if len(s2) <= 4:
            return s2.zfill(4)
        return s2

    try:
        v = int(s, 0)
        return f"{v:04X}"
    except ValueError:
        return s.upper()


def parse_channel_args(ch_list: Optional[list[str]]) -> Optional[Set[int]]:
    """Convert CLI channel arguments into a set of integer channel codes."""
    if not ch_list:
        return None
    res = set()
    for item in ch_list:
        for ch_str in item.replace(",", " ").split():
            if ch_str.strip():
                norm_hex = norm_ch_to_4hex_no0x(ch_str)
                res.add(int(norm_hex, 16))
    return res if res else None


# =========================
# Metric computation (Vectorized)
# =========================
def compute_metric(samples: np.ndarray, metric: str) -> float:
    if samples.size == 0:
        return float("nan")

    if metric == "last":
        return float(samples[-1])
    if metric == "mean":
        return float(np.mean(samples))
    if metric == "rms":
        return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if metric == "p2p":
        return float(np.ptp(samples))

    raise ValueError(metric)


# =========================
# Downsampling (Vectorized)
# =========================
def adaptive_downsample(samples: np.ndarray, sr: int, plot_pps: int = 10, mode: str = "mean") -> list[float]:
    """Make ~plot_pps points per second from raw samples with sampling rate sr."""
    n = len(samples)
    if n == 0 or sr is None or sr <= 0 or plot_pps <= 0:
        return []

    if sr <= plot_pps or n <= plot_pps:
        return samples.astype(float).tolist()

    n_out = int(plot_pps)
    segs = np.array_split(samples, n_out)

    if mode == "mean":
        return [float(np.mean(s)) for s in segs if len(s) > 0]
    elif mode == "rms":
        return [float(np.sqrt(np.mean(s.astype(np.float64) ** 2))) for s in segs if len(s) > 0]
    elif mode == "p2p":
        return [float(np.ptp(s)) for s in segs if len(s) > 0]
    elif mode == "last":
        return [float(s[-1]) for s in segs if len(s) > 0]
    else:
        raise ValueError(f"unknown downsample mode: {mode}")


# =========================
# Sparkline renderer
# =========================
def render_plot(stdscr, ts, ch_hex, sr, nsamp, metric, values, last_val, y0=0, show_header=False, plot_pps_eff=10.0):
    """1-channel braille plot renderer (4 plot rows, 16-level vertical resolution)."""
    h, w = stdscr.getmaxyx()

    stats_w = 26
    spark_chars = max(10, w - 2 - stats_w)
    points_needed = spark_chars * 2

    if values.maxlen != points_needed:
        newv = deque(values, maxlen=points_needed)
        values.clear()
        values.extend(newv)

    finite = [v for v in values if math.isfinite(v)]
    if finite:
        raw_min = min(finite)
        raw_max = max(finite)
        offset = sum(finite) / len(finite)
        dvals = [v - offset for v in finite]
        dmin = min(dvals)
        dmax = max(dvals)
        maxabs = max(abs(v) for v in dvals)
        if maxabs == 0:
            maxabs = 1.0
    else:
        raw_min, raw_max = -1.0, 1.0
        offset = 0.0
        dmin, dmax = -1.0, 1.0
        maxabs = 1.0

    TICK_SEC = 10.0
    tick_chars = max(1, int(round((TICK_SEC * plot_pps_eff) / 2.0)))

    def _dot_bit(col: int, row: int) -> int:
        if col == 0:
            return [0x01, 0x02, 0x04, 0x40][row]
        else:
            return [0x08, 0x10, 0x20, 0x80][row]

    def _val_to_y16(v: float):
        if not math.isfinite(v):
            return None
        v = v - offset
        t = v / maxabs
        if t < -1.0:
            t = -1.0
        elif t > 1.0:
            t = 1.0
        y = int(round((1.0 - t) * 7.5))
        if y < 0:
            y = 0
        elif y > 15:
            y = 15
        return y

    vals = list(values)
    if len(vals) < points_needed:
        vals = [float("nan")] * (points_needed - len(vals)) + vals
    else:
        vals = vals[-points_needed:]

    plot_lines = [[] for _ in range(4)]

    for i in range(spark_chars):
        masks = [0x00, 0x00, 0x00, 0x00]
        for col in (0, 1):
            v = vals[2 * i + col]
            y16 = _val_to_y16(v)
            if y16 is None:
                continue
            block_id = y16 // 4
            within = y16 % 4
            masks[block_id] |= _dot_bit(col, within)
        for b in range(4):
            plot_lines[b].append(chr(0x2800 + masks[b]))

    y_title = y0
    y_plot0 = y0 + 3
    y_plot1 = y0 + 4
    y_plot2 = y0 + 5
    y_plot3 = y0 + 6
    y_ruler = y0 + 7

    if show_header:
        stdscr.addnstr(y_title, 0, f"CH={ch_hex} SR={sr} metric={metric}  TS={ts}  N={nsamp}", w - 1)

    stdscr.addnstr(y_plot0, 1, "".join(plot_lines[0]), spark_chars)
    stdscr.addnstr(y_plot1, 1, "".join(plot_lines[1]), spark_chars)
    stdscr.addnstr(y_plot2, 1, "".join(plot_lines[2]), spark_chars)
    stdscr.addnstr(y_plot3, 1, "".join(plot_lines[3]), spark_chars)

    ruler = [("+" if (i % tick_chars == 0) else "-") for i in range(spark_chars)]
    stdscr.addnstr(y_ruler, 1, "".join(ruler), spark_chars)

    x0 = 2 + spark_chars
    stdscr.addnstr(y_ruler - 6, x0, f" last: {last_val:.6g}", stats_w)
    stdscr.addnstr(y_ruler - 5, x0, f"offset: {offset:.6g}", stats_w)
    stdscr.addnstr(y_ruler - 4, x0, f" raw: {raw_min:.6g}/{raw_max:.6g}", stats_w)
    stdscr.addnstr(y_ruler - 3, x0, f"dmin: {dmin:.6g}", stats_w)
    stdscr.addnstr(y_ruler - 2, x0, f"dmax: {dmax:.6g}", stats_w)
    stdscr.addnstr(y_ruler - 1, x0, f"scale: ±{maxabs:.6g}", stats_w)
    stdscr.addnstr(y_ruler,     x0, f"tick: {int(TICK_SEC)}s", stats_w)


# ========================
# Multi-channel plot renderer
# ========================
def render_plot_multi(stdscr, states, key_ts):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    if h < 18 or w < 50:
        stdscr.addnstr(0, 0, "Terminal too small for multi-plot (try bigger terminal).", w - 1)
        stdscr.refresh()
        return

    stdscr.addnstr(0, 0, f"WIN SHM MULTI | {len(states)}ch | (q to quit)", w - 1)

    block_h = 9

    y = 2
    for st in states[:3]:
        render_plot(
            stdscr,
            ts=st.get("ts", key_ts),
            ch_hex=st["ch"],
            sr=st.get("sr", 0),
            nsamp=st.get("ns", 0),
            metric=st["metric"],
            values=st["values"],
            last_val=st.get("last", float("nan")),
            y0=y,
            show_header=True,
            plot_pps_eff=st.get("pps", 10.0),
        )
        y += block_h

    stdscr.refresh()


# =========================
# Plot loop
# =========================
def run_plot(key, ch, peek, sleep, metric):
    targets = [norm_ch_to_4hex_no0x(x) for x in str(ch).split(",") if x.strip()]
    targets = targets[:3]
    target_set = set(targets)
    target_channel_ints = {int(x, 16) for x in targets}

    plot_pps = 10
    ds_mode = metric if metric in ("mean", "rms", "p2p", "last") else "mean"

    def loop(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)

        states = []
        for t in targets:
            states.append({
                "ch": t,
                "sr": 0,
                "ns": 0,
                "pps": float(plot_pps),
                "metric": metric,
                "values": deque(maxlen=1000),
                "last": float("nan"),
                "ts": datetime.now(),
            })

        idx = {st["ch"]: i for i, st in enumerate(states)}

        with WinShmReader(key=key, peek=peek, sleep=sleep, target_channels=target_channel_ints) as r:
            for block in r.iter_blocks():
                for channel in block.channels:
                    ch_norm = norm_ch_to_4hex_no0x(channel.channel_id)
                    if ch_norm not in target_set:
                        continue

                    st = states[idx[ch_norm]]
                    st["sr"] = channel.sample_rate
                    st["ns"] = len(channel.samples)
                    st["ts"] = block.timestamp

                    st["last"] = compute_metric(channel.samples, metric)

                    try:
                        plot_points = adaptive_downsample(
                            channel.samples, channel.sample_rate, plot_pps=plot_pps, mode=ds_mode
                        )
                    except Exception:
                        plot_points = [st["last"]]

                    if channel.sample_rate and channel.sample_rate > 0:
                        st["pps"] = float(min(channel.sample_rate, plot_pps))
                    else:
                        st["pps"] = float(plot_pps)

                    if plot_points:
                        st["values"].extend(plot_points)

                render_plot_multi(stdscr, states, block.timestamp)

                if stdscr.getch() in (ord("q"), ord("Q")):
                    raise KeyboardInterrupt

    curses.wrapper(loop)


# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser(description="WIN Shared Memory Dumper Tool")
    ap.add_argument("key", help="Shared memory key (e.g., 0x01 or 1)")
    ap.add_argument(
        "-c", "--channels",
        nargs="+",
        help="Target channel codes in HEX (e.g., -c 0101 0102 or -c 0101,0102)",
    )
    ap.add_argument("-t", action="store_true", help="Output in text mode")
    ap.add_argument("-p", "--peek", action="store_true", help="Peek mode (do not update read pointer)")
    ap.add_argument("--sleep", type=float, default=0.1, help="Sleep interval in seconds")
    ap.add_argument("--plot", help="Plot mode for target channels (e.g., --plot 0101,0102)")
    ap.add_argument(
        "--plot-metric",
        default="mean",
        choices=["last", "mean", "rms", "p2p"],
        help="Metric for plot mode downsampling",
    )
    args = ap.parse_args()

    key = int(args.key, 0)

    if args.plot:
        run_plot(key, args.plot, args.peek, args.sleep, args.plot_metric)
        return 0

    target_channels = parse_channel_args(args.channels)

    try:
        with WinShmReader(key=key, peek=args.peek, sleep=args.sleep, target_channels=target_channels) as r:
            for block in r.iter_blocks():
                ts = block.timestamp
                if args.t:
                    nch = len(block.channels)
                    print(
                        f"{ts.year%100:02d} {ts.month:02d} {ts.day:02d} "
                        f"{ts.hour:02d} {ts.minute:02d} {ts.second:02d} {nch}"
                    )
                    for channel in block.channels:
                        if channel.samples.size > 0:
                            print(f"{channel.channel_id} {channel.sample_rate} " + " ".join(map(str, channel.samples)))
                        else:
                            print(f"{channel.channel_id} {channel.sample_rate}")
                else:
                    print("\n--- Block ---")
                    print("TS:", ts)
                    for channel in block.channels:
                        print(
                            f"CH={channel.channel_id} SR={channel.sample_rate} N={len(channel.samples)} first5={channel.samples[:5]}"
                        )

    except KeyboardInterrupt:
        print("\nStopped", file=sys.stderr)


if __name__ == "__main__":
    main()