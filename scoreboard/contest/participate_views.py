import logging
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from devices.models import DeviceType, LapTimerDevice
from devices.services import PING_TIMEOUT_SECONDS, device_status_payload, ping_device

from .models import Contest, ContestRegistration, ItemStates, Run, Team

logger = logging.getLogger(__name__)

#: Claim attempts allowed per user per window. Claiming is MAC-only (Decision 042),
#: so this is what stops someone walking the address space of a vendor prefix.
CLAIM_MAX_ATTEMPTS = 10
CLAIM_WINDOW_SECONDS = 300


def normalise_mac(raw):
    """
    Return a canonical AA:BB:CC:DD:EE:FF string, or None if it isn't a MAC.

    Students read these off a sticker or a serial log, so accept the forms they
    actually type — colons, dashes, dots or nothing at all, any case.
    """
    hex_only = re.sub(r'[^0-9A-Fa-f]', '', str(raw or ''))
    if len(hex_only) != 12:
        return None
    return ':'.join(hex_only[i:i + 2] for i in range(0, 12, 2)).upper()


def _claim_rate_limited(user):
    """True once the user has spent their claim attempts for the current window."""
    key = f'robot-claim-attempts:{user.pk}'
    attempts = cache.get(key, 0)
    if attempts >= CLAIM_MAX_ATTEMPTS:
        return True
    # add() only sets the key if absent, so the window starts at the first attempt
    # and does not slide forward with every try.
    cache.add(key, 0, CLAIM_WINDOW_SECONDS)
    try:
        cache.incr(key)
    except ValueError:      # key expired between add() and incr()
        cache.set(key, 1, CLAIM_WINDOW_SECONDS)
    return False


def _require_participant(request):
    return (
        request.user.is_authenticated
        and request.user.groups.filter(name='Participant').exists()
    )


@login_required
def participate_dashboard(request):
    if not _require_participant(request):
        return redirect('contest:index')

    profile    = getattr(request.user, 'profile', None)
    my_regs    = (
        ContestRegistration.objects
        .filter(user=request.user)
        .select_related('contest', 'team', 'team__robot')
        .order_by('-registered_at')
    )
    joined_ids    = set(my_regs.values_list('contest_id', flat=True))
    open_contests = (
        Contest.objects
        .filter(status=ItemStates.OPEN)
        .exclude(id__in=joined_ids)
        .order_by('name')
    )
    return render(request, 'contest/participate/dashboard.html', {
        'profile':       profile,
        'my_regs':       my_regs,
        'open_contests': open_contests,
    })


@login_required
def participate_join(request, contest_id):
    if not _require_participant(request):
        return redirect('contest:index')
    if request.method != 'POST':
        return redirect('contest:participate_dashboard')

    contest   = get_object_or_404(Contest, pk=contest_id, status=ItemStates.OPEN)
    profile   = getattr(request.user, 'profile', None)
    team_name = (profile.team_name if profile and profile.team_name
                 else request.user.username)

    # Idempotent — do nothing if already registered
    if not ContestRegistration.objects.filter(user=request.user, contest=contest).exists():
        # token comes from the model default (contest.models._new_token)
        team = Team.objects.create(name=team_name, contest=contest)
        ContestRegistration.objects.create(
            user=request.user, contest=contest, team=team
        )
    return redirect('contest:participate_dashboard')


@login_required
def participate_leave(request, contest_id):
    if not _require_participant(request):
        return redirect('contest:index')
    if request.method != 'POST':
        return redirect('contest:participate_dashboard')

    contest = get_object_or_404(Contest, pk=contest_id)
    reg = ContestRegistration.objects.filter(
        user=request.user, contest=contest
    ).select_related('team').first()

    if reg:
        if Run.objects.filter(team=reg.team).exists():
            # Deleting the team would cascade away its runs and results (E7/H6).
            messages.error(
                request,
                'Your team has already competed — contact the organiser to withdraw.',
            )
        elif contest.status == ItemStates.OPEN:
            reg.team.delete()   # cascades to ContestRegistration via OneToOne
    return redirect('contest:participate_dashboard')


# ──────────────────────────────────────────────────────────────────────────────
# My Robots (Phase 5.21 section B)
#
# Robots are team-owned and team-maintained (Decision 040): everything here is
# self-service and an organiser is never in the loop. Every view is scoped to
# request.user's own devices — another participant's MAC is never rendered.
# ──────────────────────────────────────────────────────────────────────────────

def _my_robot(request, robot_id):
    """Fetch one of the caller's own robots, or 404. Never widen this lookup."""
    return get_object_or_404(
        LapTimerDevice, pk=robot_id, owner=request.user, device_type=DeviceType.ROBOT
    )


@login_required
def robots_list(request):
    if not _require_participant(request):
        return redirect('contest:index')

    my_robots = (
        LapTimerDevice.objects
        .filter(owner=request.user, device_type=DeviceType.ROBOT)
        .order_by('friendly_name')
    )
    my_regs = (
        ContestRegistration.objects
        .filter(user=request.user)
        .select_related('contest', 'team', 'team__robot')
        .order_by('contest__name')
    )
    return render(request, 'contest/participate/robots.html', {
        'my_robots': my_robots,
        'my_regs':   my_regs,
    })


