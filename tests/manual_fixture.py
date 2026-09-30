#!/usr/bin/env python3
"""
Fixtures for the Participant/Organiser manual screenshots (Phase 5.21 H).

Creates a participant with a claimed, named, bound robot so the My Robots page
has something real to photograph, plus a lap timer awaiting approval so the
organiser device console is not empty.

    ~/venv/bin/python ../tests/manual_fixture.py setup
    ~/venv/bin/python ../tests/manual_fixture.py teardown
"""
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from django.contrib.auth.models import Group, User          # noqa: E402
from django.utils import timezone                           # noqa: E402

from contest.models import (                                # noqa: E402
    Competition, CompetitionType, Contest, ContestRegistration, ItemStates, Team,
    UserProfile,
)
from devices.models import (                                # noqa: E402
    DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)

USER      = 'demo_student'
ORGANISER = 'demo_organiser'
PASSWORD  = 'RoboSteam-demo-2026'
ROBOT    = 'A4:CF:12:B7:3D:E1'
SPARE    = 'A4:CF:12:B7:3D:E2'
TIMER    = 'A4:CF:12:B7:3D:F0'
CONTEST  = 'RoboSTEAM Spring Cup 2027'


def teardown():
    LapTimerDevice.objects.filter(device_id__in=[ROBOT, SPARE, TIMER]).delete()
    Contest.objects.filter(name=CONTEST).delete()
    User.objects.filter(username__in=[USER, ORGANISER]).delete()
    print('manual fixtures removed')


def setup():
    teardown()
    group, _ = Group.objects.get_or_create(name='Participant')
    user = User.objects.create_user(
        USER, password=PASSWORD, first_name='Demo', last_name='Student')
    user.groups.add(group)

    org_group, _ = Group.objects.get_or_create(name='Organiser')
    organiser = User.objects.create_user(
        ORGANISER, password=PASSWORD, first_name='Demo', last_name='Organiser')
    organiser.groups.add(org_group)
    UserProfile.objects.create(user=user, requested_role='participant',
                               team_name='Team Alpha')

    contest = Contest.objects.create(name=CONTEST, status=ItemStates.OPEN)
    Competition.objects.create(
        name='Line Following', contest=contest,
        competition_type=CompetitionType.TIMED, status=ItemStates.OPEN,
        num_laps=3, num_runs=3, timeout_seconds=120)

    robot = LapTimerDevice.objects.create(
        device_id=ROBOT, friendly_name='Team Alpha - PicoBot',
        device_type=DeviceType.ROBOT, registration_status=RegistrationStatus.ACTIVE,
        status=DeviceStatus.ONLINE, last_seen=timezone.now(), owner=user,
        claimed_at=timezone.now(), firmware_version='1.1.0',
        country='Bulgaria', organisation='RoboSTEAM Lab')
    LapTimerDevice.objects.create(
        device_id=SPARE, friendly_name='Team Alpha - Spare',
        device_type=DeviceType.ROBOT, registration_status=RegistrationStatus.ACTIVE,
        status=DeviceStatus.OFFLINE, last_seen=timezone.now(), owner=user,
        claimed_at=timezone.now(), firmware_version='1.1.0')
    LapTimerDevice.objects.create(
        device_id=TIMER, friendly_name='Timer-1 Hall-A',
        device_type=DeviceType.LAPTIMER,
        registration_status=RegistrationStatus.PENDING,
        status=DeviceStatus.ONLINE, last_seen=timezone.now())

    team = Team.objects.create(name='Team Alpha', contest=contest, robot=robot)
    ContestRegistration.objects.create(user=user, contest=contest, team=team)

    print(f'login: {USER} / {PASSWORD}')
    print(f'contest id={contest.id}  team id={team.id}')


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'setup'
    (setup if action == 'setup' else teardown)()
