import collections
import logging
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from devices.models import DeviceStatus
from scoring.engine import (
    SCORED_RUN_STATES, counted_runs, counted_runs_by_team, refresh_competition_state,
)

from .models import (
    Competition, CompetitionType, Contest, ItemStates, Result, Run, RunState, Team,
)

logger = logging.getLogger(__name__)


def organiser_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        u = request.user
        if u.is_superuser or u.groups.filter(name__in=['Organiser', 'Administrator']).exists():
            return view_func(request, *args, **kwargs)
        raise PermissionDenied
    return _wrapped


def user_is_judge(user):
    """True if the user may operate runs (Judge, Organiser, Administrator, or superuser).

    Shared by ``judge_required`` and the read-only board views (Phase D) so the
    'show controls' decision uses exactly the same rule as the access gate.
    """
    return bool(
        user.is_authenticated and (
            user.is_superuser
            or user.groups.filter(name__in=['Judge', 'Organiser', 'Administrator']).exists()
        )
    )


def judge_required(view_func):
    """Require login AND membership of Judge/Organiser/Administrator (or superuser).

    Anonymous users are redirected to login (302); authenticated non-judges
    (e.g. Participants) get PermissionDenied (403).
    """
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if user_is_judge(request.user):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied
    return _wrapped


def index(request):
    contests = list(Contest.objects.all())
    active_contests = filter(lambda x: x.status == ItemStates.OPEN, contests)
    past_contests   = filter(lambda x: x.status == ItemStates.CLOSED, contests)
    return render(request, 'contest/index.html', {
        'active_contests': active_contests,
        'past_contests': past_contests,
    })


def contest_competitions(request, contest_id):
    contest      = get_object_or_404(Contest, pk=contest_id)
    competitions = Competition.objects.filter(contest_id=contest_id, status__in=['OPEN', 'CLOSED'])
    teams        = Team.objects.filter(contest_id=contest_id)

    over    = collections.defaultdict(dict)
    running = collections.defaultdict(dict)

    for team in teams:
        for result in team.result_set.all():
            over[team.id][result.competition.id] = result.score

    for competition in competitions:
        if competition.status != 'OPEN':
            continue
        for result in Result.objects.filter(competition=competition):
            running[result.team_id][competition.id] = result.score

    table = []
    for team in teams:
        line = []
        for competition in competitions:
            if competition.id in over[team.id] and competition.status == 'CLOSED':
                line.append(over[team.id][competition.id])
            elif competition.id in running[team.id] and competition.status == 'OPEN':
                line.append(running[team.id][competition.id])
            else:
                line.append(None)
        table.append({'team': team, 'results': line})

    return render(request, 'contest/competitions.html', {
        'contest': contest,
        'teams': teams,
        'competitions': competitions,
        'table': table,
    })


def contest_team(request, team_id):
    team = get_object_or_404(Team, pk=team_id)
    return render(request, 'contest/teams.html', {
        'contest': team.contest,
        'team': team,
        'competitions': [],
    })


def available_teams_for(competition, teams):
    """Teams from `teams` that still have attempts left in this category."""
    used = counted_runs_by_team(competition)
    return [t for t in teams if used.get(t.id, 0) < competition.num_runs]


def _ordered_results(competition):
    """
    Final Results in ranking order: lowest time first for TIMED,
    highest score first for JUDGED.
    """
    order = 'score' if competition.competition_type == CompetitionType.TIMED else '-score'
    return (
        Result.objects.filter(competition=competition)
        .order_by(order)
        .select_related('team')
    )


def competition_board(request, competition_id):
    competition = get_object_or_404(Competition, pk=competition_id)

    results = _ordered_results(competition)
    runs    = Run.objects.filter(competition_id=competition_id).order_by('team__name', 'start_time').select_related('team')

    if competition.competition_type == CompetitionType.TIMED:
        preliminary_results = _timed_preliminary(competition_id)
    else:
        preliminary_results = _judged_preliminary(competition_id)

    teams           = Team.objects.filter(contest=competition.contest)
    available_teams = available_teams_for(competition, teams)

    return render(request, 'contest/competition_board.html', {
        'contest': competition.contest,
        'competition': competition,
        'runs': runs,
        'preliminary_results': preliminary_results,
        'results': results,
        'available_teams': available_teams,
        'show_controls': user_is_judge(request.user),
    })


