import logging

from django import forms
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from devices.models import (
    DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
    validate_device_approved,
)
from devices.services import offline_threshold_seconds, push_device_update
from scoring.engine import refresh_competition_state

from .models import (
    Competition, CompetitionType, Contest, ContestRegistration, ItemStates, Team,
)
from .views import organiser_required

logger = logging.getLogger(__name__)


@organiser_required
def organise_dashboard(request):
    contests = Contest.objects.exclude(status=ItemStates.DELETED).order_by('-id')
    return render(request, 'contest/organise/dashboard.html', {'contests': contests})


class ContestForm(forms.ModelForm):
    class Meta:
        model  = Contest
        fields = ['name', 'description', 'status']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


@organiser_required
def organise_contest_new(request):
    form = ContestForm(request.POST or None)
    if form.is_valid():
        contest = form.save()
        return redirect('contest:organise_contest', contest_id=contest.id)
    return render(request, 'contest/organise/contest_form.html', {'form': form, 'action': 'Create'})


@organiser_required
def organise_contest(request, contest_id):
    contest       = get_object_or_404(Contest, pk=contest_id)
    categories    = Competition.objects.filter(contest=contest)
    teams         = Team.objects.filter(contest=contest)
    registrations = (
        ContestRegistration.objects
        .filter(contest=contest)
        .select_related('user', 'team')
        .order_by('registered_at')
    )
    return render(request, 'contest/organise/contest_detail.html', {
        'contest': contest, 'categories': categories, 'teams': teams,
        'registrations': registrations,
    })


