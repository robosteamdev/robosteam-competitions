from django.shortcuts import get_object_or_404, redirect, render

from scoring.engine import SCORED_RUN_STATES

from .models import Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team
from .views import available_teams_for, counted_runs_by_team, judge_required


def _panel_context(competition):
    """Compute current panel state for a competition."""
    active_run = (
        Run.objects
        .filter(competition=competition, state__in=[RunState.PENDING, RunState.ACTIVE])
        .select_related('team')
        .first()
    )

    # A TIMED run finalizes by itself the moment the last lap lands, so by the
    # time the judge decides on a penalty the run is already off the panel.
    # Keep the last scored run to hand so it can still be penalised (H1).
    last_scored_run = (
        Run.objects
        .filter(competition=competition, state__in=SCORED_RUN_STATES)
        .select_related('team')
        .order_by('-start_time')
        .first()
    )

    teams           = Team.objects.filter(contest=competition.contest).order_by('name')
    run_counts      = counted_runs_by_team(competition)
    available_teams = available_teams_for(competition, teams)

    return {
        'competition':     competition,
        'active_run':      active_run,
        'last_scored_run': last_scored_run,
        'available_teams': available_teams,
        'run_counts':      run_counts,
        'all_done':        not available_teams and active_run is None,
    }


@judge_required
def judge_select(request):
    """Category selector — list of all OPEN categories across OPEN competitions."""
    categories = (
        Competition.objects
        .filter(contest__status=ItemStates.OPEN, status=ItemStates.OPEN)
        .select_related('contest')
        .order_by('contest__name', 'name')
    )
    return render(request, 'contest/judge_mobile/select.html', {'categories': categories})


@judge_required
def judge_panel(request, competition_id):
    """Full panel page for one category."""
    competition = get_object_or_404(Competition, pk=competition_id)
    return render(request, 'contest/judge_mobile/panel.html', _panel_context(competition))


@judge_required
def judge_panel_state(request, competition_id):
    """State fragment only — fetched by AJAX on WebSocket push."""
    competition = get_object_or_404(Competition, pk=competition_id)
    return render(request, 'contest/judge_mobile/state_fragment.html', _panel_context(competition))


@judge_required
def judge_score(request, competition_id, run_id):
    """Score entry screen for JUDGED runs."""
    competition = get_object_or_404(Competition, pk=competition_id)
    run         = get_object_or_404(Run, pk=run_id, competition=competition)

    if competition.competition_type != CompetitionType.JUDGED:
        return redirect('contest:judge_panel', competition_id=competition_id)
    if run.state not in (RunState.ACTIVE, RunState.PENDING):
        return redirect('contest:judge_panel', competition_id=competition_id)

    if request.method == 'POST':
        score_raw = request.POST.get('score', '')
        try:
            score = int(score_raw)
        except (ValueError, TypeError):
            return render(request, 'contest/judge_mobile/score.html', {
                'competition': competition, 'run': run, 'error': 'Invalid score.'
            })
        if not 1 <= score <= 100:
            return render(request, 'contest/judge_mobile/score.html', {
                'competition': competition, 'run': run, 'error': 'Score must be 1–100.'
            })
        comment = request.POST.get('comment', '').strip()[:200]

        from scoring.engine import MAX_PENALTY_POINTS, score_judged_run

        penalty_raw = request.POST.get('penalty_points', '').strip()
        if penalty_raw:
            try:
                penalty = int(penalty_raw)
            except (ValueError, TypeError):
                penalty = -1
            if not 0 <= penalty <= MAX_PENALTY_POINTS:
                return render(request, 'contest/judge_mobile/score.html', {
                    'competition': competition, 'run': run,
                    'error': f'Penalty must be 0–{MAX_PENALTY_POINTS}.',
                })
            # Set before scoring so the best-run comparison already sees it.
            run.penalty_points = penalty
            run.save(update_fields=['penalty_points'])

        score_judged_run(run, score, comment)
        return redirect('contest:judge_panel', competition_id=competition_id)

    return render(request, 'contest/judge_mobile/score.html', {
        'competition': competition,
        'run':         run,
    })
