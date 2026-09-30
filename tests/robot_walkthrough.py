#!/usr/bin/env python3
"""
Phase 5.21 — participant robot flow over real HTTP against the running server.

Same reason as tests/role_walkthrough.py: the unit suite proves the logic, this
proves the deployment. All three Phase H bugs passed 397 unit tests and still
broke on the running stack, so every phase gets a live pass before it is called
done.

Sets up its own fixtures (a participant, an open contest, two robots, a rival
team), drives the real views through Daphne with real session cookies and real
CSRF, then verifies the DB and cleans up after itself.

    cd ~/competitions/scoreboard
    ~/venv/bin/python ../tests/robot_walkthrough.py
"""
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from django.contrib.auth.models import Group, User          # noqa: E402

from contest.models import (                                # noqa: E402
    Contest, ContestRegistration, ItemStates, Team, UserProfile,
)
from devices.models import (                                # noqa: E402
    DeviceType, LapTimerDevice, RegistrationStatus,
)

BASE     = 'http://127.0.0.1:8000'
USERNAME  = 'robotwalk_participant'
ORGANISER = 'robotwalk_organiser'
PASSWORD  = 'robotwalk-pw-2026'
MINE     = 'A0:00:00:CC:00:01'
RIVALS   = 'A0:00:00:CC:00:02'
TIMER    = 'A0:00:00:CC:00:03'

failures = []
checks   = 0


def check(label, condition, detail=''):
    global checks
    checks += 1
    if condition:
        print(f'  ok   {label}')
    else:
        print(f'  FAIL {label} {detail}')
        failures.append(label)


# ── fixtures ─────────────────────────────────────────────────────────────────

def teardown():
    LapTimerDevice.objects.filter(device_id__in=[MINE, RIVALS, TIMER]).delete()
    Contest.objects.filter(name='Robot Walkthrough Cup').delete()   # cascades teams
    User.objects.filter(
        username__in=[USERNAME, USERNAME + '_rival', ORGANISER]).delete()


