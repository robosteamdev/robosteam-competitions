#!/usr/bin/env python3
"""
Phase 5.5 — S6a/S6b: Charlie DNF scenarios (partial crossings then void).

Usage:
    cd ~/competitions/scoreboard && source ~/venv/bin/activate

    # S6a: 1 crossing then void
    python ../tests/run_dnf_scenarios.py --team "Team Charlie" --crossings 1

    # S6b: 2 crossings then void
    python ../tests/run_dnf_scenarios.py --team "Team Charlie" --crossings 2
"""

import argparse
import json
import os
import sys
import time
import django
from datetime import datetime, timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from django.test import Client

from _judge_client import judge_client
from scoring.engine import counted_runs
from contest.models import Competition, Run, RunState, Result, Team


COMPETITION_ID = 4  # Line Following (TIMED)
DEVICE_MAC = "AA:BB:CC:DD:EE:01"
MQTT_BROKER = "localhost"
MQTT_PORT = 1883


def utc_now_iso8601z():
    now = datetime.now(timezone.utc)
    ms = now.microsecond // 1000
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def parse_args():
    p = argparse.ArgumentParser(description="DNF scenario driver (S6a/S6b)")
    p.add_argument("--team", required=True, help="Team name")
    p.add_argument("--crossings", type=int, required=True,
                   help="Number of beam crossings to publish before voiding")
    p.add_argument("--start-seq", type=int, default=25,
                   help="Starting sequence number for published crossings")
    return p.parse_args()


def publish_crossing(seq, client, mac):
    """Publish a single beam crossing via paho-mqtt."""
    ts = utc_now_iso8601z()
    topic = f"robosteam/laptimer/{mac}/event"
    payload = json.dumps({"seq": seq, "ts": ts})
    result = client.publish(topic, payload, qos=1)
    result.wait_for_publish(timeout=5)
    print(f"  Published crossing seq={seq} ts={ts}")
    return ts


def run(args):
    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion

    client_obj = judge_client()

    # Find team — .first() because participant self-registration can create a
    # same-named team; the oldest row is the fixture one.
    team = (Team.objects.filter(name=args.team, contest__name="RoboACADEMY Test Cup 2026")
            .order_by('id').first())
    if team is None:
        sys.exit(f"ERROR: Team '{args.team}' not found.")

    competition = Competition.objects.get(id=COMPETITION_ID)
    # Phase E6: VOIDED runs don't consume an attempt — use the server's own rule.
    run_count = counted_runs(team, competition)
    print(f"Team: {team.name}  runs={run_count}/{competition.num_runs}")

    # A voided run must not create or change the team's Result — remember what
    # it was so the driver stays re-runnable against a contest with history.
    result_before = Result.objects.filter(team=team, competition=competition).first()
    score_before = result_before.score if result_before else None

    # Create PENDING run
    print(f"\n[1/4] Creating PENDING run ...")
    resp = client_obj.post(
        f"/contest/competition/{COMPETITION_ID}/run/create",
        {"team_id": team.id},
    )
    if resp.status_code not in (200, 302):
        print(f"ERROR: run_create returned {resp.status_code}")
        return
    new_run = (
        Run.objects.filter(team=team, competition=competition, state=RunState.PENDING)
        .order_by("-id").first()
    )
    print(f"  Created run id={new_run.id}")

    # Start the run
    print(f"[2/4] Starting run id={new_run.id} ...")
    resp = client_obj.post(f"/contest/run/{new_run.id}/start")
    if resp.status_code != 200:
        print(f"ERROR: run_start returned {resp.status_code}")
        return
    new_run.refresh_from_db()
    assert new_run.state == RunState.ACTIVE, f"Expected ACTIVE, got {new_run.state}"
    print(f"  Run is ACTIVE")

    # Connect paho MQTT and publish partial crossings
    print(f"[3/4] Publishing {args.crossings} partial crossing(s) ...")
    mqclient = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id="dnf-scenario-driver",
    )
    username = os.environ.get("MQTT_USERNAME", "")
    password = os.environ.get("MQTT_PASSWORD", "")
    mqclient.username_pw_set(username, password)
    mqclient.connect(MQTT_BROKER, MQTT_PORT, keepalive=10)
    mqclient.loop_start()
    time.sleep(1)

    seq = args.start_seq
    for i in range(args.crossings):
        time.sleep(1)  # small gap so timestamps are distinct
        publish_crossing(seq, mqclient, DEVICE_MAC)
        seq += 1

    time.sleep(2)  # let bridge process the events
    mqclient.loop_stop()
    mqclient.disconnect()

    # Void the run
    print(f"[4/4] Voiding run ...")
    resp = client_obj.post(f"/contest/run/{new_run.id}/stop")
    if resp.status_code == 200:
        data = json.loads(resp.content)
        print(f"  run_stop returned: {data}")
    else:
        print(f"ERROR: run_stop returned {resp.status_code}: {resp.content[:200]}")
        return

    # Verify
    new_run.refresh_from_db()
    print(f"\n  run.state     = {new_run.state}")
    print(f"  run.time_ms   = {new_run.time_ms}")

    from devices.models import LapEvent
    lap_events = LapEvent.objects.filter(run=new_run).count()
    print(f"  LapEvents     = {lap_events} (expected {args.crossings})")

    result_after = Result.objects.filter(team=team, competition=competition).first()
    score_after = result_after.score if result_after else None
    print(f"  Result score  = {score_after} (unchanged, was {score_before})")

    # Checks
    ok = True
    if new_run.state != RunState.VOIDED:
        print(f"  FAIL: expected VOIDED, got {new_run.state}")
        ok = False
    else:
        print(f"  state check: VOIDED  ✓")
    if lap_events != args.crossings:
        print(f"  FAIL: expected {args.crossings} LapEvents, got {lap_events}")
        ok = False
    else:
        print(f"  LapEvents check: {lap_events} crossings assigned  ✓")
    if score_after != score_before:
        print(f"  FAIL: voided run changed the Result ({score_before} -> {score_after})")
        ok = False
    else:
        print(f"  Result untouched by the voided run  ✓")

    if ok:
        print(f"\nS6 {'a' if args.crossings == 1 else 'b'} PASSED ✓")
    else:
        print(f"\nS6 {'a' if args.crossings == 1 else 'b'} FAILED ✗")


if __name__ == "__main__":
    run(parse_args())
