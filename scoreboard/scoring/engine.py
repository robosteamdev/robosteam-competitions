import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from contest.models import (
    Competition, CompetitionState, CompetitionType, ItemStates, Result, Run, RunState, Team,
)
from devices.models import LapEvent, RegistrationStatus

logger = logging.getLogger(__name__)


def crossings_needed(run):
    """Number of beam crossings to complete a run: start crossing + N lap completions."""
    return run.competition.num_laps + 1


def assign_lap_event_to_active_run(lap_event):
    """
    Find the ACTIVE Run whose competition has this device as lap_timer.
    Assigns the LapEvent to that run and competition. Returns the Run or None.

    Only devices an administrator has approved (registration_status ACTIVE) may
    score a run (H4). Events from PENDING/INACTIVE devices stay in the database
    unassigned, so the audit trail is complete and they can be reviewed, but they
    never advance or finalize a run.
    """
    device = lap_event.device

    if device.registration_status != RegistrationStatus.ACTIVE:
        logger.warning(
            'LapEvent %s ignored for scoring: device %s is %s, not ACTIVE',
            lap_event.pk, device.device_id, device.registration_status,
        )
        return None

    try:
        run = Run.objects.select_related('competition').get(
            competition__lap_timer=device,
            state=RunState.ACTIVE,
        )
    except Run.DoesNotExist:
        return None
    except Run.MultipleObjectsReturned:
        return None

    lap_event.competition = run.competition
    lap_event.run = run
    lap_event.save(update_fields=['competition', 'run'])
    return run


def try_finalize_run(run):
    """
    If enough crossings have been recorded, calculate time_ms, mark COMPLETED,
    and update the best result. Returns True if the run was finalized.
    """
    events = list(LapEvent.objects.filter(run=run).order_by('timestamp_utc'))
    needed = crossings_needed(run)
    if len(events) < needed:
        return False

    start_ts  = events[0].timestamp_utc
    finish_ts = events[needed - 1].timestamp_utc
    elapsed_ms = int((finish_ts - start_ts).total_seconds() * 1000)

    # Take the row lock before committing to COMPLETED. The last lap event and
    # the timeout deadline can land within milliseconds of each other, and they
    # are handled by two different processes (mqtt_bridge vs timeout_runs), so
    # without this a run can be finalized and then voided — or voided and then
    # finalized, resurrecting a run the judge already saw fail. (G3)
    with transaction.atomic():
        locked = Run.objects.select_for_update().get(pk=run.pk)
        if locked.state != RunState.ACTIVE:
            logger.info(
                'Run %s reached %s crossings but is already %s — not finalizing',
                run.pk, needed, locked.state,
            )
            return False

        locked.time_ms = elapsed_ms
        locked.state   = RunState.COMPLETED
        locked.save(update_fields=['time_ms', 'state'])

        for i, ev in enumerate(events[:needed]):
            ev.lap_number = i
        LapEvent.objects.bulk_update(events[:needed], ['lap_number'])

    # Keep the caller's instance in step with the row we just wrote.
    run.time_ms = elapsed_ms
    run.state   = RunState.COMPLETED

    # Tell the robot the run is over. Nothing did this before Phase 5.21 E: the
    # run finalized on the finish-line crossing and the robot kept driving until
    # a judge voided something or the firmware gave up on its own.
    # Targeted only, no broadcast: the lap timer needs no instruction — it just
    # published the crossing that ended the run — and adding a broadcast STOP to
    # a code path that has never sent one would change what every already-flashed
    # timer sees at finalize time.
    from mqtt_bridge.publisher import publish_run_command
    publish_run_command(run, 'STOP', broadcast=False, reason='finished')

    _update_best_timed_result(run)
    refresh_competition_state(run.competition)
    push_competition_update(run.competition_id)
    return True