@login_required
def robot_claim(request):
    if not _require_participant(request):
        return redirect('contest:index')
    if request.method != 'POST':
        return redirect('contest:robots_list')

    raw = request.POST.get('mac', '')
    mac = normalise_mac(raw)

    if _claim_rate_limited(request.user):
        logger.warning('Claim rate limit hit by user %s', request.user.username)
        messages.error(
            request,
            'Too many claim attempts. Wait a few minutes and try again.',
        )
        return redirect('contest:robots_list')

    if mac is None:
        messages.error(
            request,
            'That is not a MAC address. It should be 12 hex characters, '
            'for example AA:BB:CC:DD:EE:01.',
        )
        return redirect('contest:robots_list')

    device = LapTimerDevice.objects.filter(device_id=mac).first()
    if device is None:
        # Deliberately specific: the student already knows the MAC (they typed it),
        # and "has it ever connected?" is the first thing they need to debug.
        messages.error(
            request,
            f'No device with address {mac} has connected to the server yet. '
            'Power the robot up and check its WiFi and broker settings first.',
        )
        return redirect('contest:robots_list')

    try:
        device.claim(request.user)
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
        return redirect('contest:robots_list')

    logger.info('Robot %s claimed by %s', mac, request.user.username)
    messages.success(request, f'{device.friendly_name} is now yours.')
    return redirect('contest:robots_list')


@login_required
def robot_rename(request, robot_id):
    if not _require_participant(request):
        return redirect('contest:index')
    if request.method != 'POST':
        return redirect('contest:robots_list')

    robot = _my_robot(request, robot_id)
    name  = str(request.POST.get('friendly_name', '')).strip()
    if not name:
        messages.error(request, 'The robot needs a name.')
    else:
        robot.friendly_name = name[:100]
        robot.save(update_fields=['friendly_name'])
        messages.success(request, f'Renamed to {robot.friendly_name}.')
    return redirect('contest:robots_list')


@login_required
def robot_release(request, robot_id):
    if not _require_participant(request):
        return redirect('contest:index')
    if request.method != 'POST':
        return redirect('contest:robots_list')

    robot = _my_robot(request, robot_id)
    name  = robot.friendly_name
    robot.release()
    logger.info('Robot %s released by %s', robot.device_id, request.user.username)
    messages.success(
        request,
        f'{name} released. It is unbound from your teams and anyone can claim it now.',
    )
    return redirect('contest:robots_list')


@login_required
def robot_test(request, robot_id):
    """
    Start a connection test and return immediately.

    The PONG is delivered to `mqtt_bridge`, not here, so this cannot wait for a
    result — it publishes, and the browser watches the devices WebSocket for the
    reply, falling back to heartbeat freshness if none arrives.
    """
    if not _require_participant(request):
        return JsonResponse({'error': 'forbidden'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    robot = _my_robot(request, robot_id)
    try:
        ping = ping_device(robot, requested_by=request.user)
    except Exception:
        # A broker that is down is the single most likely reason a student is on
        # this page at all. Say so plainly instead of returning a 500.
        logger.exception('PING publish failed for %s', robot.device_id)
        return JsonResponse(
            {'error': 'Could not reach the competition broker. Tell an organiser.'},
            status=503,
        )

    return JsonResponse({
        'nonce':           ping.nonce,
        'device_id':       robot.device_id,
        'timeout_seconds': PING_TIMEOUT_SECONDS,
    })


@login_required
def robot_status(request, robot_id):
    """Heartbeat freshness for one of my robots — the fallback when no PONG arrives."""
    if not _require_participant(request):
        return JsonResponse({'error': 'forbidden'}, status=403)
    return JsonResponse(device_status_payload(_my_robot(request, robot_id)))


@login_required
def robot_bind(request, contest_id):
    """Choose which of my robots competes in one contest I have joined."""
    if not _require_participant(request):
        return redirect('contest:index')
    if request.method != 'POST':
        return redirect('contest:robots_list')

    reg = get_object_or_404(
        ContestRegistration.objects.select_related('team'),
        user=request.user, contest_id=contest_id,
    )

    robot_id = str(request.POST.get('robot', '')).strip()
    if not robot_id:
        reg.team.robot = None
        reg.team.save(update_fields=['robot'])
        messages.success(request, f'No robot set for {reg.contest.name}.')
        return redirect('contest:robots_list')

    robot = _my_robot(request, robot_id)
    reg.team.robot = robot
    try:
        with transaction.atomic():
            reg.team.save(update_fields=['robot'])
    except IntegrityError:
        # one_robot_per_team_per_contest — reachable when an organiser has bound
        # this robot to another team in the same event (the override path).
        messages.error(
            request,
            f'{robot.friendly_name} is already assigned to another team in '
            f'{reg.contest.name}. Ask the organiser to sort it out.',
        )
        return redirect('contest:robots_list')

    messages.success(
        request, f'{robot.friendly_name} will compete for you in {reg.contest.name}.'
    )
    return redirect('contest:robots_list')
