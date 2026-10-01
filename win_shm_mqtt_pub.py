#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish raw WIN blocks from shared memory to MQTT broker."""

from __future__ import annotations

import argparse
import sys
import time

import paho.mqtt.client as mqtt
from win_shm_reader import WinShmReader


def main() -> None:
    parser = argparse.ArgumentParser(description="WIN SHM to MQTT Publisher")
    parser.add_argument("--shm-key", type=lambda x: int(x, 0), required=True, help="Shared memory key (e.g. 15 or 0x0f)")
    parser.add_argument("--broker", type=str, default="localhost", help="MQTT broker hostname/IP")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", type=str, required=True, help="MQTT topic (e.g. win/15/raw)")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0, help="MQTT QoS level")
    args = parser.parse_args()

    client = mqtt.Client()

    try:
        client.connect(args.broker, args.port, 60)
        client.loop_start()
        print(f"[pub] Connected to MQTT broker {args.broker}:{args.port}")
    except Exception as e:
        sys.stderr.write(f"[pub] Failed to connect to broker: {e}\n")
        sys.exit(1)

    try:
        with WinShmReader(key=args.shm_key) as reader:
            print(f"[pub] Reading SHM key={hex(args.shm_key)}, publishing to topic={args.topic}...")
            for block in reader.iter_raw_blocks():
                info = client.publish(args.topic, payload=block, qos=args.qos)
                
                # 送信受付のリターンコードチェック
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    sys.stderr.write(f"[pub] publish failed: rc={info.rc}\n")
                    
    except KeyboardInterrupt:
        print("\n[pub] Stopping publisher...")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()