def score_judged_run(run, score, comment=''):
    """Record a judge's score for a JUDGED run and update best result."""
    run.score         = score
    run.judge_comment = comment
    run.state         = RunState.COMPLETED
    run.save(update_fields=['score', 'judge_comment', 'state'])
    _update_best_judged_result(run)
    refresh_competition_state(run.competition)
    push_competition_update(run.competition_id)


def check_and_void_timed_out_runs():
    """
    Find every ACTIVE TIMED run whose start_time + timeout_seconds has passed
    and void it. Returns the count of runs voided.
    """
    now = timezone.now()
    active_timed = list(
        Run.objects.filter(
            state=RunState.ACTIVE,
            competition__competition_type=CompetitionType.TIMED,
        ).select_related('competition')
    )
    count = 0
    for run in active_timed:
        deadline = run.start_time + timedelta(seconds=run.competition.timeout_seconds)
        if now >= deadline and _void_timed_out_run(run):
            count += 1
    return count


def _void_run_if_active(run):
    """Flip an ACTIVE run to VOIDED under a row lock. False if it already moved on.

    Both voiders (timeout sweep, broker disconnect) select their candidates and
    then write them one by one, so a run can finalize on its last lap event in
    between — in a different process. A COMPLETED run must keep its time. (G3)
    """
    with transaction.atomic():
        locked = Run.objects.select_for_update().get(pk=run.pk)
        if locked.state != RunState.ACTIVE:
            logger.info(
                'Run %s is already %s — not voiding it', run.pk, locked.state,
            )
            return False
        locked.state = RunState.VOIDED
        locked.save(update_fields=['state'])

    run.state = RunState.VOIDED
    return True


def _void_timed_out_run(run):
    """Void a single timed-out ACTIVE run and notify via MQTT + WebSocket.

    Returns True if this call is the one that voided it.
    """
    if not _void_run_if_active(run):
        return False

    from mqtt_bridge.publisher import publish_run_command
    publish_run_command(run, 'STOP', reason='timeout')

    refresh_competition_state(run.competition)
    push_competition_update(run.competition_id)
    return True


# ── Internal helpers ──────────────────────────────────────────────────────────

#: Run states that may hold a valid measurement and so compete for "best run".
#: MANUAL is a run whose value was entered by hand after the run was voided.
SCORED_RUN_STATES = (RunState.COMPLETED, RunState.MANUAL)


def _update_best_timed_result(run):
    """Mark is_best on the fastest scored TIMED run, upsert Result, refresh overall ranking."""
    scored = list(
        Run.objects.filter(
            team=run.team,
            competition=run.competition,
            state__in=SCORED_RUN_STATES,
            time_ms__isnull=False,
        )
    )
    Run.objects.filter(team=run.team, competition=run.competition).update(is_best=False)

    best = min(scored, key=lambda r: r.total_time_ms, default=None)
    if best:
        best.is_best = True
        best.save(update_fields=['is_best'])
        total = best.total_time_ms
        Result.objects.update_or_create(
            team=run.team,
            competition=run.competition,
            defaults={
                'score': total,
                'is_manual': best.is_manual,
                'comment': best.judge_comment,
            },
        )

    _update_overall_ranking(run.competition)


def _update_best_judged_result(run):
    """Mark is_best on the highest-scoring scored JUDGED run, upsert Result, refresh overall ranking."""
    scored = list(
        Run.objects.filter(
            team=run.team,
            competition=run.competition,
            state__in=SCORED_RUN_STATES,
            score__isnull=False,
        )
    )
    Run.objects.filter(team=run.team, competition=run.competition).update(is_best=False)

    best = max(scored, key=lambda r: r.effective_score, default=None)
    if best:
        best.is_best = True
        best.save(update_fields=['is_best'])
        Result.objects.update_or_create(
            team=run.team,
            competition=run.competition,
            defaults={
                'score': best.effective_score,
                'is_manual': best.is_manual,
                'comment': best.judge_comment,
            },
        )

    _update_overall_ranking(run.competition)