def _timed_preliminary(competition_id):
    best_runs = Run.objects.filter(
        competition_id=competition_id,
        state__in=SCORED_RUN_STATES,
        is_best=True,
        time_ms__isnull=False,
    ).select_related('team')
    results = [
        {'team_id': run.team.id, 'team_name': run.team.name, 'best_time_ms': run.total_time_ms}
        for run in best_runs
    ]
    results.sort(key=lambda r: r['best_time_ms'])
    return results


def _judged_preliminary(competition_id):
    """Best effective score (judge score minus penalty) per team, highest first."""
    runs = (
        Run.objects
        .filter(competition_id=competition_id, state__in=SCORED_RUN_STATES, score__isnull=False)
        .select_related('team')
    )
    best_by_team = {}
    for run in runs:
        current = best_by_team.get(run.team_id)
        if current is None or run.effective_score > current['max_score']:
            best_by_team[run.team_id] = {
                'team_id': run.team_id,
                'team_name': run.team.name,
                'max_score': run.effective_score,
            }
    return sorted(best_by_team.values(), key=lambda r: -r['max_score'])


# ── Judge run actions ──────────────────────────────────────────────────────────

@judge_required
@require_POST
def run_create(request, competition_id):
    """Create a PENDING run for a team."""
    competition = get_object_or_404(Competition, pk=competition_id)
    if competition.status != 'OPEN':
        return JsonResponse({'error': 'Competition is not open for new runs'}, status=400)
    team_id = request.POST.get('team_id')
    team    = get_object_or_404(Team, pk=team_id, contest=competition.contest)

    if counted_runs(team, competition) >= competition.num_runs:
        return JsonResponse({'error': 'Run limit reached for this team'}, status=400)

    Run.objects.create(
        team=team,
        competition=competition,
        start_time=timezone.now(),
        duration=0,
        state=RunState.PENDING,
    )
    refresh_competition_state(competition)
    return redirect('contest:competition_board', competition_id=competition_id)


@judge_required
@require_POST
def run_discard(request, run_id):
    """
    Delete a queued run that was never started (wrong team, mis-click).

    Only PENDING runs may be discarded — anything that has been started
    carries a measurement or a void decision and must stay on the record.
    """
    run = get_object_or_404(Run.objects.select_related('competition'), pk=run_id)

    if run.state != RunState.PENDING:
        return JsonResponse(
            {'error': f'Run is {run.state}, only PENDING runs can be discarded'}, status=400,
        )

    competition = run.competition
    competition_id = competition.id
    run.delete()

    from scoring.engine import push_competition_update
    refresh_competition_state(competition)
    push_competition_update(competition_id)

    return JsonResponse({'status': 'discarded', 'run_id': run_id})


@judge_required
def run_preflight(request, run_id):
    """Pre-START check for a run: is the team's robot bound, and is it online?

    Read-only, and it **never blocks a start** (Decision, Phase 5.21): offline
    detection lags up to DEVICE_OFFLINE_SECONDS, so a hard gate would stall a
    real heat over a stale badge. The judge UI turns a warning into a confirm
    dialog and nothing more.

    The MAC is deliberately absent from the payload — judges and organisers
    work with friendly names everywhere in the UI.
    """
    run = get_object_or_404(
        Run.objects.select_related('competition', 'team', 'team__robot'), pk=run_id,
    )
    robot = run.team.robot
    warnings = []

    # Only an offline *bound* robot is worth interrupting a judge for. A team
    # with no robot is the ordinary case — organiser-created teams have no
    # participant account to self-serve with — and JUDGED categories publish no
    # MQTT at all, so a robot warning there would be pure noise.
    if (
        robot is not None
        and robot.status != DeviceStatus.ONLINE
        and run.competition.competition_type == CompetitionType.TIMED
    ):
        warnings.append(
            f'Robot "{robot.friendly_name}" for {run.team.name} is {robot.status}'
            f'{_last_seen_phrase(robot)}. It may not respond to START.'
        )

    return JsonResponse({
        'run_id':   run.id,
        'team':     run.team.name,
        'robot':    (
            {
                'friendly_name':      robot.friendly_name,
                'status':             robot.status,
                'seconds_since_seen': (
                    int((timezone.now() - robot.last_seen).total_seconds())
                    if robot.last_seen else None
                ),
            }
            if robot else None
        ),
        'warnings': warnings,
    })


