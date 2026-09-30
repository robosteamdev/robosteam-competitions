#!/usr/bin/env python3
"""
Phase 5.21 G probe — what a re-registration can do to a CLAIMED robot.

/devices/register/ is unauthenticated by design (DEVICE_REG_SECRET is optional)
because a device has to be able to introduce itself. Section A added type-based
approval to it. This asks what happens when a claimed, team-bound robot
re-registers as a LAPTIMER — which needs no attacker at all, just a student
flashing lap timer firmware onto the wrong board.
"""
import json
import os
import sys
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

BASE = 'http://127.0.0.1:8000'
MAC  = 'D0:0D:00:00:00:99'
USER = 'reregprobe_owner'


def teardown():
    LapTimerDevice.objects.filter(device_id=MAC).delete()
    Contest.objects.filter(name='Rereg Probe Cup').delete()
    User.objects.filter(username=USER).delete()


def setup():
    teardown()
    group, _ = Group.objects.get_or_create(name='Participant')
    user = User.objects.create_user(USER, password='x')
    user.groups.add(group)
    UserProfile.objects.create(user=user, requested_role='participant',
                               team_name='Probe Team')

    robot = LapTimerDevice.objects.create(
        device_id=MAC, friendly_name='Claimed Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=user)

    contest = Contest.objects.create(name='Rereg Probe Cup', status=ItemStates.OPEN)
    team = Team.objects.create(name='Probe Team', contest=contest, robot=robot)
    ContestRegistration.objects.create(user=user, contest=contest, team=team)
    return user, robot, team


def post_register(payload):
    req = urllib.request.Request(
        BASE + '/devices/register/', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read().decode())


def main():
    user, robot, team = setup()
    print(f'before: type={robot.device_type} owner={robot.owner.username} '
          f'status={robot.registration_status} bound_to={team.name}\n')

    status, body = post_register({
        'mac': MAC,
        'friendly_name': 'Suddenly A Timer',
        'device_type': 'LAPTIMER',
    })
    print(f'unauthenticated re-registration as LAPTIMER -> HTTP {status}')

    robot.refresh_from_db()
    team.refresh_from_db()
    print(f'after:  type={robot.device_type} '
          f'owner={robot.owner.username if robot.owner else None} '
          f'status={robot.registration_status} '
          f'still_bound_to={team.robot_id == robot.pk}\n')

    problems = 0
    if robot.device_type != DeviceType.ROBOT:
        # _my_robot() filters device_type=ROBOT, so the owner can no longer
        # rename, release or test it, and it vanishes from My Robots.
        still_owned = robot.owner_id == user.pk
        reachable = LapTimerDevice.objects.filter(
            pk=robot.pk, owner=user, device_type=DeviceType.ROBOT).exists()
        print(f'  owner field kept:            {still_owned}')
        print(f'  reachable via _my_robot():   {reachable}')
        print(f'  team binding still in place: {team.robot_id == robot.pk}')
        if still_owned and not reachable:
            print('\nFINDING: the robot is stranded — still owned and still bound,')
            print('         but invisible and unreachable in the participant UI.')
            print('         Only Django admin can undo it.')
            problems = 1

    teardown()
    return problems


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        teardown()
        raise
