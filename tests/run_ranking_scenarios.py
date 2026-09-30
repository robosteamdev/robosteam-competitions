#!/usr/bin/env python3
"""
Phase 5.13 — Ranking Scenarios (S11–S13)

Tests Overall Ranking, tie-break, and DNF-exclusion against the real dev DB.
No MQTT or browser required — drives Django ORM directly.

Usage:
    cd ~/competitions/scoreboard
    source ~/venv/bin/activate
    python ../tests/run_ranking_scenarios.py --scenario S11
    python ../tests/run_ranking_scenarios.py --scenario S12
    python ../tests/run_ranking_scenarios.py --scenario S13
    python ../tests/run_ranking_scenarios.py --all
"""

import argparse
import os
import sys
import secrets

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")

import django
django.setup()

from django.utils import timezone

from contest.models import Competition, CompetitionType, Contest, Result, Run, RunState, Team
from scoring.engine import _update_best_timed_result, score_judged_run
from scoring.models import OverallResult


PASS = "✓"
FAIL = "✗"


def check(condition, msg_pass, msg_fail=""):
    if condition:
        print(f"  {PASS}  {msg_pass}")
    else:
        print(f"  {FAIL}  {msg_fail or msg_pass}")
    return condition


def _contest(name):
    return Contest.objects.create(
        name=name,
        points_table={"1": 10, "2": 8, "3": 6, "4": 4, "5": 2},
    )


def _comp(contest, name, ctype):
    return Competition.objects.create(
        name=name, contest=contest,
        competition_type=ctype,
        token=secrets.token_hex(8),
    )


def _team(contest, name):
    return Team.objects.create(name=name, contest=contest, token=None)


def _timed_run(team, comp, time_ms):
    """Complete a TIMED run with the given time_ms and update best/ranking."""
    run = Run.objects.create(
        team=team, competition=comp,
        start_time=timezone.now(), duration=0,
        state=RunState.COMPLETED, time_ms=time_ms,
    )
    _update_best_timed_result(run)
    return run


def _judged_run(team, comp, score_val):
    """Complete a JUDGED run with score_val and update best/ranking."""
    run = Run.objects.create(
        team=team, competition=comp,
        start_time=timezone.now(), duration=0, state=RunState.PENDING,
    )
    score_judged_run(run, score_val, "")
    return run


def _cleanup(contest):
    # Contest CASCADE deletes Team, Competition → Run, Result, OverallResult
    contest.delete()
    print("  (test data cleaned up)")


# ── S11 — Overall Ranking basic ───────────────────────────────────────────────

def s11():
    print("\n── S11: Overall Ranking ─────────────────────────────────────────")
    print("All 3 teams complete TIMED + JUDGED. Alpha wins both → rank 1.")
    print()

    contest = _contest("S11 Ranking Test")
    timed  = _comp(contest, "Line Following", CompetitionType.TIMED)
    judged = _comp(contest, "Object Manipulation", CompetitionType.JUDGED)
    alpha   = _team(contest, "Alpha")
    bravo   = _team(contest, "Bravo")
    charlie = _team(contest, "Charlie")

    # TIMED: lower ms = better rank
    # Alpha 1st (10pts), Bravo 2nd (8pts), Charlie 3rd (6pts)
    _timed_run(alpha,   timed, 10_000)
    _timed_run(bravo,   timed, 20_000)
    _timed_run(charlie, timed, 30_000)

    # JUDGED: higher score = better rank
    # Alpha 1st (10pts), Bravo 2nd (8pts), Charlie 3rd (6pts)
    _judged_run(alpha,   judged, 90)
    _judged_run(bravo,   judged, 80)
    _judged_run(charlie, judged, 70)

    # Expected: Alpha=20pts rank=1, Bravo=16pts rank=2, Charlie=12pts rank=3

    ors = {or_.team.name: or_ for or_ in OverallResult.objects.filter(contest=contest)}

    ok = True
    ok &= check(set(ors) == {"Alpha", "Bravo", "Charlie"},
                "OverallResult rows exist for all 3 teams")
    ok &= check(all(ors[n].is_eligible for n in ("Alpha", "Bravo", "Charlie")),
                "All 3 teams are eligible (have Results in both categories)")
    ok &= check(ors["Alpha"].total_points == 20,
                "Alpha total_points=20",
                f"Alpha total_points={ors['Alpha'].total_points} (expected 20)")
    ok &= check(ors["Bravo"].total_points == 16,
                "Bravo total_points=16",
                f"Bravo total_points={ors['Bravo'].total_points} (expected 16)")
    ok &= check(ors["Charlie"].total_points == 12,
                "Charlie total_points=12",
                f"Charlie total_points={ors['Charlie'].total_points} (expected 12)")
    ok &= check(ors["Alpha"].rank == 1,
                "Alpha rank=1", f"Alpha rank={ors['Alpha'].rank}")
    ok &= check(ors["Bravo"].rank == 2,
                "Bravo rank=2", f"Bravo rank={ors['Bravo'].rank}")
    ok &= check(ors["Charlie"].rank == 3,
                "Charlie rank=3", f"Charlie rank={ors['Charlie'].rank}")

    _cleanup(contest)
    print()
    return ok


