#!/usr/bin/env python3
"""
Phase 5.13 — Server feature scenarios (S15–S17)

Tests auto-timeout, device self-registration, and MQTT fallback / manual mode.
All automated — no MQTT broker or browser required.

Usage:
    cd ~/competitions/scoreboard
    source ~/venv/bin/activate
    python ../tests/run_server_complete_scenarios.py --scenario S15
    python ../tests/run_server_complete_scenarios.py --scenario S16
    python ../tests/run_server_complete_scenarios.py --scenario S17
    python ../tests/run_server_complete_scenarios.py --all
"""

import argparse
import json
import os
import secrets
import sys
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")

import django
django.setup()

from django.test import Client

from _judge_client import judge_client
from django.utils import timezone

from contest.models import Competition, CompetitionType, Contest, Result, Run, RunState, Team
from devices.models import LapTimerDevice, RegistrationStatus


PASS = "✓"
FAIL = "✗"


def check(condition, msg_pass, msg_fail=""):
    if condition:
        print(f"  {PASS}  {msg_pass}")
    else:
        print(f"  {FAIL}  {msg_fail or msg_pass}")
    return condition


def _tok():
    return secrets.token_hex(8)


def _cleanup_contest(contest):
    # Contest CASCADE deletes Team, Competition → Run, Result, OverallResult
    contest.delete()
    print("  (test data cleaned up)")


# ── S15 — Auto-timeout ────────────────────────────────────────────────────────

def s15():
    print("\n── S15: Auto-timeout ────────────────────────────────────────────")
    print("ACTIVE TIMED run exceeds timeout_seconds → check_and_void_timed_out_runs().")
    print()

    contest = Contest.objects.create(name="S15 Timeout Test")
    comp = Competition.objects.create(
        name="LF S15", contest=contest,
        competition_type=CompetitionType.TIMED,
        timeout_seconds=5, num_laps=1,
        token=_tok(),
    )
    team = Team.objects.create(name="Alpha S15", contest=contest, token=None)
    run = Run.objects.create(
        team=team, competition=comp,
        start_time=timezone.now(), duration=0, state=RunState.ACTIVE,
    )

    print(f"  Created ACTIVE run id={run.id}, timeout_seconds=5")
    print("  Waiting 6 s for timeout to expire ...")
    time.sleep(6)

    from scoring.engine import check_and_void_timed_out_runs
    voided = check_and_void_timed_out_runs()

    ok = True
    run.refresh_from_db()
    # On a live stack the `timeout_runs` daemon is sweeping every 5 s, so it
    # usually gets there first and this call correctly returns 0 — the row lock
    # in _void_run_if_active is doing exactly its job (G3). Either voider counts;
    # what must be true is that the run ends up VOIDED.
    ok &= check(voided >= 1 or run.state == RunState.VOIDED,
                f"run voided (this call returned {voided}; "
                f"the timeout_runs daemon may have won the race)",
                f"returned {voided} and run is still {run.state}")
    ok &= check(run.state == RunState.VOIDED,
                "Run state=VOIDED",
                f"Run state={run.state} (expected VOIDED)")

    _cleanup_contest(contest)
    print()
    return ok


# ── S16 — Device Self-Registration ───────────────────────────────────────────

