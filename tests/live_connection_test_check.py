#!/usr/bin/env python3
"""
Phase 5.21 (scenario S22) — the connection test, both halves.

A student presses "Test connection" on My Robots. Two things can happen and
both must be useful:

  * firmware that answers PING replies with a PONG and the page shows a real
    round-trip time;
  * firmware that predates PING/PONG never replies, and the page falls back to
    heartbeat freshness — "last seen 3 s ago" is still a useful answer, and is
    the *normal* path for anything not yet reflashed.

Nothing here is mocked. This drives the real participant endpoints over HTTP
with a real session, against the real broker, with `tests/robot_sim.py`
providing the robot.

Needs a simulator per half, started by this script:

    cd ~/competitions/scoreboard
    ~/venv/bin/python ../tests/live_connection_test_check.py

Sets up its own fixtures and deletes them again, pass or fail.
"""
import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE + "/../scoreboard")
django.setup()

from django.conf import settings                            # noqa: E402
from django.contrib.auth.models import Group, User          # noqa: E402

from contest.models import UserProfile                      # noqa: E402
from devices.models import (                                # noqa: E402
    DevicePing, DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)
from devices.services import PING_TIMEOUT_SECONDS           # noqa: E402

BASE     = 'http://127.0.0.1:8000'
USER     = 's22_participant'
PASSWORD = 's22-pw-2026'
TALKER   = 'A0:00:00:22:00:01'     # answers PING
MUTE     = 'A0:00:00:22:00:02'     # never answers, but does heartbeat

VENV_PY  = os.path.expanduser('~/venv/bin/python')
SIM      = HERE + '/robot_sim.py'

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
    LapTimerDevice.objects.filter(device_id__in=[TALKER, MUTE]).delete()
    User.objects.filter(username=USER).delete()


def setup():
    teardown()
    group, _ = Group.objects.get_or_create(name='Participant')
    user = User.objects.create_user(USER, password=PASSWORD)
    user.groups.add(group)
    UserProfile.objects.create(user=user, requested_role='participant',
                               team_name='S22 Team')

    talker = LapTimerDevice.objects.create(
        device_id=TALKER, friendly_name='Chatty Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=user)
    mute = LapTimerDevice.objects.create(
        device_id=MUTE, friendly_name='Old Firmware Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=user)
    return talker, mute


def start_sim(mac, *extra):
    """Launch robot_sim.py in its own process group so it can be killed cleanly."""
    proc = subprocess.Popen(
        [VENV_PY, '-u', SIM, '--mac', mac,
         '--broker', settings.MQTT_BROKER_HOST,
         '--port', str(settings.MQTT_BROKER_PORT),
         '--user', settings.MQTT_USERNAME,
         '--password', settings.MQTT_PASSWORD, *extra],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(4)   # connect, subscribe, first heartbeat
    return proc


def stop_sim(proc):
    if proc and proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=10)


# ── HTTP plumbing ────────────────────────────────────────────────────────────

jar    = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def csrf():
    return next((c.value for c in jar if c.name == 'csrftoken'), '')


def get(path):
    with opener.open(BASE + path) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    req  = urllib.request.Request(BASE + path, data=body)
    req.add_header('Referer', BASE + path)
    req.add_header('X-CSRFToken', csrf())
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')


def login():
    get('/accounts/login/')
    return post('/accounts/login/',
                {'username': USER, 'password': PASSWORD,
                 'csrfmiddlewaretoken': csrf()})


def await_pong(nonce, seconds):
    """Poll the DB the way the browser polls the WebSocket. Returns rtt or None."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        ping = DevicePing.objects.filter(nonce=nonce).first()
        if ping and ping.rtt_ms is not None:
            return ping.rtt_ms
        time.sleep(0.25)
    return None


# ── the check ────────────────────────────────────────────────────────────────

def main():
    talker, mute = setup()
    sim = None
    print(f'\nS22 connection test against {BASE}')
    print(f'broker {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}\n')

    try:
        login()
        status, body = get('/contest/participate/robots/')
        check('participant loads My Robots', status == 200)
        check('own robots are listed', 'Chatty Rover' in body)

        # ── half 1: firmware that answers ────────────────────────────────────
        print('\n-- firmware WITH PING/PONG --')
        sim = start_sim(TALKER)

        status, body = post(f'/contest/participate/robots/{talker.id}/test/',
                            {'csrfmiddlewaretoken': csrf()})
        check('test connection accepted', status == 200, body[:200])
        nonce = json.loads(body).get('nonce') if status == 200 else None
        check('server returns a nonce to watch for', bool(nonce))

        rtt = await_pong(nonce, PING_TIMEOUT_SECONDS) if nonce else None
        check('PONG arrives inside the timeout', rtt is not None,
              f'no reply in {PING_TIMEOUT_SECONDS}s')
        check('round-trip time is plausible',
              rtt is not None and 0 <= rtt < 2000, f'rtt={rtt}ms')
        if rtt is not None:
            print(f'       round trip: {rtt} ms')

        talker.refresh_from_db()
        check('device is marked ONLINE', talker.status == DeviceStatus.ONLINE)
        check('firmware version captured', bool(talker.firmware_version),
              repr(talker.firmware_version))
        stop_sim(sim)
        sim = None

        # ── half 2: firmware that never answers ──────────────────────────────
        print('\n-- firmware WITHOUT PING/PONG (--no-pong) --')
        sim = start_sim(MUTE, '--no-pong')

        status, body = post(f'/contest/participate/robots/{mute.id}/test/',
                            {'csrfmiddlewaretoken': csrf()})
        check('test connection still accepted', status == 200, body[:200])
        nonce = json.loads(body).get('nonce') if status == 200 else None

        rtt = await_pong(nonce, PING_TIMEOUT_SECONDS) if nonce else None
        check('no PONG arrives, as expected', rtt is None, f'rtt={rtt}')

        # The fallback the browser uses when the timeout expires.
        status, body = get(f'/contest/participate/robots/{mute.id}/status/')
        payload = json.loads(body)
        check('status endpoint answers', status == 200)
        check('heartbeat freshness is reported',
              payload.get('seconds_since_seen') is not None, payload)
        check('the heartbeat is recent, so the verdict is "healthy"',
              payload.get('seconds_since_seen') is not None
              and payload['seconds_since_seen'] < 60,
              payload.get('seconds_since_seen'))
        check('device is ONLINE despite never answering a PING',
              payload.get('status') == DeviceStatus.ONLINE, payload.get('status'))
        print(f"       last seen {payload.get('seconds_since_seen')} s ago")

        # ── a robot that is not mine is not testable ─────────────────────────
        print('\n-- owner scoping --')
        other = LapTimerDevice.objects.create(
            device_id='A0:00:00:22:00:09', friendly_name='Not Mine',
            device_type=DeviceType.ROBOT,
            registration_status=RegistrationStatus.ACTIVE)
        status, _ = post(f'/contest/participate/robots/{other.id}/test/',
                         {'csrfmiddlewaretoken': csrf()})
        check("testing someone else's robot is refused", status == 404, status)
        other.delete()

    finally:
        stop_sim(sim)
        teardown()

    print(f'\n{checks - len(failures)}/{checks} checks passed')
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('S22 CONNECTION TEST CHECK PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