# ── S12 — Tie-break by 1st-place count ───────────────────────────────────────

def s12():
    print("\n── S12: Tie-break by 1st-place count ───────────────────────────")
    print("Alpha and Bravo both score 26pts. Alpha has 2 first-place finishes,")
    print("Bravo has 1 → Alpha ranks higher despite equal total points.")
    print()
    print("3 TIMED categories, 3 teams:")
    print("  Cat A: Alpha 1st(10), Bravo 2nd(8),  Charlie 3rd(6)")
    print("  Cat B: Alpha 1st(10), Bravo 2nd(8),  Charlie 3rd(6)")
    print("  Cat C: Bravo 1st(10), Charlie 2nd(8), Alpha 3rd(6)")
    print("  Alpha: 10+10+6=26  Bravo: 8+8+10=26  Charlie: 6+6+8=20")
    print()

    contest = _contest("S12 Tiebreak Test")
    cat_a = _comp(contest, "Cat A", CompetitionType.TIMED)
    cat_b = _comp(contest, "Cat B", CompetitionType.TIMED)
    cat_c = _comp(contest, "Cat C", CompetitionType.TIMED)
    alpha   = _team(contest, "Alpha")
    bravo   = _team(contest, "Bravo")
    charlie = _team(contest, "Charlie")

    # Cat A: Alpha 1st, Bravo 2nd, Charlie 3rd
    _timed_run(alpha,   cat_a, 10_000)
    _timed_run(bravo,   cat_a, 20_000)
    _timed_run(charlie, cat_a, 30_000)

    # Cat B: Alpha 1st, Bravo 2nd, Charlie 3rd
    _timed_run(alpha,   cat_b, 10_000)
    _timed_run(bravo,   cat_b, 20_000)
    _timed_run(charlie, cat_b, 30_000)

    # Cat C: Bravo 1st, Charlie 2nd, Alpha 3rd
    _timed_run(bravo,   cat_c, 10_000)
    _timed_run(charlie, cat_c, 20_000)
    _timed_run(alpha,   cat_c, 30_000)

    ors = {or_.team.name: or_ for or_ in OverallResult.objects.filter(contest=contest)}

    ok = True
    ok &= check(ors["Alpha"].total_points == 26,
                "Alpha total_points=26",
                f"Alpha total_points={ors['Alpha'].total_points} (expected 26)")
    ok &= check(ors["Bravo"].total_points == 26,
                "Bravo total_points=26 (tied with Alpha)",
                f"Bravo total_points={ors['Bravo'].total_points} (expected 26)")
    ok &= check(ors["Alpha"].total_points == ors["Bravo"].total_points,
                "Alpha and Bravo are tied on total points")
    ok &= check(ors["Alpha"].rank == 1,
                "Alpha rank=1 (wins tie-break: 2 first-place finishes > 1)",
                f"Alpha rank={ors['Alpha'].rank} (expected 1)")
    ok &= check(ors["Bravo"].rank == 2,
                "Bravo rank=2 (loses tie-break: only 1 first-place finish)",
                f"Bravo rank={ors['Bravo'].rank} (expected 2)")
    ok &= check(ors["Charlie"].total_points == 20,
                "Charlie total_points=20",
                f"Charlie total_points={ors['Charlie'].total_points} (expected 20)")
    ok &= check(ors["Charlie"].rank == 3,
                "Charlie rank=3", f"Charlie rank={ors['Charlie'].rank}")

    _cleanup(contest)
    print()
    return ok