def s16():
    print("\n── S16: Device Self-Registration ────────────────────────────────")
    print("POST /devices/register/ → 201; a LAPTIMER lands PENDING and needs")
    print("approval, a ROBOT lands ACTIVE (Decision 041). Re-register preserves it.")
    print()

    client = Client(enforce_csrf_checks=False)
    a, b = secrets.token_hex(1).upper(), secrets.token_hex(1).upper()
    mac = f"BB:{a}:{b}:AA:BB:CC"

    # Step 1: Register a new lap timer. Timers keep the H4 approval gate — the
    # engine ignores lap events from a device an administrator has not approved,
    # so an unapproved timer would silently score nothing.
    resp = client.post(
        "/devices/register/",
        data=json.dumps({
            "mac": mac,
            "friendly_name": "Test Timer S16",
            "device_type": "LAPTIMER",
            "country": "Bulgaria",
            "school": "RoboSTEAM Lab",
        }),
        content_type="application/json",
    )

    ok = True
    ok &= check(resp.status_code == 201,
                "POST /devices/register/ → 201 Created",
                f"status={resp.status_code} (expected 201)")
    data = resp.json()
    ok &= check(data.get("registration_status") == RegistrationStatus.PENDING,
                "Lap timer response registration_status=PENDING",
                f"registration_status={data.get('registration_status')} (expected PENDING)")

    dev = LapTimerDevice.objects.get(device_id=mac)
    ok &= check(dev.registration_status == RegistrationStatus.PENDING,
                "DB: lap timer registration_status=PENDING")

    # Step 1b: A robot needs no approval (Decision 041) — it is a command
    # receiver, never a scoring source, so the worst an unapproved one can do
    # is not move. Making students wait for an organiser to tick a box would
    # be a competition-day queue for no safety gain.
    robot_mac = f"BB:{a}:{b}:AA:BB:DD"
    resp_robot = client.post(
        "/devices/register/",
        data=json.dumps({
            "mac": robot_mac,
            "friendly_name": "Test Robot S16",
            "device_type": "ROBOT",
            "country": "Bulgaria",
            "school": "RoboSTEAM Lab",
        }),
        content_type="application/json",
    )
    ok &= check(resp_robot.json().get("registration_status") == RegistrationStatus.ACTIVE,
                "Robot registers straight to ACTIVE (Decision 041)",
                f"registration_status={resp_robot.json().get('registration_status')}")
    LapTimerDevice.objects.filter(device_id=robot_mac).delete()

    # Step 2: Admin approves the timer (via ORM, as the admin bulk-action would)
    LapTimerDevice.objects.filter(pk=dev.pk).update(
        registration_status=RegistrationStatus.ACTIVE
    )
    dev.refresh_from_db()
    ok &= check(dev.registration_status == RegistrationStatus.ACTIVE,
                "After admin approval: registration_status=ACTIVE")

    # Step 3: Re-registration must update mutable fields but preserve ACTIVE status
    resp2 = client.post(
        "/devices/register/",
        data=json.dumps({
            "mac": mac,
            "friendly_name": "Test Timer S16 Renamed",
        }),
        content_type="application/json",
    )
    dev.refresh_from_db()
    ok &= check(resp2.status_code == 200,
                "Re-register → 200 (device already exists)",
                f"status={resp2.status_code} (expected 200)")
    ok &= check(dev.registration_status == RegistrationStatus.ACTIVE,
                "Re-registration preserved ACTIVE status")
    ok &= check(dev.friendly_name == "Test Timer S16 Renamed",
                "friendly_name updated to 'Test Timer S16 Renamed'",
                f"friendly_name='{dev.friendly_name}'")

    dev.delete()
    print()
    return ok


# ── S17 — MQTT Fallback / Manual Mode ────────────────────────────────────────

