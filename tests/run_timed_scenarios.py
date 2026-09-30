#!/usr/bin/env python3
"""
Phase 5.5 — TIMED scenario runner (S2–S6).

Drives the HTTP views to create and start runs, waits for the simulator
to publish crossings, then verifies DB state.

IMPORTANT: The simulator must already be running on the server before
running this script. Start it with the correct --sequence for each run
(or run it in manual mode for S6a/S6b).

Usage:
    cd ~/competitions/scoreboard
    source ~/venv/bin/activate

    # S2: Alpha Run 1  (simulator must be running with --sequence 2,6,5,4)
    python ../tests/run_timed_scenarios.py --team "Team Alpha" --wait 25

    # S3: Alpha Run 2  (restart simulator with --sequence 2,7,9,8 first)
    python ../tests/run_timed_scenarios.py --team "Team Alpha" --wait 30

    # S4: Alpha Run 3  (restart simulator with --sequence 2,5,10,8)
    python ../tests/run_timed_scenarios.py --team "Team Alpha" --wait 30

    # S5a: Bravo Run 1  (restart simulator with --sequence 2,9,8,7)
    python ../tests/run_timed_scenarios.py --team "Team Bravo" --wait 30

    # S6c: Charlie Run 3  (restart simulator with --sequence 2,8,9,10)
    python ../tests/run_timed_scenarios.py --team "Team Charlie" --wait 35
"""

import argparse
import os
import sys
import time
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from django.test import Client

from _judge_client import judge_client
from scoring.engine import counted_runs
from contest.models import Competition, Run, RunState, Result, Team


COMPETITION_ID = 4  # Line Following (TIMED)


def parse_args():
    p = argparse.ArgumentParser(description="TIMED scenario run driver")
    p.add_argument("--team", required=True, help="Team name, e.g. 'Team Alpha'")
    p.add_argument("--wait", type=int, default=25,
                   help="Seconds to wait for crossings before checking DB (default: 25)")
    p.add_argument("--expected-ms", type=int, default=None,
                   help="Expected time_ms value for verification (optional)")
    return p.parse_args()


def run(args):
    client = judge_client()

    # Find team
    # .first(): participant self-registration can create a same-named team,
    # so pick the oldest (the fixture one) instead of blowing up.
    team = (Team.objects.filter(name=args.team, contest__name="RoboACADEMY Test Cup 2026")
            .order_by('id').first())
    if team is None:
        sys.exit(f"ERROR: Team '{args.team}' not found in test contest.")

    competition = Competition.objects.get(id=COMPETITION_ID)
    # Phase E6: VOIDED runs don't consume an attempt — use the server's own rule.
    run_count   = counted_runs(team, competition)
    print(f"Team: {team.name} (id={team.id})")
    print(f"Category: {competition.name} (id={competition.id}, type={competition.competition_type})")
    print(f"Existing runs for this team: {run_count} / {competition.num_runs}")

    if run_count >= competition.num_runs:
        print(f"ERROR: Run limit reached ({competition.num_runs}). Cannot create more runs.")
        return

    # Step 1: Create PENDING run
    print()
    print(f"[1/3] Creating PENDING run for {team.name} ...")
    resp = client.post(
        f"/contest/competition/{COMPETITION_ID}/run/create",
        {"team_id": team.id},
    )
    if resp.status_code not in (200, 302):
        print(f"ERROR: run_create returned {resp.status_code}")
        print(resp.content[:500])
        return

    new_run = (
        Run.objects.filter(team=team, competition=competition, state=RunState.PENDING)
        .order_by("-id").first()
    )
    if not new_run:
        print("ERROR: PENDING run not found after creation.")
        return
    print(f"  Created run id={new_run.id}, state={new_run.state}")

    # Step 2: Start the run (triggers MQTT START to simulator)
    print(f"[2/3] Starting run id={new_run.id} (publishes MQTT START) ...")
    resp = client.post(f"/contest/run/{new_run.id}/start")
    if resp.status_code == 200:
        import json
        data = json.loads(resp.content)
        print(f"  run_start returned: {data}")
    else:
        print(f"ERROR: run_start returned {resp.status_code}")
        print(resp.content[:500])
        return

    new_run.refresh_from_db()
    if new_run.state != RunState.ACTIVE:
        print(f"ERROR: expected ACTIVE, got {new_run.state}")
        return
    print(f"  Run is now ACTIVE. Waiting {args.wait}s for simulator crossings ...")

    # Step 3: Wait for simulator crossings to be processed
    for i in range(args.wait):
        time.sleep(1)
        new_run.refresh_from_db()
        if new_run.state == RunState.COMPLETED:
            elapsed = i + 1
            print(f"  Run COMPLETED after ~{elapsed}s  ✓")
            break
        elif new_run.state == RunState.VOIDED:
            print(f"  Run was VOIDED unexpectedly after {i+1}s")
            break
    else:
        print(f"  Timeout: run still {new_run.state} after {args.wait}s")
        from devices.models import LapEvent
        laps = LapEvent.objects.filter(run=new_run).count()
        print(f"  LapEvents assigned to this run: {laps}")
        return

    # Step 4: Verify DB state
    print()
    print(f"[3/3] Verifying DB state ...")
    new_run.refresh_from_db()
    print(f"  run.state     = {new_run.state}")
    print(f"  run.time_ms   = {new_run.time_ms}")
    print(f"  run.is_best   = {new_run.is_best}")
    print(f"  run.penalty   = {new_run.penalty_time_ms}")

    from devices.models import LapEvent
    lap_events = LapEvent.objects.filter(run=new_run).order_by("sequence")
    print(f"  LapEvents     = {lap_events.count()} crossings")
    for ev in lap_events:
        print(f"    seq={ev.sequence}  ts={ev.timestamp_utc.strftime('%H:%M:%S.%f')[:-3]}")

    # Verify best result
    try:
        result = Result.objects.get(team=team, competition=competition)
        print(f"  Result.score  = {result.score}")
    except Result.DoesNotExist:
        print(f"  Result: not found yet")

    # Verify expected time
    if args.expected_ms and new_run.time_ms:
        tolerance = 200  # ms
        diff = abs(new_run.time_ms - args.expected_ms)
        if diff <= tolerance:
            print(f"  time_ms check: {new_run.time_ms} ≈ {args.expected_ms} (diff={diff}ms)  ✓")
        else:
            print(f"  time_ms check: {new_run.time_ms} ≠ {args.expected_ms} (diff={diff}ms)  ✗")

    # is_best check
    all_runs = Run.objects.filter(team=team, competition=competition, state=RunState.COMPLETED)
    best_count = all_runs.filter(is_best=True).count()
    if best_count == 1:
        best = all_runs.get(is_best=True)
        print(f"  is_best check: exactly 1 best run (id={best.id}, time_ms={best.time_ms})  ✓")
    elif best_count == 0:
        print(f"  is_best check: no best run found  ✗")
    else:
        print(f"  is_best check: {best_count} runs marked is_best (should be 1)  ✗")

    print()
    print("Done.")


if __name__ == "__main__":
    run(parse_args())
