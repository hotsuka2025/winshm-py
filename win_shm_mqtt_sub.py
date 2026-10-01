#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Subscribe raw WIN blocks from MQTT broker and write to shared memory."""

from __future__ import annotations

import argparse
import sys
import time

import paho.mqtt.client as mqtt
from win_shm_writer import ShmConfig, WinShmWriter


def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT to WIN SHM Subscriber")
    parser.add_argument("--shm-key", type=lambda x: int(x, 0), required=True, help="Target shared memory key")
    parser.add_argument("--shm-size", type=int, default=0, help="Create SHM with size in KB (1000 bytes/KB) if it does not exist")
    parser.add_argument("--broker", type=str, default="localhost", help="MQTT broker hostname/IP")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", type=str, required=True, help="MQTT topic to subscribe (e.g. win/15/raw)")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0, help="MQTT QoS level")
    args = parser.parse_args()

    shm_config = ShmConfig(key=args.shm_key, create_size=args.shm_size * 1000)
    writer = WinShmWriter(shm_config)

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print(f"[sub] Connected. Subscribing to topic={args.topic}...")
            client.subscribe(args.topic, qos=args.qos)
        else:
            sys.stderr.write(f"[sub] Connection failed with code {rc}\n")

    def on_message(client, userdata, msg):
        if msg.payload:
            writer.write_block(msg.payload)

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(args.broker, args.port, 60)
        print(f"[sub] Connecting to MQTT broker {args.broker}:{args.port}...")
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[sub] Stopping subscriber...")
    finally:
        writer.close()
        client.disconnect()


if __name__ == "__main__":
    main()