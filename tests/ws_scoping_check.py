#!/usr/bin/env python3
"""
Phase 5.21 G probe — can a participant enumerate other people's hardware?

The devices WebSocket accepts any authenticated user and pushes every device
update to one group. Claiming is MAC-only and first-claimer-wins (Decision
042), so a MAC *is* the security boundary — the claim form deliberately never
lists or suggests unclaimed MACs. This asks whether the socket gives them away
anyway.

Sets up a participant who owns nothing, a rival's robot and an unclaimed robot,
connects as the participant, triggers a heartbeat for each, and reports what
arrived.

    cd ~/competitions/scoreboard
    ~/venv/bin/python ../tests/../scratch_ws_leak_probe.py
"""
import asyncio
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

import websockets                                           # noqa: E402
from django.contrib.auth.models import Group, User          # noqa: E402

from contest.models import UserProfile                      # noqa: E402
from devices.models import (                                # noqa: E402
    DeviceType, LapTimerDevice, RegistrationStatus,
)

BASE     = 'http://127.0.0.1:8000'
WS       = 'ws://127.0.0.1:8000/ws/devices/'
SNOOP    = 'gprobe_snooper'
RIVAL    = 'gprobe_rival'
PASSWORD = 'gprobe-pw-2026'

RIVALS_MAC   = 'C0:FF:EE:00:00:01'
UNCLAIMED_MAC = 'C0:FF:EE:00:00:02'
TIMER_MAC    = 'C0:FF:EE:00:00:03'


def teardown():
    LapTimerDevice.objects.filter(
        device_id__in=[RIVALS_MAC, UNCLAIMED_MAC, TIMER_MAC]).delete()
    User.objects.filter(username__in=[SNOOP, RIVAL]).delete()


def setup():
    teardown()
    group, _ = Group.objects.get_or_create(name='Participant')

    snoop = User.objects.create_user(SNOOP, password=PASSWORD)
    snoop.groups.add(group)
    UserProfile.objects.create(user=snoop, requested_role='participant',
                               team_name='Snooper Team')

    rival = User.objects.create_user(RIVAL, password=PASSWORD)
    rival.groups.add(group)
    UserProfile.objects.create(user=rival, requested_role='participant',
                               team_name='Rival Team')

    LapTimerDevice.objects.create(
        device_id=RIVALS_MAC, friendly_name='Rivals Secret Rover',
        device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=rival)
    LapTimerDevice.objects.create(
        device_id=UNCLAIMED_MAC, friendly_name='Nobodys Rover',
        device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE)
    LapTimerDevice.objects.create(
        device_id=TIMER_MAC, friendly_name='Venue Timer',
        device_type=DeviceType.LAPTIMER,
        registration_status=RegistrationStatus.ACTIVE)
    print('fixtures: snooper owns NOTHING; rival owns one robot; '
          'one robot unclaimed; one lap timer')


def session_cookie():
    """Log in as the snooper over real HTTP and return the sessionid cookie."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + '/accounts/login/').read()
    csrf = next((c.value for c in jar if c.name == 'csrftoken'), '')
    body = urllib.parse.urlencode({
        'username': SNOOP, 'password': PASSWORD, 'csrfmiddlewaretoken': csrf,
    }).encode()
    req = urllib.request.Request(BASE + '/accounts/login/', data=body)
    req.add_header('Referer', BASE + '/accounts/login/')
    opener.open(req).read()
    parts = [f'{c.name}={c.value}' for c in jar if c.name in ('sessionid', 'csrftoken')]
    if not any(p.startswith('sessionid') for p in parts):
        sys.exit('FAILED: could not log in as the snooper')
    return '; '.join(parts)


async def probe(cookie):
    """Hold the socket open while every device heartbeats; report what arrives."""
    received = []
    async with websockets.connect(WS, extra_headers={'Cookie': cookie}) as ws:
        print('snooper socket: CONNECTED as a plain participant\n')

        # Push each device the way mqtt_bridge would on a heartbeat.
        def beat():
            from devices.services import push_device_update
            for mac in (RIVALS_MAC, UNCLAIMED_MAC, TIMER_MAC):
                push_device_update(LapTimerDevice.objects.get(device_id=mac))

        await asyncio.to_thread(beat)

        for _ in range(3):
            try:
                received.append(json.loads(
                    await asyncio.wait_for(ws.recv(), timeout=4)))
            except asyncio.TimeoutError:
                break
    return received


def main():
    setup()
    cookie = session_cookie()
    received = asyncio.run(probe(cookie))

    leaked = {r.get('device_id'): r.get('friendly_name') for r in received}
    for mac, name in leaked.items():
        print(f'  RECEIVED  {mac}  "{name}"')
    if not leaked:
        print('  (nothing received)')

    print()
    verdict = 0
    if RIVALS_MAC in leaked:
        print("LEAK: the snooper learned a RIVAL PARTICIPANT's robot MAC.")
        verdict = 1
    if UNCLAIMED_MAC in leaked:
        print('LEAK: the snooper learned an UNCLAIMED robot MAC — which is the '
              'entire\n      credential needed to claim it (Decision 042).')
        verdict = 1
    if TIMER_MAC in leaked:
        print('LEAK: the snooper learned a LAP TIMER MAC — organiser-only '
              'infrastructure\n      that must never appear in the participant UI.')
        verdict = 1
    if verdict == 0:
        print('No leak: the participant received nothing they do not own.')

    teardown()
    return verdict


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        teardown()
        raise
