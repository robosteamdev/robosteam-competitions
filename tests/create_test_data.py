#!/usr/bin/env python3
"""
Phase 5.5 test fixture setup.
Creates (or verifies) the test data for scenarios S1-S10.
Safe to run multiple times (uses get_or_create throughout).

Run from the Django project root:
    cd ~/competitions/scoreboard
    source ~/venv/bin/activate
    python ../tests/create_test_data.py
"""

import os
import sys
import django

# Point Django at the scoreboard settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")

# The script must be run from ~/competitions/scoreboard (manage.py directory)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

import secrets
from contest.models import Contest, Competition, Team


def make_token():
    return secrets.token_hex(16)


def run():
    print("=" * 55)
    print("  Phase 5.5 — Test Fixture Setup")
    print("=" * 55)

    # ------------------------------------------------------------------
    # Contest
    # ------------------------------------------------------------------
    contest, created = Contest.objects.get_or_create(
        name="RoboACADEMY Test Cup 2026",
        defaults={
            "description": "Phase 5.5 end-to-end test competition",
            "status": "OPEN",
            "points_table": {"1": 10, "2": 8, "3": 6, "4": 4, "5": 2},
        },
    )
    tag = "CREATED" if created else "EXISTS"
    print(f"[{tag}] Contest id={contest.id}: {contest.name}")

    # ------------------------------------------------------------------
    # Category 1 — Line Following (TIMED)
    # ------------------------------------------------------------------
    lf, created = Competition.objects.get_or_create(
        name="Line Following",
        contest=contest,
        defaults={
            "description": "Hardware-timed lap run — lowest time wins",
            "status": "OPEN",
            "competition_type": "TIMED",
            "num_laps": 3,
            "num_runs": 3,
            "timeout_seconds": 120,
            "token": make_token(),
        },
    )
    tag = "CREATED" if created else "EXISTS"
    print(f"[{tag}] Category id={lf.id}: {lf.name} "
          f"(type={lf.competition_type}, num_laps={lf.num_laps}, num_runs={lf.num_runs})")
    if not created and lf.competition_type != "TIMED":
        lf.competition_type = "TIMED"
        lf.num_laps = 3
        lf.num_runs = 3
        lf.timeout_seconds = 120
        lf.save()
        print(f"  -> Updated to TIMED / num_laps=3 / num_runs=3")

    # ------------------------------------------------------------------
    # Category 2 — Object Manipulation (JUDGED)
    # ------------------------------------------------------------------
    om, created = Competition.objects.get_or_create(
        name="Object Manipulation",
        contest=contest,
        defaults={
            "description": "Judge-scored arm task — highest score wins",
            "status": "OPEN",
            "competition_type": "JUDGED",
            "num_laps": 1,
            "num_runs": 3,
            "timeout_seconds": 300,
            "token": make_token(),
        },
    )
    tag = "CREATED" if created else "EXISTS"
    print(f"[{tag}] Category id={om.id}: {om.name} "
          f"(type={om.competition_type}, num_runs={om.num_runs})")

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------
    team_names = ["Team Alpha", "Team Bravo", "Team Charlie"]
    teams = {}
    for name in team_names:
        team, created = Team.objects.get_or_create(
            name=name,
            contest=contest,
            defaults={"description": f"Test team — {name}", "token": make_token()},
        )
        tag = "CREATED" if created else "EXISTS"
        print(f"[{tag}] Team id={team.id}: {team.name}")
        teams[name] = team

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("Test fixture ready. Before running S1:")
    print(f"  - Line Following Category id = {lf.id}")
    print(f"  - Object Manipulation Category id = {om.id}")
    print(f"  - Contest id = {contest.id}")
    print()
    print("Next: start mqtt_bridge, start simulator, wait for heartbeat (S1).")
    print("After S1: set lap_timer on Line Following to the registered device.")
    print()
    print(f"Board URLs:")
    print(f"  http://localhost:8000/contest/competition_board/{lf.id}/")
    print(f"  http://localhost:8000/contest/competition_board/{om.id}/")
    print(f"  http://localhost:8000/contest/contest/{contest.id}/")


if __name__ == "__main__":
    run()