def _lookup_points(points_map, position):
    """Return points for a given position from the contest's points_map."""
    key = str(position)
    if key in points_map:
        return int(points_map[key])
    if 'default' in points_map:
        return int(points_map['default'])
    return 1


def _update_overall_ranking(competition):
    """Recalculate OverallResult for every team in this competition's contest."""
    from scoring.models import OverallResult

    contest = competition.contest
    # NOTE (C7, decision 2026-07-07): soft-deleted categories must not gate eligibility.
    # NEW (draft) categories deliberately still count — adding a draft category mid-event
    # makes every team ineligible until it has results. To be confirmed by the project team.
    all_categories = list(
        Competition.objects.filter(contest=contest).exclude(status=ItemStates.DELETED)
    )
    teams = list(Team.objects.filter(contest=contest))

    if not all_categories or not teams:
        return

    points_map = contest.points_table or {}

    # Build ordered team_id list per category (rank order)
    category_rankings = {}
    for cat in all_categories:
        if cat.competition_type == CompetitionType.TIMED:
            ordered = list(
                Result.objects.filter(competition=cat)
                .order_by('score')
                .values_list('team_id', flat=True)
            )
        else:
            ordered = list(
                Result.objects.filter(competition=cat)
                .order_by('-score')
                .values_list('team_id', flat=True)
            )
        category_rankings[cat.id] = ordered

    max_positions = len(teams)
    team_data = {}
    for team in teams:
        is_eligible = all(team.id in category_rankings[cat.id] for cat in all_categories)

        total_points = 0
        position_counts = [0] * max_positions

        if is_eligible:
            for cat in all_categories:
                ranking = category_rankings[cat.id]
                try:
                    pos = ranking.index(team.id) + 1  # 1-based
                    total_points += _lookup_points(points_map, pos)
                    if pos <= max_positions:
                        position_counts[pos - 1] += 1
                except ValueError:
                    pass

        team_data[team.id] = {
            'total_points': total_points,
            'is_eligible': is_eligible,
            'position_counts': position_counts,
        }

    # Upsert all OverallResult rows (rank=None until assigned below)
    for team in teams:
        data = team_data[team.id]
        OverallResult.objects.update_or_create(
            contest=contest,
            team=team,
            defaults={
                'total_points': data['total_points'],
                'is_eligible': data['is_eligible'],
                'rank': None,
            },
        )

    # Sort eligible teams: most total_points first; tie-break by most 1st-place finishes, then 2nd, etc.
    eligible = [t for t in teams if team_data[t.id]['is_eligible']]
    eligible.sort(key=lambda t: (
        -team_data[t.id]['total_points'],
        *[-c for c in team_data[t.id]['position_counts']],
    ))

    for rank, team in enumerate(eligible, start=1):
        OverallResult.objects.filter(contest=contest, team=team).update(rank=rank)


