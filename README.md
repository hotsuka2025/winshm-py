English | [日本語](README_ja.md)

# winshm-py

**winshm-py** is a Pure Python reader/writer library for Linux shared memory (IPC) compatible with the stock WIN-System developed by the Earthquake Research Institute, The University of Tokyo (ERI_UTokyo).

It enables direct reading and writing of WIN-format time-series seismic data to/from shared memory segments without relying on external C binaries or sub-processes.

## Overview

The WIN-System is a multi-channel seismic waveform processing system widely used in Japan. While traditional tools rely on C binaries for shared memory access, `winshm-py` provides a native Python implementation for low-latency IPC stream ingestion and injection.

This facilitates seamless integration between real-time WIN data streams and modern Python data science / AI pipelines (e.g., PyTorch, TensorFlow, PhaseNet, ObsPy).

## Key Features

- **Pure Python Implementation**: Interacts directly with Linux shared memory (IPC) without C dependencies or wrapper scripts.
- **Bi-directional Shared Memory I/O**: Supports both reading (`win_shm_reader.py` / `pyshmin.py`) and writing (`win_shm_writer.py` / `pyshmout.py`) WIN format data packets.
- **Low-Latency IPC**: Direct shared memory access for real-time waveform streaming, simulations, and AI testbeds.
- **Stock WIN Compatibility**: Fully compatible with standard WIN system shared memory structures and packet specifications developed by ERI, UTokyo.

## Repository Structure

- `win_shm_reader.py` / `pyshmin.py`: Shared memory reader components for streaming WIN packets from IPC segments.
- `win_shm_writer.py` / `pyshmout.py`: Shared memory writer components for injecting WIN packets into IPC segments.
- `win_shm_common.py`: Shared memory structure definitions and common IPC utilities.
- `win_packet.py`: WIN packet structures, encoding, and decoding functions.

## Requirements

- **OS**: Linux / Ubuntu (System V shared memory IPC required)
- **Python**: 3.10+ (tested on Python 3.12)

## Basic Usage

### Reading from Shared Memory
```python
from win_shm_reader import WinShmReader

# Attach to WIN shared memory segment
reader = WinShmReader(shm_id=1)
for packet in reader.read_packets():
    print(packet)
```

### Writing to Shared Memory
```python
from win_shm_writer import WinShmWriter

# Attach and write packet to WIN shared memory segment
writer = WinShmWriter(shm_id=1)
writer.write_packet(raw_packet_bytes)
```

## License

This project is licensed under the **GNU General Public License v2.0 (GPL-2.0)** - matching the stock WIN-System license by ERI, The University of Tokyo.

## References

- Urabe, T., & Tsukada, S., 1992. win --- A Workstation Program for Processing Waveform Data from Microearthquake Networks, Seismological Society of Japan Fall Meeting Abstracts, P-41.
- Urabe, T., 1994. A Common Format for Multi-Channel Earthquake Waveform Data, Seismological Society of Japan Abstracts, No. 2, P-24.
- ERI WIN-System Manual: [https://wwweic.eri.u-tokyo.ac.jp/WIN/man.ja/](https://wwweic.eri.u-tokyo.ac.jp/WIN/man.ja/)