@organiser_required
def organise_contest_edit(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    form    = ContestForm(request.POST or None, instance=contest)
    if form.is_valid():
        form.save()
        return redirect('contest:organise_contest', contest_id=contest.id)
    return render(request, 'contest/organise/contest_form.html', {'form': form, 'action': 'Edit', 'contest': contest})


class TeamForm(forms.ModelForm):
    class Meta:
        model  = Team
        fields = ['name', 'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}


@organiser_required
def organise_team_new(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    form    = TeamForm(request.POST or None)
    if form.is_valid():
        team = form.save(commit=False)
        team.contest = contest
        team.save()
        return redirect('contest:organise_contest', contest_id=contest.id)
    return render(request, 'contest/organise/team_form.html', {
        'form': form, 'contest': contest, 'action': 'Add',
    })


@organiser_required
def organise_team_edit(request, team_id):
    team = get_object_or_404(Team, pk=team_id)
    form = TeamForm(request.POST or None, instance=team)
    if form.is_valid():
        form.save()
        return redirect('contest:organise_contest', contest_id=team.contest.id)
    return render(request, 'contest/organise/team_form.html', {
        'form': form, 'contest': team.contest, 'action': 'Edit',
    })


@organiser_required
def organise_team_delete(request, team_id):
    team = get_object_or_404(Team, pk=team_id)
    contest_id = team.contest.id
    if request.method == 'POST':
        team.delete()
        return redirect('contest:organise_contest', contest_id=contest_id)
    return render(request, 'contest/organise/confirm_delete.html', {
        'object': team, 'back_url': f'/contest/organise/competition/{contest_id}/',
    })


class CategoryForm(forms.ModelForm):
    class Meta:
        model  = Competition
        fields = ['name', 'description', 'competition_type', 'status',
                  'num_runs', 'num_laps', 'timeout_seconds', 'lap_timer']
        widgets = {'description': forms.Textarea(attrs={'rows': 2})}

    def clean_lap_timer(self):
        # An unapproved timer scores nothing (H4) — refuse it here rather than
        # let the organiser discover it mid-run.
        return validate_device_approved(self.cleaned_data.get('lap_timer'))


@organiser_required
def organise_category_new(request, contest_id):
    contest = get_object_or_404(Contest, pk=contest_id)
    form    = CategoryForm(request.POST or None)
    if form.is_valid():
        cat = form.save(commit=False)
        cat.contest = contest
        cat.save()   # token comes from the model default (contest.models._new_token)
        return redirect('contest:organise_contest', contest_id=contest.id)
    return render(request, 'contest/organise/category_form.html', {
        'form': form, 'contest': contest, 'action': 'Add',
    })


@organiser_required
def organise_category_edit(request, category_id):
    cat  = get_object_or_404(Competition, pk=category_id)
    form = CategoryForm(request.POST or None, instance=cat)
    if form.is_valid():
        cat = form.save()
        # Closing a category (or changing its run limit) changes its live state.
        refresh_competition_state(cat)
        return redirect('contest:organise_contest', contest_id=cat.contest.id)
    return render(request, 'contest/organise/category_form.html', {
        'form': form, 'contest': cat.contest, 'action': 'Edit',
    })


# ──────────────────────────────────────────────────────────────────────────────
# Device console (Phase 5.21 section D)
#
# Robots are team-owned and team-maintained (Decision 040), so this is a
# visibility and exception-handling surface, not the place robots are assigned.
# What genuinely lives here: lap timer approval (H4), and the override for the
# two cases a team cannot solve itself.
# ──────────────────────────────────────────────────────────────────────────────

@organiser_required
def organise_devices(request):
    devices = (
        LapTimerDevice.objects
        .select_related('owner')
        .prefetch_related('teams__contest')
        .order_by('device_type', 'friendly_name')
    )

    device_filter = request.GET.get('filter', '')
    if device_filter == 'pending':
        devices = devices.filter(registration_status=RegistrationStatus.PENDING)
    elif device_filter == 'offline':
        devices = devices.exclude(status=DeviceStatus.ONLINE)
    elif device_filter == 'robots':
        devices = devices.filter(device_type=DeviceType.ROBOT)
    elif device_filter == 'timers':
        devices = devices.filter(device_type=DeviceType.LAPTIMER)

    return render(request, 'contest/organise/devices.html', {
        'devices':         devices,
        'device_filter':   device_filter,
        'pending_count':   LapTimerDevice.objects.filter(
                               registration_status=RegistrationStatus.PENDING).count(),
        'offline_seconds': offline_threshold_seconds(),
    })


@organiser_required
def organise_device_approval(request, device_id):
    """
    Approve or deactivate a device — the one thing that previously required
    Django admin, which organisers do not have.
    """
    if request.method != 'POST':
        return redirect('contest:organise_devices')

    device = get_object_or_404(LapTimerDevice, pk=device_id)
    action = request.POST.get('action', '')

    if action == 'approve':
        device.registration_status = RegistrationStatus.ACTIVE
    elif action == 'deactivate':
        device.registration_status = RegistrationStatus.INACTIVE
    else:
        messages.error(request, 'Unknown action.')
        return redirect('contest:organise_devices')

    device.save(update_fields=['registration_status'])
    logger.info('Device %s set to %s by %s',
                device.device_id, device.registration_status, request.user.username)
    push_device_update(device)
    messages.success(
        request,
        f'{device.friendly_name} is now {device.get_registration_status_display()}.',
    )
    return redirect('contest:organise_devices')


@organiser_required
def organise_team_robot(request, team_id):
    """
    Override a team's robot binding.

    The safety valve from Decision 040, not the normal path: a robot dies at the
    table and a spare goes in while the judge waits, or the team was created by an
    organiser and has no account to self-serve with. Always recorded, so a binding
    that a team did not choose can be told apart from one they did.
    """
    if request.method != 'POST':
        return redirect('contest:organise_devices')

    team     = get_object_or_404(Team.objects.select_related('contest'), pk=team_id)
    robot_id = str(request.POST.get('robot', '')).strip()

    if not robot_id:
        previous = team.robot
        team.robot             = None
        team.robot_assigned_by = request.user
        team.robot_assigned_at = timezone.now()
        team.save(update_fields=['robot', 'robot_assigned_by', 'robot_assigned_at'])
        # Unbinding is as much an override as binding: it silently stops a
        # team's robot receiving targeted START. Log it the same way (G).
        logger.warning('Robot %s cleared from team %s by %s (override)',
                       previous.device_id if previous else '(none)',
                       team.name, request.user.username)
        messages.success(request, f'Robot cleared for {team.name}.')
        return redirect(request.POST.get('next') or 'contest:organise_devices')

    robot = get_object_or_404(
        LapTimerDevice, pk=robot_id, device_type=DeviceType.ROBOT)

    team.robot             = robot
    team.robot_assigned_by = request.user
    team.robot_assigned_at = timezone.now()
    try:
        with transaction.atomic():
            team.save(update_fields=['robot', 'robot_assigned_by', 'robot_assigned_at'])
    except IntegrityError:
        messages.error(
            request,
            f'{robot.friendly_name} is already assigned to another team in '
            f'{team.contest.name}. Free it there first.',
        )
        return redirect(request.POST.get('next') or 'contest:organise_devices')

    logger.warning('Robot %s assigned to team %s by %s (override)',
                   robot.device_id, team.name, request.user.username)
    messages.success(request, f'{robot.friendly_name} assigned to {team.name}.')
    return redirect(request.POST.get('next') or 'contest:organise_devices')


@organiser_required
def organise_readiness(request, contest_id):
    """
    Pre-flight check for one competition: is the hardware actually ready?

    Answers in one screen what a judge otherwise discovers when a heat fails —
    an unapproved or offline timer, or a team whose robot is missing or dead.
    """
    contest    = get_object_or_404(Contest, pk=contest_id)
    categories = (
        Competition.objects
        .filter(contest=contest)
        .exclude(status=ItemStates.DELETED)
        .select_related('lap_timer')
        .order_by('name')
    )
    teams = (
        Team.objects
        .filter(contest=contest)
        .select_related('robot')
        .order_by('name')
    )

    rows = []
    for cat in categories:
        timer    = cat.lap_timer
        is_timed = cat.competition_type == CompetitionType.TIMED
        problems = []
        if is_timed:
            if timer is None:
                problems.append('No lap timer assigned — this category cannot score.')
            else:
                if timer.registration_status != RegistrationStatus.ACTIVE:
                    problems.append(
                        f'Timer {timer.friendly_name} is '
                        f'{timer.get_registration_status_display()} and will score nothing.')
                if timer.status != DeviceStatus.ONLINE:
                    problems.append(f'Timer {timer.friendly_name} is offline.')
        rows.append({'category': cat, 'timer': timer,
                     'is_timed': is_timed, 'problems': problems})

    team_rows = [{
        'team':    team,
        'robot':   team.robot,
        'problem': (
            'No robot bound' if team.robot is None
            else ('Robot offline' if team.robot.status != DeviceStatus.ONLINE else '')
        ),
    } for team in teams]

    return render(request, 'contest/organise/readiness.html', {
        'contest':      contest,
        'rows':         rows,
        'team_rows':    team_rows,
        'all_robots':   LapTimerDevice.objects.filter(
                            device_type=DeviceType.ROBOT).order_by('friendly_name'),
        'ready':        not any(r['problems'] for r in rows)
                        and not any(t['problem'] for t in team_rows),
    })