def setup():
    teardown()

    org_group, _ = Group.objects.get_or_create(name='Organiser')
    organiser = User.objects.create_user(ORGANISER, password=PASSWORD)
    organiser.groups.add(org_group)

    group, _ = Group.objects.get_or_create(name='Participant')

    user = User.objects.create_user(USERNAME, password=PASSWORD)
    user.groups.add(group)
    UserProfile.objects.create(user=user, requested_role='participant',
                               team_name='Walkthrough Team')

    rival = User.objects.create_user(USERNAME + '_rival', password=PASSWORD)
    rival.groups.add(group)
    UserProfile.objects.create(user=rival, requested_role='participant',
                               team_name='Rival Team')

    contest = Contest.objects.create(name='Robot Walkthrough Cup', status=ItemStates.OPEN)

    team = Team.objects.create(name='Walkthrough Team', contest=contest)
    ContestRegistration.objects.create(user=user, contest=contest, team=team)

    rival_team = Team.objects.create(name='Rival Team', contest=contest)
    ContestRegistration.objects.create(user=rival, contest=contest, team=rival_team)

    LapTimerDevice.objects.create(
        device_id=MINE, friendly_name='Walkthrough Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE)
    rivals_robot = LapTimerDevice.objects.create(
        device_id=RIVALS, friendly_name='Rival Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=rival)
    LapTimerDevice.objects.create(
        device_id=TIMER, friendly_name='Walkthrough Timer',
        device_type=DeviceType.LAPTIMER)

    rival_team.robot = rivals_robot
    rival_team.save()

    return contest, team, rival_team


# ── HTTP plumbing ────────────────────────────────────────────────────────────

jar    = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path):
    with opener.open(BASE + path) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def get_landing(path):
    """Follow redirects and report where we ended up — used for the anonymous gate."""
    with opener.open(BASE + path) as r:
        return r.geturl(), r.read().decode('utf-8', 'replace')


def denied(path):
    """
    True if the caller may not have this page.

    Two shapes count: a 403 (organiser_required raises PermissionDenied) and a
    redirect somewhere else (the participant views bounce to the index).
    """
    try:
        with opener.open(BASE + path) as r:
            return not r.geturl().endswith(path)
    except urllib.error.HTTPError as e:
        return e.code in (403, 404)


def csrf():
    for c in jar:
        if c.name == 'csrftoken':
            return c.value
    return ''


def post(path, data, referer=None):
    body = urllib.parse.urlencode(data).encode()
    req  = urllib.request.Request(BASE + path, data=body)
    req.add_header('Referer', referer or (BASE + path))
    req.add_header('X-CSRFToken', csrf())
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def login(username=USERNAME):
    get('/accounts/login/')
    return post('/accounts/login/',
                {'username': username, 'password': PASSWORD,
                 'csrfmiddlewaretoken': csrf()},
                referer=BASE + '/accounts/login/')


def new_session():
    """Fresh cookie jar — used to switch from the participant to the organiser."""
    global jar, opener
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


# ── the walkthrough ──────────────────────────────────────────────────────────

def main():
    contest, team, rival_team = setup()
    print(f'\nPhase 5.21 robot walkthrough against {BASE}\n')

    landed, body = get_landing('/contest/participate/robots/')
    check('anonymous is redirected to login', '/login' in landed, f'landed on {landed}')
    check('anonymous sees no robot data', MINE not in body and RIVALS not in body)

    login()
    status, body = get('/contest/participate/robots/')
    check('participant loads My Robots', status == 200)
    check('page offers the claim form', 'Add robot' in body)

    check('an unclaimed robot is not listed', MINE not in body)
    check("another participant's robot is not listed", RIVALS not in body)
    check("another participant's robot name is not listed", 'Rival Rover' not in body)

    # claim, in the messy format a student would actually type
    post('/contest/participate/robots/claim/',
         {'mac': MINE.lower().replace(':', '-'), 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    mine = LapTimerDevice.objects.get(device_id=MINE)
    check('claim binds the robot to me', mine.owner and mine.owner.username == USERNAME)
    check('claim stamps claimed_at', mine.claimed_at is not None)

    status, body = get('/contest/participate/robots/')
    check('my robot now appears', MINE in body)

    # a lap timer must not be claimable
    post('/contest/participate/robots/claim/',
         {'mac': TIMER, 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    check('a lap timer cannot be claimed',
          LapTimerDevice.objects.get(device_id=TIMER).owner is None)

    # someone else's robot must not be claimable
    post('/contest/participate/robots/claim/',
         {'mac': RIVALS, 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    check("a claimed robot cannot be stolen",
          LapTimerDevice.objects.get(device_id=RIVALS).owner.username == USERNAME + '_rival')

    # rename
    post(f'/contest/participate/robots/{mine.id}/rename/',
         {'friendly_name': 'Renamed Rover', 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    mine.refresh_from_db()
    check('rename works from the website', mine.friendly_name == 'Renamed Rover')

    # bind to my contest
    post(f'/contest/participate/robots/bind/{contest.id}/',
         {'robot': str(mine.id), 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    team.refresh_from_db()
    check('binding my robot to my team works', team.robot_id == mine.id)

    # the rival's robot is already bound in this contest — must be refused, not 500
    status, _ = post(f'/contest/participate/robots/bind/{contest.id}/',
                     {'robot': str(LapTimerDevice.objects.get(device_id=RIVALS).id),
                      'csrfmiddlewaretoken': csrf()},
                     referer=BASE + '/contest/participate/robots/')
    team.refresh_from_db()
    check('binding a robot I do not own is refused', status in (200, 302, 404))
    check('my binding is unchanged after the refused attempt', team.robot_id == mine.id)

    # dashboard shows the robot
    status, body = get('/contest/participate/')
    check('dashboard shows the bound robot', 'Renamed Rover' in body)

    # unbind
    post(f'/contest/participate/robots/bind/{contest.id}/',
         {'robot': '', 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    team.refresh_from_db()
    check('unbinding clears the robot', team.robot_id is None)

    # a participant must not reach the organiser console
    check('participant is kept out of the device console',
          denied('/contest/organise/devices/'))

    # release drops ownership
    post(f'/contest/participate/robots/{mine.id}/release/',
         {'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/participate/robots/')
    mine.refresh_from_db()
    check('release clears ownership', mine.owner_id is None)

    # ── organiser console (section D) ────────────────────────────────────────
    print('\n  -- organiser --')
    new_session()
    login(ORGANISER)

    status, body = get('/contest/organise/devices/')
    check('organiser loads the device console', status == 200)
    check('console lists the lap timer', TIMER in body)
    check('console lists a robot', RIVALS in body)

    status, body = get('/contest/organise/devices/?filter=timers')
    check('timers filter excludes robots', TIMER in body and RIVALS not in body)

    # approve the timer — the thing that used to need Django admin
    timer = LapTimerDevice.objects.get(device_id=TIMER)
    post(f'/contest/organise/device/{timer.id}/approval/',
         {'action': 'approve', 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/organise/devices/')
    timer.refresh_from_db()
    check('organiser approves a timer without Django admin',
          timer.registration_status == RegistrationStatus.ACTIVE)

    # override a team's robot, and check it is recorded as an override
    spare = LapTimerDevice.objects.get(device_id=MINE)
    post(f'/contest/organise/team/{team.id}/robot/',
         {'robot': str(spare.id), 'csrfmiddlewaretoken': csrf()},
         referer=BASE + '/contest/organise/devices/')
    team.refresh_from_db()
    check('organiser override binds the robot', team.robot_id == spare.id)
    check('override records who did it',
          team.robot_assigned_by is not None
          and team.robot_assigned_by.username == ORGANISER)

    status, body = get(f'/contest/organise/competition/{contest.id}/readiness/')
    check('readiness page loads', status == 200)
    check('readiness names the team', 'Walkthrough Team' in body)

    teardown()

    print(f'\n{checks - len(failures)}/{checks} checks passed')
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('ALL CHECKS PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