def push_competition_update(competition_id):
    """Push a WebSocket update to all clients watching this competition board."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        channel_layer = get_channel_layer()
        if channel_layer is not None:
            async_to_sync(channel_layer.group_send)(
                f'competition_{competition_id}',
                {'type': 'competition.update', 'competition_id': competition_id},
            )
    except Exception:
        logger.exception(
            'WebSocket push failed for competition %s', competition_id,
        )


# Keep old name as alias so existing callers (signals) still work
_update_best_result = _update_best_timed_result


# ── Run-limit accounting ──────────────────────────────────────────────────────

#: Run states that consume one of a team's `num_runs` attempts.
#: A VOIDED run does not (decision 2026-07-07) — the team gets the attempt back.
#: Recording a manual measurement moves the run VOIDED -> MANUAL, which does count.
COUNTED_RUN_STATES = (
    RunState.PENDING, RunState.ACTIVE, RunState.COMPLETED, RunState.MANUAL,
)


def counted_runs(team, competition):
    """How many of its allowed attempts a team has used in this category."""
    return Run.objects.filter(
        team=team, competition=competition, state__in=COUNTED_RUN_STATES,
    ).count()


def counted_runs_by_team(competition):
    """
    {team_id: attempts used} for every team with runs in this category.

    One aggregate query, so callers listing teams don't issue a COUNT per team.
    """
    from django.db.models import Count
    return dict(
        Run.objects
        .filter(competition=competition, state__in=COUNTED_RUN_STATES)
        .values('team_id')
        .annotate(used=Count('id'))
        .values_list('team_id', 'used')
    )


def attempts_remain(competition):
    """True if any team in the contest still has an attempt left in this category."""
    used = counted_runs_by_team(competition)
    team_ids = Team.objects.filter(contest=competition.contest).values_list('id', flat=True)
    return any(used.get(team_id, 0) < competition.num_runs for team_id in team_ids)


# ── Competition state machine (H11) ───────────────────────────────────────────

def refresh_competition_state(competition):
    """
    Recompute Competition.state from the category's runs and save it if changed.

    WAITING  — no runs yet
    READY    — a run is queued, or attempts are still available
    RUNNING  — a run is in progress
    FINISHED — the category is CLOSED, or every team has used every attempt

    Returns the (possibly unchanged) state and pushes a WebSocket update on
    every transition.
    """
    runs = Run.objects.filter(competition=competition)

    if competition.status == ItemStates.CLOSED:
        new_state = CompetitionState.FINISHED
    elif runs.filter(state=RunState.ACTIVE).exists():
        new_state = CompetitionState.RUNNING
    elif runs.filter(state=RunState.PENDING).exists():
        new_state = CompetitionState.READY
    elif not runs.exists():
        new_state = CompetitionState.WAITING
    elif attempts_remain(competition):
        new_state = CompetitionState.READY
    else:
        new_state = CompetitionState.FINISHED

    if new_state != competition.state:
        competition.state = new_state
        competition.save(update_fields=['state'])
        push_competition_update(competition.id)

    return new_state


# ── Phase 5.10: MQTT fallback helpers ─────────────────────────────────────────

def void_active_runs_on_disconnect():
    """
    Void every ACTIVE **TIMED** run when the MQTT broker disconnects.

    Only TIMED runs depend on the broker for their timing source; a JUDGED run
    is scored by hand and must survive a broker outage untouched.
    Called from the mqtt_bridge on_disconnect callback.
    Returns the count of runs voided.

    **Nothing is published here, targeted or broadcast** (Phase 5.21 E). The
    broker is the thing that just went away, so a STOP cannot reach anyone —
    and `publish.single` would sit in a connect attempt per voided run, inside
    the on_disconnect callback, delaying the reconnect that is the only way
    back. The robot's own broker-loss handling has to stop it; the judge falls
    back to Manual Mode (Phase 5.10) either way.
    """
    active_runs = list(
        Run.objects.filter(
            state=RunState.ACTIVE,
            competition__competition_type=CompetitionType.TIMED,
        ).select_related('competition')
    )
    count = 0
    for run in active_runs:
        if not _void_run_if_active(run):
            continue
        refresh_competition_state(run.competition)
        push_competition_update(run.competition_id)
        count += 1
    return count


# ── Phase F5: penalties (H1) ──────────────────────────────────────────────────

#: Run states a judge may attach a penalty to. A PENDING run hasn't happened yet
#: and a VOIDED one carries no measurement, so neither can earn one.
PENALISABLE_RUN_STATES = (RunState.ACTIVE, RunState.COMPLETED, RunState.MANUAL)

#: Upper bound on a JUDGED penalty — scores run 1–100, so anything above 100
#: is a typo rather than a very bad run.
MAX_PENALTY_POINTS = 100

#: Upper bound on a TIMED penalty, in seconds. Generous, but catches a judge
#: typing milliseconds into the seconds box.
MAX_PENALTY_SECONDS = 3600


def record_penalty(run, penalty_seconds=None, penalty_points=None):
    """
    Set a run's penalty and recalculate the team's best run and the ranking.

    TIMED runs take `penalty_seconds` (decimal seconds, stored as
    `penalty_time_ms` and added to the raw time). JUDGED runs take
    `penalty_points`, subtracted from the judge's score with a floor of 0.

    The penalty replaces any previous value rather than accumulating, so a judge
    can correct a mis-typed penalty by entering the right one. Passing 0 clears it.
    Raises ValueError on invalid input or a run state that cannot be penalised.
    """
    if run.state not in PENALISABLE_RUN_STATES:
        raise ValueError(f'a {run.state} run cannot be penalised')

    is_timed = run.competition.competition_type == CompetitionType.TIMED

    if is_timed:
        if penalty_seconds is None:
            raise ValueError('penalty_seconds is required for TIMED runs')
        try:
            seconds = float(penalty_seconds)
        except (TypeError, ValueError):
            raise ValueError('penalty must be a number of seconds')
        if seconds < 0:
            raise ValueError('penalty cannot be negative')
        if seconds > MAX_PENALTY_SECONDS:
            raise ValueError(f'penalty cannot exceed {MAX_PENALTY_SECONDS} seconds')
        run.penalty_time_ms = int(round(seconds * 1000))
        run.save(update_fields=['penalty_time_ms'])
    else:
        if penalty_points is None:
            raise ValueError('penalty_points is required for JUDGED runs')
        try:
            points = int(penalty_points)
        except (TypeError, ValueError):
            raise ValueError('penalty must be a whole number of points')
        if points < 0:
            raise ValueError('penalty cannot be negative')
        if points > MAX_PENALTY_POINTS:
            raise ValueError(f'penalty cannot exceed {MAX_PENALTY_POINTS} points')
        run.penalty_points = points
        run.save(update_fields=['penalty_points'])

    # An ACTIVE run has no measurement yet — the penalty is stored and will be
    # applied when the run finalizes. Anything scored re-competes for best run now.
    if run.state in SCORED_RUN_STATES:
        if is_timed:
            _update_best_timed_result(run)
        else:
            _update_best_judged_result(run)

    push_competition_update(run.competition_id)
    return run


def record_manual_result(run, time_ms=None, score=None, comment=''):
    """
    Record a manually-entered measurement on a VOIDED run (MQTT-fallback path).

    The value is stored on the Run itself (state VOIDED -> MANUAL) and the
    team's Result is then recalculated by the normal best-run logic, so a
    manual entry can no longer overwrite a better automated result.

    For TIMED competitions: pass time_ms (positive int, milliseconds).
    For JUDGED competitions: pass score (int, 1-100).
    Raises ValueError on invalid input or wrong run state.
    Returns the team's current Result for this category (may be an earlier,
    better run).
    """
    if run.state != RunState.VOIDED:
        raise ValueError(f'manual_result only allowed on VOIDED runs (run is {run.state})')

    is_timed = run.competition.competition_type == CompetitionType.TIMED
    if is_timed:
        if time_ms is None:
            raise ValueError('time_ms is required for TIMED runs')
        time_ms = int(time_ms)
        if time_ms <= 0:
            raise ValueError('time_ms must be a positive integer')
        run.time_ms = time_ms
    else:
        if score is None:
            raise ValueError('score is required for JUDGED runs')
        score = int(score)
        if not (1 <= score <= 100):
            raise ValueError('score must be between 1 and 100')
        run.score = score

    run.judge_comment = (comment or '')[:200]
    run.is_manual     = True
    run.state         = RunState.MANUAL
    run.save(update_fields=['time_ms', 'score', 'judge_comment', 'is_manual', 'state'])

    if is_timed:
        _update_best_timed_result(run)
    else:
        _update_best_judged_result(run)

    refresh_competition_state(run.competition)
    push_competition_update(run.competition_id)
    return Result.objects.filter(team=run.team, competition=run.competition).first()
