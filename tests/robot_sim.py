#!/usr/bin/env python3
"""
RoboACADEMY Robot Simulator
============================
Simulates a PicoBot robot over MQTT for end-to-end testing.

Subscribes to competition-level and robot-specific command topics, logs
every START/STOP command with a timestamp, and publishes a status heartbeat
every 30 seconds.  With --respond it sends an ACK back on every command.

Usage:
    source ~/venv/bin/activate
    export MQTT_USERNAME=<mqtt-user>
    export MQTT_PASSWORD='your-mqtt-password'
    python tests/robot_sim.py --mac 11:22:33:44:55:01 --competition 4

See tests/README.md for full usage examples and Scenario S18–S20 reference.
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone


try:
    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion
except ImportError:
    sys.exit("ERROR: paho-mqtt not installed. Run: pip install paho-mqtt>=2.0")


def utc_now_iso8601z() -> str:
    now = datetime.now(timezone.utc)
    ms = now.microsecond // 1000
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def hhmmss() -> str:
    return datetime.now().strftime("%H:%M:%S")


def parse_args():
    p = argparse.ArgumentParser(description="RoboACADEMY Robot Simulator")
    p.add_argument("--mac", default="11:22:33:44:55:01",
                   help="MAC address used as robot device_id (default: 11:22:33:44:55:01)")
    p.add_argument("--competition", type=int, default=1,
                   help="Competition (Category) DB id — used for competition-level cmd topic")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=51883)
    p.add_argument("--user", default=None,
                   help="MQTT username (overrides MQTT_USERNAME env var)")
    p.add_argument("--password", default=None,
                   help="MQTT password (overrides MQTT_PASSWORD env var)")
    p.add_argument("--respond", action="store_true",
                   help="Send an ACK status message back on every START/STOP command")
    p.add_argument("--no-pong", action="store_true",
                   help="Never answer a PING. Simulates firmware that predates "
                        "PING/PONG, so you can test the heartbeat-freshness "
                        "fallback in the connection test (scenario S22)")
    p.add_argument("--firmware", default=None,
                   help="Firmware version to report in heartbeats and PONGs "
                        f"(default: {RobotSim.FIRMWARE_VERSION})")
    p.add_argument("--bound", action="store_true",
                   help="Act like a robot bound to a team: ignore the competition "
                        "broadcast and obey only this robot's own topic, as the "
                        "Phase 7 library does once the server has addressed it")
    return p.parse_args()


class RobotSim:
    FIRMWARE_VERSION = "robot-sim-1.0"

    def __init__(self, args):
        self.mac = args.mac
        self.competition_id = args.competition
        self.broker = args.broker
        self.port = args.port
        self.respond = args.respond
        self.answer_pings = not args.no_pong
        self.bound = args.bound
        if args.firmware:
            self.FIRMWARE_VERSION = args.firmware

        # Credentials: CLI args override env vars
        self.username = args.user or os.environ.get("MQTT_USERNAME", "")
        self.password = args.password or os.environ.get("MQTT_PASSWORD", "")

        # MQTT topics
        self.status_topic   = f"robosteam/robot/{self.mac}/status"
        self.robot_cmd_topic = f"robosteam/robot/{self.mac}/cmd"
        self.comp_cmd_topic  = f"robosteam/competition/{self.competition_id}/cmd"

        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=self.mac,
        )
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect    = self._on_connect
        self.client.on_message    = self._on_message
        self.client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"[{hhmmss()}] Connected to broker {self.broker}:{self.port}")
            client.subscribe(self.comp_cmd_topic,  qos=1)
            client.subscribe(self.robot_cmd_topic, qos=1)
            print(f"[{hhmmss()}] Subscribed to: {self.comp_cmd_topic}")
            print(f"[{hhmmss()}] Subscribed to: {self.robot_cmd_topic}")
            print(f"[{hhmmss()}] Heartbeat topic: {self.status_topic}")
            print(f"[{hhmmss()}] Firmware reported: {self.FIRMWARE_VERSION}")
            if self.respond:
                print(f"[{hhmmss()}] --respond ON: ACK will be published on each command")
            if not self.answer_pings:
                print(f"[{hhmmss()}] --no-pong ON: PINGs will go unanswered")
            if self.bound:
                print(f"[{hhmmss()}] --bound ON: broadcast commands will be ignored")
            print()
        else:
            print(f"[{hhmmss()}] Connection failed: reason_code={reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        print(f"[{hhmmss()}] Disconnected (reason={reason_code})")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[{hhmmss()}] Bad payload on {msg.topic}: {msg.payload!r}")
            return

        cmd     = payload.get("cmd", "").upper()
        run_id  = payload.get("run_id")
        source  = "competition" if msg.topic == self.comp_cmd_topic else "robot"

        if cmd == "PING":
            # Answered before the --bound check: a connection test is always
            # addressed to this robot, and it has to work before any team has
            # bound it — that is when a student needs it most.
            if self.answer_pings:
                self._send_pong(payload.get("nonce"))
            else:
                print(f"[{hhmmss()}] PING ignored (--no-pong): the server should "
                      f"fall back to heartbeat freshness")
            return

        if self.bound and source == "competition":
            print(f"[{hhmmss()}] broadcast {cmd} ignored (--bound): not addressed "
                  f"to this robot")
            return

        if cmd == "START":
            print(f"[{hhmmss()}] >>> START received (source={source}, run_id={run_id}) <<<")
            print(f"             full payload: {json.dumps(payload)}")
        elif cmd == "STOP":
            print(f"[{hhmmss()}] >>> STOP received  (source={source}, run_id={run_id}) <<<")
            print(f"             full payload: {json.dumps(payload)}")
        else:
            print(f"[{hhmmss()}] Command received on {msg.topic}: {json.dumps(payload)}")

        if self.respond and cmd in ("START", "STOP"):
            self._send_ack(cmd, run_id)

    # ------------------------------------------------------------------
    # ACK response
    # ------------------------------------------------------------------

    def _send_pong(self, nonce):
        """Answer a server connection test. The nonce must be echoed unchanged."""
        pong_payload = json.dumps({
            "device_id": self.mac,
            "ts":        utc_now_iso8601z(),
            "firmware":  self.FIRMWARE_VERSION,
            "cmd":       "PONG",
            "nonce":     nonce,
        })
        self.client.publish(self.status_topic, pong_payload, qos=1)
        print(f"[{hhmmss()}] >>> PING received — PONG sent (nonce={nonce}) <<<")

    def _send_ack(self, cmd: str, run_id):
        ack_payload = json.dumps({
            "device_id":       self.mac,
            "ts":              utc_now_iso8601z(),
            "firmware_version": self.FIRMWARE_VERSION,
            "ack":             cmd,
            "run_id":          run_id,
        })
        self.client.publish(self.status_topic, ack_payload, qos=1)
        print(f"[{hhmmss()}] ACK sent: {ack_payload}")

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    def _heartbeat_loop(self):
        while True:
            payload = json.dumps({
                "device_id":        self.mac,
                "ts":               utc_now_iso8601z(),
                "firmware_version": self.FIRMWARE_VERSION,
            })
            self.client.publish(self.status_topic, payload, qos=1)
            print(f"[{hhmmss()}] Heartbeat sent")
            time.sleep(30)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self):
        print("=" * 60)
        print("  RoboACADEMY Robot Simulator")
        print(f"  MAC:         {self.mac}")
        print(f"  Competition: {self.competition_id}")
        print(f"  Broker:      {self.broker}:{self.port}")
        print(f"  Respond:     {'yes (ACK on every command)' if self.respond else 'no'}")
        print("=" * 60)
        print()

        try:
            self.client.connect(self.broker, self.port, keepalive=60)
        except Exception as e:
            sys.exit(f"ERROR: Cannot connect to {self.broker}:{self.port} — {e}")

        hb = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb.start()

        print(f"[{hhmmss()}] Waiting for START/STOP commands ...")
        print(f"[{hhmmss()}] Press Ctrl-C to exit.")
        print()

        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print(f"\n[{hhmmss()}] Interrupted.")
            self.client.disconnect()


def main():
    args = parse_args()
    sim = RobotSim(args)
    sim.run()


if __name__ == "__main__":
    main()
