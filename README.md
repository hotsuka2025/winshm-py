English | [日本語](README_ja.md)

# winshm-py

**winshm-py** is a Python-native reader/writer library for Linux shared memory (IPC) compatible with the [WIN-System](https://wwweic.eri.u-tokyo.ac.jp/WIN/Eindex.html) (Urabe & Tsukada, 1992) developed by the Earthquake Research Institute, The University of Tokyo (ERI).

It enables direct reading and writing of WIN-format time-series seismic data to/from shared memory segments without relying on external C binaries or sub-processes.

## Overview

The WIN-System is a multi-channel seismic waveform processing system that has long been widely used in seismic observation networks in Japan. While traditional tools have centered on C-based utilities for shared memory access, `winshm-py` provides a native Python implementation for low-latency IPC stream processing.

The primary purpose of this project is to provide **Python-based access to WIN shared memory**. The shared-memory reader and writer are the core functionality of the library. This project does not aim to replace the entire WIN system with Python. Instead, it is intended to improve compatibility with real-time processing using Python and to lower the barrier to using the WIN system for learning and educational purposes.


For experimental purposes, the repository also includes utilities for sending WIN blocks over MQTT and recording WIN blocks to files. These are not part of the core library, but are provided as **experimental utilities** for testing data transport and storage paths using the core functionality. The MQTT utilities do not aim to be compatible with `raw2mq` / `mq2raw` of the original WIN-System.

## Key Features

- **Pure Python Implementation**: Interacts directly with Linux shared memory (IPC) without C dependencies or wrapper scripts.
- **Bi-directional Shared Memory I/O**: Supports both reading (`win_shm_reader.py` / `pyshmin.py`) and writing (`win_shm_writer.py` / `pyshmout.py`) WIN format data packets.
- **Low-Latency IPC**: Can be used for real-time waveform streaming and evaluation environments for AI phase-picking models.
- **Compatibility with the Original WIN-System**: Supports the shared memory structures and packet format of the original WIN-System.
- **Experimental MQTT Transport**: `win_shm_mqtt_pub.py` / `win_shm_mqtt_sub.py` can transfer WIN blocks between shared-memory segments via an MQTT broker.
- **Experimental File Recording**: `win_shm_recorder.py` can record WIN blocks from shared memory into minute-based WIN files.

## Repository Structure

### Core shared-memory functionality

- `win_shm_reader.py` / `pyshmin.py`: Shared memory reader components for streaming WIN packets from IPC segments.
- `win_shm_writer.py` / `pyshmout.py`: Shared memory writer components for injecting WIN packets into IPC segments.
- `win_shm_common.py`: Shared memory structure definitions and common IPC utilities.
- `win_packet.py`: WIN packet structures, encoding, and decoding functions.

### Experimental utilities

- `win_shm_mqtt_pub.py`: Reads raw WIN blocks from shared memory and publishes them to an MQTT topic.
- `win_shm_mqtt_sub.py`: Subscribes to an MQTT topic and writes received WIN blocks to shared memory.
- `win_shm_recorder.py`: Reads WIN blocks from shared memory and records them as minute-based WIN files.
- `win_file.py`: WIN file writing utilities used by the recorder.

The MQTT and file-recording utilities are separated from the core shared-memory implementation. They are intended primarily for experiments, testing, and demonstrating example data paths.

## Requirements

- **OS**: Linux / Ubuntu (System V shared memory IPC required)
- **Python**: 3.10+ (tested on Python 3.12)
- **MQTT utilities**: `paho-mqtt` is required when using the MQTT functionality.
- **MQTT broker**: An MQTT broker such as Mosquitto is required for MQTT experiments.

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

### Command-line examples

#### `pyshmin` — WIN text format to shared memory

`pyshmin` reads the **WIN text format** from standard input, converts it into WIN blocks, and writes the blocks to shared memory.

For example, a WIN text format data stream can be piped directly into `pyshmin`:

```bash
cat waveform.txt | python pyshmin.py --shm-key 15
```

The WIN text format consists of time information followed by sample data for each channel.

```text
2026 09 28 12 00 00 2
0101 100 0.123 0.125 0.121 ...
0102 100 0.456 0.452 0.459 ...
```

When creating a new shared memory segment, its size can be specified:

```bash
cat waveform.txt | python pyshmin.py --shm-key 15 --shm-size 1048576
```

#### `pyshmout` — Read WIN data from shared memory

`pyshmout` reads WIN data from shared memory and provides several command-line output modes.

For example, a specific channels can be selected:

```bash
python pyshmout.py --shm-key 15 -c 0101,0102,0103
```

The WIN data can also be output as text:

```bash
python pyshmout.py --shm-key 15 -c 0101 -t
```

The `--plot` option provides a real-time terminal display of up to three channels:

```bash
python pyshmout.py --shm-key 15 --plot 0101,0102,0103
```

In --plot mode, you can select the decimation method used for waveform display from last, mean, rms, and p2p. This can be used to check in real time from the terminal whether waveform data is being properly written to shared memory.

```bash
python pyshmout.py --shm-key 15 --plot 0101 --plot-metric rms
```


## Experimental Utilities

### MQTT: Shared Memory to Shared Memory

The MQTT utilities provide an experimental path for retrieving WIN blocks from shared memory and transferring them to another shared-memory segment via MQTT.

```
[WIN data source]
      |
      v
System-V shared memory
      |
      v
win_shm_mqtt_pub.py
      |
      | MQTT
      v
MQTT broker
      |
      v
win_shm_mqtt_sub.py
      |
      v
System-V shared memory
      |
      v
[WIN data consumer]
```

Publisher example:

```bash
python win_shm_mqtt_pub.py \
    --shm-key 15 \
    --broker 192.168.1.100 \
    --port 1883 \
    --topic win/15/raw
```

Subscriber example:

```bash
python win_shm_mqtt_sub.py \
    --shm-key 16 \
    --shm-size 1024 \
    --broker 192.168.1.100 \
    --port 1883 \
    --topic win/15/raw
```

The publisher sends raw WIN blocks as MQTT payloads, and the subscriber writes the received payloads directly into the destination WIN shared-memory segment.

### File Recording

`win_shm_recorder.py` is a simple experimental recorder for writing WIN blocks from shared memory to minute-based WIN files.

```bash
python win_shm_recorder.py \
    --shm-key 15 \
    --output-dir ./win-data
```

By default, the recorder uses a short polling interval and skips ahead to the current write position if the reader falls behind in the ring buffer. Use `--no-drop-if-behind` to disable this behavior.

These utilities are intended for experiments and testing of communication and storage paths centered around WIN shared memory. They do not replace the core shared-memory reader/writer functionality.

## License

This project is licensed under the **GNU General Public License v2.0 (GPL-2.0)** - matching the stock WIN-System license by ERI, The University of Tokyo.

## References

- ERI WIN-System HP: [https://wwweic.eri.u-tokyo.ac.jp/WIN/Jindex.html](https://wwweic.eri.u-tokyo.ac.jp/WIN/Jindex.html)
- ERI WIN-System Manual: [https://wwweic.eri.u-tokyo.ac.jp/WIN/man.en/](https://wwweic.eri.u-tokyo.ac.jp/WIN/man.en/)
- Urabe, T., & Tsukada, S., 1992. win --- A Workstation Program for Processing Waveform Data from Microearthquake Networks, Seismological Society of Japan Fall Meeting Abstracts, P-41.
- Urabe, T., 1994. A Common Format for Multi-Channel Earthquake Waveform Data, Seismological Society of Japan Abstracts, No. 2, P-24.