def _last_seen_phrase(device):
    """' (last seen 4 min ago)' — or '' for a device that has never reported."""
    if not device.last_seen:
        return ' and has never reported in'
    seconds = int((timezone.now() - device.last_seen).total_seconds())
    if seconds < 120:
        return f' (last seen {seconds} s ago)'
    return f' (last seen {seconds // 60} min ago)'


@judge_required
@require_POST
def run_start(request, run_id):
    """Activate a PENDING run. Publishes MQTT START for TIMED competitions only."""
    run = get_object_or_404(
        Run.objects.select_related('competition', 'team', 'team__robot'), pk=run_id,
    )

    if run.state != RunState.PENDING:
        return JsonResponse({'error': f'Run is {run.state}, expected PENDING'}, status=400)

    run.state      = RunState.ACTIVE
    run.start_time = timezone.now()
    run.save(update_fields=['state', 'start_time'])

    if run.competition.competition_type == CompetitionType.TIMED:
        from mqtt_bridge.publisher import publish_run_command
        publish_run_command(run, 'START', broadcast_extra={'run_id': run.id})

    refresh_competition_state(run.competition)
    return JsonResponse({'status': 'started', 'run_id': run.id})


@judge_required
@require_POST
def run_stop(request, run_id):
    """Void an ACTIVE run. Publishes MQTT STOP for TIMED competitions only."""
    run = get_object_or_404(
        Run.objects.select_related('competition', 'team', 'team__robot'), pk=run_id,
    )

    if run.state != RunState.ACTIVE:
        return JsonResponse({'error': f'Run is {run.state}, expected ACTIVE'}, status=400)

    run.state = RunState.VOIDED
    run.save(update_fields=['state'])

    if run.competition.competition_type == CompetitionType.TIMED:
        from mqtt_bridge.publisher import publish_run_command
        publish_run_command(run, 'STOP')

    refresh_competition_state(run.competition)
    return JsonResponse({'status': 'voided', 'run_id': run.id})


@judge_required
@require_POST
def run_score(request, run_id):
    """Submit a judge's score for a JUDGED run. Marks it COMPLETED and updates best result."""
    run = get_object_or_404(Run.objects.select_related('competition'), pk=run_id)

    if run.competition.competition_type != CompetitionType.JUDGED:
        return JsonResponse({'error': 'Score entry is only for JUDGED runs'}, status=400)

    if run.state not in (RunState.PENDING, RunState.ACTIVE):
        return JsonResponse({'error': f'Run is {run.state}, cannot score'}, status=400)

    try:
        score = int(request.POST.get('score', ''))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid score value'}, status=400)

    if not 1 <= score <= 100:
        return JsonResponse({'error': 'Score must be between 1 and 100'}, status=400)

    comment = request.POST.get('comment', '').strip()[:200]

    from scoring.engine import MAX_PENALTY_POINTS, score_judged_run

    penalty_raw = request.POST.get('penalty_points', '').strip()
    penalty = 0
    if penalty_raw:
        try:
            penalty = int(penalty_raw)
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid penalty value'}, status=400)
        if not 0 <= penalty <= MAX_PENALTY_POINTS:
            return JsonResponse(
                {'error': f'Penalty must be between 0 and {MAX_PENALTY_POINTS}'}, status=400,
            )

    if penalty:
        # Set before scoring so the best-run comparison already sees the penalty.
        run.penalty_points = penalty
        run.save(update_fields=['penalty_points'])

    score_judged_run(run, score, comment)

    return redirect('contest:competition_board', competition_id=run.competition.id)


