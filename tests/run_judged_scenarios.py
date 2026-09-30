#!/usr/bin/env python3
"""
Phase 5.5 S7 — JUDGED competition (Object Manipulation) driver.

Submits scores for all 9 judged runs (3 teams × 3 runs each).
No MQTT simulator needed for JUDGED runs.

Run from scoreboard directory:
    cd ~/competitions/scoreboard && source ~/venv/bin/activate
    python ../tests/run_judged_scenarios.py
"""

import json
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from django.test import Client

from _judge_client import judge_client
from contest.models import Competition, Run, RunState, Result, Team


COMPETITION_ID = 5  # Object Manipulation (JUDGED)

# Fixture: (team_name, [scores per run])
FIXTURE = [
    ("Team Alpha",   [75, 82, 68]),
    ("Team Bravo",   [91, 78, 85]),
    ("Team Charlie", [60, 70, 45]),
]

# Expected best scores
EXPECTED_BEST = {
    "Team Alpha":   82,
    "Team Bravo":   91,
    "Team Charlie": 70,
}

EXPECTED_RANK = ["Team Bravo", "Team Alpha", "Team Charlie"]


def _fixture_teams(competition):
    """The fixture team rows, oldest first — participant self-registration can
    create a same-named team, so never use .get() here."""
    teams = {}
    for team_name, _ in FIXTURE:
        teams[team_name] = (
            Team.objects.filter(name=team_name, contest=competition.contest)
            .order_by('id').first()
        )
        if teams[team_name] is None:
            sys.exit(f"ERROR: Team '{team_name}' not found in test contest.")
    return teams


def reset(competition, teams):
    """Delete this scenario's own runs and results so the driver can re-run."""
    for team in teams.values():
        Run.objects.filter(team=team, competition=competition).delete()
        Result.objects.filter(team=team, competition=competition).delete()
    print("  (previous S7 runs and results cleared)")
    print()


def main(do_reset=False):
    client = judge_client()
    competition = Competition.objects.get(id=COMPETITION_ID)
    print(f"Category: {competition.name} (id={competition.id}, type={competition.competition_type})")
    print()

    teams = _fixture_teams(competition)
    if do_reset:
        reset(competition, teams)

    errors = []

    for team_name, scores in FIXTURE:
        team = teams[team_name]
        print(f"--- {team_name} ---")

        for run_num, score in enumerate(scores, start=1):
            # Create PENDING run
            resp = client.post(
                f"/contest/competition/{COMPETITION_ID}/run/create",
                {"team_id": team.id},
            )
            if resp.status_code not in (200, 302):
                err = f"run_create HTTP {resp.status_code} for {team_name} run {run_num}"
                print(f"  ERROR: {err}")
                errors.append(err)
                continue

            new_run = (
                Run.objects.filter(team=team, competition=competition, state=RunState.PENDING)
                .order_by("-id").first()
            )

            # For run 2, test scoring from ACTIVE state (click Start first)
            # For runs 1 and 3, score from PENDING (no Start click)
            if run_num == 2:
                resp = client.post(f"/contest/run/{new_run.id}/start")
                new_run.refresh_from_db()
                state_before = new_run.state
            else:
                state_before = RunState.PENDING

            # Submit score
            resp = client.post(
                f"/contest/run/{new_run.id}/score",
                {"score": score, "comment": f"S7 test score"},
            )
            # run_score returns redirect (302) to the board on success
            if resp.status_code not in (200, 302):
                err = f"run_score HTTP {resp.status_code} for {team_name} run {run_num}"
                print(f"  ERROR: {err} — {resp.content[:200]}")
                errors.append(err)
                continue

            new_run.refresh_from_db()
            print(f"  Run {run_num}: score={score}  state_before_score={state_before}  "
                  f"run.state={new_run.state}  run.score={new_run.score}  "
                  f"is_best={new_run.is_best}")

        # Verify Result
        try:
            result = Result.objects.get(team=team, competition=competition)
            expected = EXPECTED_BEST[team_name]
            ok = result.score == expected
            mark = "✓" if ok else "✗"
            print(f"  Result.score={result.score}  (expected={expected})  {mark}")
            if not ok:
                errors.append(f"{team_name}: Result.score={result.score} ≠ {expected}")
        except Result.DoesNotExist:
            print(f"  Result: NOT FOUND  ✗")
            errors.append(f"{team_name}: Result not found")

        # Verify exactly one is_best
        best_runs = Run.objects.filter(
            team=team, competition=competition, is_best=True
        )
        best_count = best_runs.count()
        mark = "✓" if best_count == 1 else "✗"
        print(f"  is_best runs: {best_count}  {mark}")
        if best_count != 1:
            errors.append(f"{team_name}: {best_count} runs have is_best=True")
        else:
            best = best_runs.first()
            print(f"  Best run: id={best.id} score={best.score}")

        print()

    # S7d: Verify MQTT bridge was silent (no START/STOP for JUDGED)
    print("--- S7d: MQTT bridge guard check ---")
    print("  Checking bridge log for unexpected START/STOP on JUDGED runs ...")
    import subprocess
    result = subprocess.run(
        ["grep", "-c", "Publishing.*START", "/tmp/mqtt_bridge.log"],
        capture_output=True, text=True
    )
    # If log doesn't exist or no matches, that's correct
    start_count = int(result.stdout.strip()) if result.returncode == 0 else 0
    print(f"  START publishes in bridge log: {start_count}")
    # Note: these STARTs may all be from TIMED runs (S2-S6), not JUDGED
    # The check is that no new STARTs appear after S6c
    print("  (Review manually: all STARTs should be from TIMED runs S2-S6c only)")

    # Board ranking check
    print()
    print("--- Ranking check ---")
    results = list(
        Result.objects.filter(competition=competition)
        .select_related("team")
        .order_by("-score")
    )
    for rank, r in enumerate(results, 1):
        expected_team = EXPECTED_RANK[rank - 1] if rank <= len(EXPECTED_RANK) else "?"
        ok = r.team.name == expected_team
        mark = "✓" if ok else "✗"
        print(f"  #{rank}: {r.team.name} (score={r.score})  {mark}")

    print()
    if errors:
        print(f"S7 FAILED — {len(errors)} error(s):")
        for e in errors:
            print(f"  ✗ {e}")
    else:
        print("S7 PASSED ✓")


if __name__ == "__main__":
    # --reset clears the runs/results this scenario created last time, so the
    # driver can be re-run against the same fixture contest.
    main(do_reset="--reset" in sys.argv)
