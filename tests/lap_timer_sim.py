#!/usr/bin/env python3
"""
RoboACADEMY Lap Timer Simulator
================================
Simulates a NodeMCU E18 lap timer device over MQTT for end-to-end testing.

Usage:
    source ~/venv/bin/activate
    export MQTT_USERNAME=<mqtt-user>
    export MQTT_PASSWORD='your-mqtt-password'
    python tests/lap_timer_sim.py --competition 1 --laps 3 --sequence "2,6,5,4"

See tests/TEST_SCENARIOS.md for the full S1–S10 test scenario reference.
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
    p = argparse.ArgumentParser(description="RoboACADEMY Lap Timer Simulator")
    p.add_argument("--mac", default="AA:BB:CC:DD:EE:01",
                   help="MAC address used as device_id (default: AA:BB:CC:DD:EE:01)")
    p.add_argument("--competition", type=int, default=1,
                   help="Competition (Category) DB id — used for cmd subscription topic")
    p.add_argument("--laps", type=int, default=3,
                   help="Number of laps per run — simulator publishes laps+1 crossings")
    p.add_argument("--sequence", default=None,
                   help="Comma-separated crossing intervals in seconds, len=laps+1. "
                        "E.g. '2,6,5,4' for laps=3. Overrides --interval.")
    p.add_argument("--interval", type=float, default=5.0,
                   help="Uniform seconds between crossings when --sequence not given (default: 5)")
    p.add_argument("--mode", choices=["auto", "manual"], default="auto",
                   help="auto=timer fires automatically; manual=press Enter per crossing")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=51883)
    p.add_argument("--start-seq", type=int, default=1,
                   help="Starting value for the sequence counter (default: 1). "
                        "Use when restarting to continue a session without seq collisions.")
    return p.parse_args()


class LapTimerSim:
    def __init__(self, args):
        self.mac = args.mac
        self.competition_id = args.competition
        self.laps = args.laps
        self.mode = args.mode
        self.broker = args.broker
        self.port = args.port
        self.seq = args.start_seq
        self._run_active = False
        self._stop_requested = False

        # Resolve crossing intervals
        if args.sequence:
            parts = args.sequence.split(",")
            if len(parts) != self.laps + 1:
                sys.exit(
                    f"ERROR: --sequence must have {self.laps + 1} values (laps+1), "
                    f"got {len(parts)}: '{args.sequence}'"
                )
            self.intervals = [float(x) for x in parts]
        else:
            self.intervals = [float(args.interval)] * (self.laps + 1)

        # MQTT topics
        self.event_topic = f"robosteam/laptimer/{self.mac}/event"
        self.status_topic = f"robosteam/laptimer/{self.mac}/status"
        self.cmd_topic = f"robosteam/competition/{self.competition_id}/cmd"

        # Credentials from environment
        self.username = os.environ.get("MQTT_USERNAME", "")
        self.password = os.environ.get("MQTT_PASSWORD", "")

        # Build client
        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=self.mac,
        )
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"[{hhmmss()}] Connected to broker {self.broker}:{self.port}")
            client.subscribe(self.cmd_topic, qos=1)
            print(f"[{hhmmss()}] Subscribed to: {self.cmd_topic}")
            print(f"[{hhmmss()}] Publishing heartbeat to: {self.status_topic}")
            print(f"[{hhmmss()}] Beam crossings will go to: {self.event_topic}")
            print(f"[{hhmmss()}] Mode: {self.mode} | Laps: {self.laps} | "
                  f"Intervals: {self.intervals}")
            print()
        else:
            print(f"[{hhmmss()}] Connection failed: {reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        print(f"[{hhmmss()}] Disconnected (reason={reason_code})")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[{hhmmss()}] Bad payload on {msg.topic}: {msg.payload}")
            return

        cmd = payload.get("cmd", "").upper()
        run_id = payload.get("run_id")

        if cmd == "START":
            if self._run_active:
                print(f"[{hhmmss()}] Received START but a run is already active — ignored")
                return
            print(f"[{hhmmss()}] >>> START received (run_id={run_id}) <<<")
            self._stop_requested = False
            if self.mode == "auto":
                t = threading.Thread(target=self._auto_run, daemon=True)
                t.start()
            else:
                t = threading.Thread(target=self._manual_run, daemon=True)
                t.start()
        elif cmd == "STOP":
            print(f"[{hhmmss()}] >>> STOP received <<<")
            self._stop_requested = True
            self._run_active = False
        else:
            print(f"[{hhmmss()}] Unknown command: {payload}")

    # ------------------------------------------------------------------
    # Run modes
    # ------------------------------------------------------------------

    def _publish_crossing(self, crossing_index: int):
        ts = utc_now_iso8601z()
        payload = json.dumps({"seq": self.seq, "ts": ts})
        self.client.publish(self.event_topic, payload, qos=1)

        is_last = crossing_index == self.laps
        label = "RUN COMPLETE" if is_last else f"Crossing #{crossing_index}"
        print(f"[{hhmmss()}] {label} published  (seq={self.seq}, ts={ts})")
        self.seq += 1

    def _auto_run(self):
        self._run_active = True
        try:
            for i, delay in enumerate(self.intervals):
                if self._stop_requested:
                    print(f"[{hhmmss()}] Run aborted by STOP command before crossing {i}")
                    return
                time.sleep(delay)
                if self._stop_requested:
                    print(f"[{hhmmss()}] Run aborted by STOP command before crossing {i}")
                    return
                self._publish_crossing(i)
            print(f"[{hhmmss()}] Auto-run complete. Waiting for next START.")
        finally:
            self._run_active = False

    def _manual_run(self):
        self._run_active = True
        try:
            print(f"[{hhmmss()}] Manual mode: press Enter to publish each crossing "
                  f"(need {self.laps + 1} total).")
            for i in range(self.laps + 1):
                if self._stop_requested:
                    print(f"[{hhmmss()}] Run aborted by STOP command.")
                    return
                label = f"crossing {i} (RUN COMPLETE)" if i == self.laps else f"crossing {i}"
                try:
                    input(f"  Press Enter to publish {label} ... ")
                except EOFError:
                    print(f"[{hhmmss()}] EOF — stopping manual run.")
                    return
                if self._stop_requested:
                    print(f"[{hhmmss()}] Run aborted by STOP command.")
                    return
                self._publish_crossing(i)
            print(f"[{hhmmss()}] Manual run complete. Waiting for next START.")
        finally:
            self._run_active = False

    # ------------------------------------------------------------------
    # Heartbeat loop
    # ------------------------------------------------------------------

    def _heartbeat_loop(self):
        while True:
            payload = json.dumps({"ts": utc_now_iso8601z(), "firmware": "sim-1.0"})
            self.client.publish(self.status_topic, payload, qos=1)
            print(f"[{hhmmss()}] Heartbeat sent")
            time.sleep(30)

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    def run(self):
        print("=" * 60)
        print("  RoboACADEMY Lap Timer Simulator")
        print(f"  MAC:         {self.mac}")
        print(f"  Competition: {self.competition_id}")
        print(f"  Laps:        {self.laps}  ({self.laps + 1} crossings per run)")
        print(f"  Mode:        {self.mode}")
        if self.mode == "auto":
            print(f"  Intervals:   {self.intervals}")
            total_ms = sum(self.intervals[1:]) * 1000
            print(f"  Expected time_ms: {int(total_ms)} ms  "
                  f"(= sum of intervals[1:], i.e. excluding pre-start delay)")
        print("=" * 60)
        print()

        try:
            self.client.connect(self.broker, self.port, keepalive=60)
        except Exception as e:
            sys.exit(f"ERROR: Cannot connect to {self.broker}:{self.port} — {e}")

        # First heartbeat immediately, then every 30 s
        hb = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb.start()

        print(f"[{hhmmss()}] Waiting for START command on {self.cmd_topic} ...")
        print(f"[{hhmmss()}] (Sequence counter starts at seq={self.seq} for this session)")
        print()

        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print(f"\n[{hhmmss()}] Interrupted. Final seq counter was {self.seq - 1}.")
            self.client.disconnect()


def main():
    args = parse_args()
    sim = LapTimerSim(args)
    sim.run()


if __name__ == "__main__":
    main()