def s17():
    print("\n── S17: MQTT Fallback / Manual Mode ─────────────────────────────")
    print("Simulate broker disconnect → runs voided → manual results recorded.")
    print()

    contest = Contest.objects.create(name="S17 Manual Mode Test")
    timed_comp = Competition.objects.create(
        name="LF S17", contest=contest,
        competition_type=CompetitionType.TIMED,
        timeout_seconds=120, num_laps=1,
        token=_tok(),
    )
    judged_comp = Competition.objects.create(
        name="Arm S17", contest=contest,
        competition_type=CompetitionType.JUDGED,
        token=_tok(),
    )
    team = Team.objects.create(name="Alpha S17", contest=contest, token=None)

    # Create ACTIVE runs then void them (simulates broker disconnect)
    run_t = Run.objects.create(
        team=team, competition=timed_comp,
        start_time=timezone.now(), duration=0, state=RunState.ACTIVE,
    )
    run_j = Run.objects.create(
        team=team, competition=judged_comp,
        start_time=timezone.now(), duration=0, state=RunState.ACTIVE,
    )

    from scoring.engine import void_active_runs_on_disconnect, record_manual_result
    voided = void_active_runs_on_disconnect()
    run_t.refresh_from_db()
    run_j.refresh_from_db()

    ok = True
    # Phase E5: only TIMED runs depend on the broker, so only they are voided.
    ok &= check(voided == 1,
                "void_active_runs_on_disconnect() voided 1 (TIMED) run",
                f"voided {voided} run(s) (expected 1)")
    ok &= check(run_t.state == RunState.VOIDED, "TIMED run state=VOIDED")
    ok &= check(run_j.state == RunState.ACTIVE,
                "JUDGED run left ACTIVE (does not need MQTT)",
                f"JUDGED run state={run_j.state} (expected ACTIVE)")

    # The JUDGED fallback path still starts from a voided run.
    run_j.state = RunState.VOIDED
    run_j.save(update_fields=["state"])

    # Manual entry via HTTP view (as a judge would use the UI)
    client = judge_client()

    resp_t = client.post(
        f"/contest/run/{run_t.id}/manual_entry",
        {"manual_time_ms": "8500", "comment": "Stopwatch fallback"},
    )
    ok &= check(resp_t.status_code == 302,
                "TIMED manual_entry POST → 302 redirect",
                f"status={resp_t.status_code} (expected 302)")

    result_t = Result.objects.filter(team=team, competition=timed_comp).first()
    ok &= check(result_t is not None, "TIMED Result row created")
    ok &= check(result_t is not None and result_t.score == 8500,
                "TIMED Result.score=8500",
                f"Result.score={result_t.score if result_t else None}")
    ok &= check(result_t is not None and result_t.is_manual is True,
                "TIMED Result.is_manual=True",
                f"Result.is_manual={result_t.is_manual if result_t else None}")

    resp_j = client.post(
        f"/contest/run/{run_j.id}/manual_entry",
        {"score": "77", "comment": "Judge fallback"},
    )
    ok &= check(resp_j.status_code == 302,
                "JUDGED manual_entry POST → 302 redirect",
                f"status={resp_j.status_code} (expected 302)")

    result_j = Result.objects.filter(team=team, competition=judged_comp).first()
    ok &= check(result_j is not None, "JUDGED Result row created")
    ok &= check(result_j is not None and result_j.score == 77,
                "JUDGED Result.score=77",
                f"Result.score={result_j.score if result_j else None}")
    ok &= check(result_j is not None and result_j.is_manual is True,
                "JUDGED Result.is_manual=True",
                f"Result.is_manual={result_j.is_manual if result_j else None}")

    _cleanup_contest(contest)
    print()
    return ok


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="S15–S17 server feature scenario runner")
    p.add_argument("--scenario", choices=["S15", "S16", "S17"],
                   help="Run a single scenario")
    p.add_argument("--all", action="store_true", help="Run all three scenarios")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.scenario and not args.all:
        print("Specify --scenario S15|S16|S17 or --all")
        sys.exit(1)

    results = []
    if args.scenario == "S15" or args.all:
        results.append(("S15", s15()))
    if args.scenario == "S16" or args.all:
        results.append(("S16", s16()))
    if args.scenario == "S17" or args.all:
        results.append(("S17", s17()))

    print("\n── Summary ──────────────────────────────────────────────────────")
    all_pass = True
    for name, ok in results:
        marker = PASS if ok else FAIL
        print(f"  {marker}  {name}: {'PASS' if ok else 'FAIL'}")
        all_pass &= ok
    print()
    sys.exit(0 if all_pass else 1)