@judge_required
@require_POST
def run_penalty(request, run_id):
    """
    Set a run's penalty (H1).

    TIMED categories take `penalty_seconds` (added to the raw time), JUDGED ones
    take `penalty_points` (subtracted from the score, floored at 0). The value
    replaces any previous penalty, so 0 clears it.
    """
    run = get_object_or_404(Run.objects.select_related('competition'), pk=run_id)

    from scoring.engine import record_penalty

    try:
        if run.competition.competition_type == CompetitionType.TIMED:
            record_penalty(run, penalty_seconds=request.POST.get('penalty_seconds', '').strip() or None)
        else:
            record_penalty(run, penalty_points=request.POST.get('penalty_points', '').strip() or None)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    return JsonResponse({
        'status': 'penalty_set',
        'run_id': run.id,
        'penalty_time_ms': run.penalty_time_ms,
        'penalty_points': run.penalty_points,
    })


@judge_required
@require_POST
def manual_entry(request, run_id):
    """Submit a manually-measured result for a VOIDED run (MQTT fallback path)."""
    run = get_object_or_404(Run.objects.select_related('competition'), pk=run_id)

    if run.state != RunState.VOIDED:
        return JsonResponse({'error': f'Run is {run.state}, manual entry requires VOIDED state'}, status=400)

    comment = request.POST.get('comment', '').strip()[:200]

    from scoring.engine import record_manual_result

    try:
        if run.competition.competition_type == CompetitionType.TIMED:
            raw = request.POST.get('manual_time_ms', '').strip()
            if not raw:
                return JsonResponse({'error': 'manual_time_ms is required for TIMED runs'}, status=400)
            record_manual_result(run, time_ms=int(raw), comment=comment)
        else:
            raw = request.POST.get('score', '').strip()
            if not raw:
                return JsonResponse({'error': 'score is required for JUDGED runs'}, status=400)
            record_manual_result(run, score=int(raw), comment=comment)
    except (ValueError, TypeError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    return redirect('contest:competition_board', competition_id=run.competition_id)


# Removed in Phase F4 (H7): the upstream `robot_action` endpoint let any client
# holding a team token create and stop runs unauthenticated, bypassing the judge,
# the run limits and the hardware timer. Our robots are driven over MQTT
# (robosteam/robot/{mac}/cmd), so nothing called it.


def contest_ranking(request, contest_id):
    from scoring.models import OverallResult
    contest = get_object_or_404(Contest, pk=contest_id)
    overall_results = (
        OverallResult.objects
        .filter(contest=contest)
        .select_related('team')
        .order_by('rank', 'team__name')
    )
    return render(request, 'contest/ranking.html', {
        'contest': contest,
        'overall_results': overall_results,
    })


def competition_board_fragment(request, competition_id):
    """
    Returns the live-updating portion of the competition board (runs table +
    preliminary results) as a partial HTML snippet for XHR updates via WebSocket.
    """
    competition = get_object_or_404(Competition, pk=competition_id)

    results = _ordered_results(competition)
    runs    = Run.objects.filter(competition_id=competition_id).order_by('team__name', 'start_time').select_related('team')

    if competition.competition_type == CompetitionType.TIMED:
        preliminary_results = _timed_preliminary(competition_id)
    else:
        preliminary_results = _judged_preliminary(competition_id)

    teams           = Team.objects.filter(contest=competition.contest)
    available_teams = available_teams_for(competition, teams)

    return render(request, 'contest/board_fragment.html', {
        'contest': competition.contest,
        'competition': competition,
        'runs': runs,
        'preliminary_results': preliminary_results,
        'results': results,
        'available_teams': available_teams,
        'show_controls': user_is_judge(request.user),
    })


from django.contrib.auth import logout as auth_logout


@login_required
def account_cancel(request):
    if request.method == 'POST':
        user = request.user
        auth_logout(request)
        user.is_active = False
        user.save()
        return redirect('contest:account_cancelled')
    return render(request, 'contest/account_cancel.html')


def account_cancelled(request):
    return render(request, 'contest/account_cancelled.html')


@login_required
def post_login_redirect(request):
    """Send each role to its natural landing page after login.

    Precedence when a user is in several groups: Administrator > Organiser >
    Judge > Participant, then a safe fallback to the public contest index.
    """
    u = request.user
    groups = set(u.groups.values_list('name', flat=True))
    if u.is_superuser or 'Administrator' in groups:
        return redirect('/admin/')
    if 'Organiser' in groups:
        return redirect('contest:organise_dashboard')
    if 'Judge' in groups:
        return redirect('contest:index')
    if 'Participant' in groups:
        return redirect('contest:participate_dashboard')
    return redirect('contest:index')
