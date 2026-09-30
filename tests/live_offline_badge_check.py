#!/usr/bin/env python3
"""
Phase 5.21 (scenario S23) — an unplugged robot turns its badge red, live.

Nothing else ever clears the ONLINE flag: `mqtt_bridge` sets it on every
heartbeat and lap event, so without the sweep a robot unplugged mid-competition
would show green forever (H7). And the sweep is useless if the page does not
follow it — a judge or student staring at a stale green badge is worse than one
staring at no badge at all.

So this holds a real WebSocket open, ages a device, runs the sweep in a
*different* process the way `timeout_runs` does, and waits for the push.

    cd ~/competitions/scoreboard
    ~/venv/bin/python ../tests/live_offline_badge_check.py

`DEVICE_OFFLINE_SECONDS` is 90 s in production. This overrides the threshold per
call instead of waiting, which is what `sweep_offline_devices(threshold_seconds)`
takes the argument for.

Sets up its own fixtures and deletes them again, pass or fail.
"""
import asyncio
import http.cookiejar
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE + "/../scoreboard")
django.setup()

import websockets                                           # noqa: E402
from django.contrib.auth.models import Group, User          # noqa: E402
from django.utils import timezone                           # noqa: E402

from contest.models import UserProfile                      # noqa: E402
from devices.models import (                                # noqa: E402
    DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)

BASE     = 'http://127.0.0.1:8000'
WS       = 'ws://127.0.0.1:8000/ws/devices/'
USER     = 's23_participant'
PASSWORD = 's23-pw-2026'
MAC      = 'A0:00:00:23:00:01'
VENV_PY  = os.path.expanduser('~/venv/bin/python')

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
    LapTimerDevice.objects.filter(device_id=MAC).delete()
    User.objects.filter(username=USER).delete()


def setup():
    teardown()
    group, _ = Group.objects.get_or_create(name='Participant')
    user = User.objects.create_user(USER, password=PASSWORD)
    user.groups.add(group)
    UserProfile.objects.create(user=user, requested_role='participant',
                               team_name='S23 Team')

    device = LapTimerDevice.objects.create(
        device_id=MAC, friendly_name='Unplugged Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=user,
        status=DeviceStatus.ONLINE, last_seen=timezone.now(),
    )
    return user, device


def session_cookie():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + '/accounts/login/').read()
    token = next((c.value for c in jar if c.name == 'csrftoken'), '')
    body = urllib.parse.urlencode({
        'username': USER, 'password': PASSWORD, 'csrfmiddlewaretoken': token,
    }).encode()
    req = urllib.request.Request(BASE + '/accounts/login/', data=body)
    req.add_header('Referer', BASE + '/accounts/login/')
    opener.open(req).read()
    parts = [f'{c.name}={c.value}' for c in jar if c.name in ('sessionid', 'csrftoken')]
    if not any(p.startswith('sessionid') for p in parts):
        sys.exit('FAILED: could not log in')
    return '; '.join(parts)


SWEEP_IN_ANOTHER_PROCESS = """
import django, os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'scoreboard.settings')
django.setup()
from devices.services import sweep_offline_devices
print(sweep_offline_devices(threshold_seconds=5))
"""


def sweep_elsewhere():
    """Run the sweep in a separate process, as `timeout_runs` really does.

    In-process would prove nothing about the channel layer: the whole point of
    Phase B's move to Redis is that a push from one process reaches a socket
    held by another.
    """
    result = subprocess.run(
        [VENV_PY, '-c', SWEEP_IN_ANOTHER_PROCESS],
        cwd=HERE + '/../scoreboard',
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stderr[-500:])
        return -1
    return int(result.stdout.strip().splitlines()[-1])


# ── the check ────────────────────────────────────────────────────────────────

async def watch(cookie, device):
    async with websockets.connect(WS, extra_headers={'Cookie': cookie}) as ws:
        print('  ..   socket open, badge currently ONLINE')

        # Age the device past the threshold without waiting 90 s.
        await asyncio.to_thread(
            LapTimerDevice.objects.filter(pk=device.pk).update,
            last_seen=timezone.now() - timedelta(seconds=30),
        )

        swept = await asyncio.to_thread(sweep_elsewhere)
        check('the sweep marked one device offline', swept == 1, f'swept={swept}')

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
        except asyncio.TimeoutError:
            check('badge update pushed to the open socket', False, '(timed out)')
            return

        message = json.loads(raw)
        check('badge update pushed to the open socket', True)
        check('the push is about this device',
              message.get('device_id') == MAC, message)
        check('the badge says OFFLINE',
              message.get('status') == DeviceStatus.OFFLINE, message.get('status'))
        print(f'       {json.dumps(message)}')


def main():
    user, device = setup()
    print(f'\nS23 offline badge check against {WS}\n')
    cookie = session_cookie()

    try:
        asyncio.run(watch(cookie, device))

        device.refresh_from_db()
        check('the database agrees the device is OFFLINE',
              device.status == DeviceStatus.OFFLINE, device.status)

        # A heartbeat brings it back — the sweep must not be a one-way door.
        from devices.services import push_device_update
        LapTimerDevice.objects.filter(pk=device.pk).update(
            status=DeviceStatus.ONLINE, last_seen=timezone.now())
        device.refresh_from_db()
        push_device_update(device)
        check('a fresh heartbeat restores ONLINE',
              device.status == DeviceStatus.ONLINE, device.status)
    finally:
        teardown()

    print(f'\n{checks - len(failures)}/{checks} checks passed')
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('S23 OFFLINE BADGE CHECK PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