# ── S13 — DNF exclusion from Overall Ranking ─────────────────────────────────

def s13():
    print("\n── S13: DNF exclusion from Overall Ranking ─────────────────────")
    print("Charlie completes TIMED but has no JUDGED result → is_eligible=False,")
    print("excluded from overall ranking but still appears in TIMED category table.")
    print()

    contest = _contest("S13 DNF Exclusion Test")
    timed  = _comp(contest, "Line Following", CompetitionType.TIMED)
    judged = _comp(contest, "Object Manipulation", CompetitionType.JUDGED)
    alpha   = _team(contest, "Alpha")
    bravo   = _team(contest, "Bravo")
    charlie = _team(contest, "Charlie")

    # All 3 complete TIMED
    _timed_run(alpha,   timed, 10_000)   # Alpha 1st
    _timed_run(bravo,   timed, 20_000)   # Bravo 2nd
    _timed_run(charlie, timed, 30_000)   # Charlie 3rd

    # Only Alpha and Bravo complete JUDGED — Charlie deliberately skipped
    _judged_run(bravo, judged, 91)   # Bravo 1st (score 91 > 82)
    _judged_run(alpha, judged, 82)   # Alpha 2nd

    ors = {or_.team.name: or_ for or_ in OverallResult.objects.filter(contest=contest)}
    timed_results = list(
        Result.objects.filter(competition=timed).order_by("score").values_list("team__name", flat=True)
    )

    ok = True
    ok &= check("Charlie" in ors,
                "Charlie has an OverallResult row (even if ineligible)")
    ok &= check(not ors["Charlie"].is_eligible,
                "Charlie is_eligible=False (missing JUDGED result)")
    ok &= check(ors["Charlie"].rank is None,
                "Charlie rank=None (excluded from overall ranking)",
                f"Charlie rank={ors['Charlie'].rank} (expected None)")
    ok &= check(ors["Charlie"].total_points == 0,
                "Charlie total_points=0 (ineligible teams score 0)",
                f"Charlie total_points={ors['Charlie'].total_points} (expected 0)")
    ok &= check(ors["Alpha"].is_eligible and ors["Bravo"].is_eligible,
                "Alpha and Bravo are eligible (both have Results in all categories)")
    ok &= check(ors["Alpha"].rank is not None and ors["Bravo"].rank is not None,
                "Alpha and Bravo have assigned ranks")
    ok &= check("Charlie" in timed_results,
                "Charlie appears in TIMED category ranking despite being ineligible overall")

    _cleanup(contest)
    print()
    return ok


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="S11–S13 ranking scenario runner")
    p.add_argument("--scenario", choices=["S11", "S12", "S13"],
                   help="Run a single scenario")
    p.add_argument("--all", action="store_true", help="Run all three scenarios")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.scenario and not args.all:
        print("Specify --scenario S11|S12|S13 or --all")
        sys.exit(1)

    results = []
    if args.scenario == "S11" or args.all:
        results.append(("S11", s11()))
    if args.scenario == "S12" or args.all:
        results.append(("S12", s12()))
    if args.scenario == "S13" or args.all:
        results.append(("S13", s13()))

    print("\n── Summary ──────────────────────────────────────────────────────")
    all_pass = True
    for name, ok in results:
        marker = PASS if ok else FAIL
        print(f"  {marker}  {name}: {'PASS' if ok else 'FAIL'}")
        all_pass &= ok
    print()
    sys.exit(0 if all_pass else 1)
