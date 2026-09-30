#!/usr/bin/env python3
"""
Phase 5.21 section E (scenario S24) — live proof that START reaches ONE robot.

Nothing is mocked. A real MQTT subscriber sits on both teams' robot topics and
on the competition broadcast while a real judge presses START and STOP over
HTTP against the running server. The check that matters is the negative one:
the rival team's robot topic must stay silent.

The unit tests in `mqtt_bridge/tests_targeting.py` prove the same logic against
a patched paho. This proves the deployment — all three Phase H bugs passed the
unit suite and still broke on the running stack.

    cd ~/competitions/scoreboard
    ~/venv/bin/python ../tests/live_targeted_start_check.py

Sets up its own fixtures and deletes them again, pass or fail.
"""
import http.cookiejar
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

import paho.mqtt.client as mqtt                             # noqa: E402
from django.conf import settings                            # noqa: E402
from django.contrib.auth.models import Group, User          # noqa: E402

from contest.models import (                                # noqa: E402
    Competition, CompetitionType, Contest, ItemStates, Run, Team,
)
from devices.models import (                                # noqa: E402
    DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)

BASE      = 'http://127.0.0.1:8000'
JUDGE     = 'targetcheck_judge'
PASSWORD  = 'targetcheck-pw-2026'
MINE      = 'A0:00:00:EE:00:01'
RIVALS    = 'A0:00:00:EE:00:02'
TIMER     = 'A0:00:00:EE:00:03'
CONTEST   = 'Targeted START Check Cup'

#: How long to wait for a publish to come back round through the broker. The
#: round trip is single-digit milliseconds on the LAN; this is slack, not a
#: measurement.
SETTLE_SECONDS = 3

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
    Contest.objects.filter(name=CONTEST).delete()      # cascades categories + teams
    User.objects.filter(username=JUDGE).delete()


def setup():
    teardown()

    group, _ = Group.objects.get_or_create(name='Judge')
    User.objects.create_user(JUDGE, password=PASSWORD).groups.add(group)

    contest = Contest.objects.create(name=CONTEST, status=ItemStates.OPEN)
    timer = LapTimerDevice.objects.create(
        device_id=TIMER, friendly_name='Target Check Timer',
        device_type=DeviceType.LAPTIMER,
        registration_status=RegistrationStatus.ACTIVE)
    category = Competition.objects.create(
        name='Target Check Line Following', contest=contest,
        competition_type=CompetitionType.TIMED, status=ItemStates.OPEN,
        num_laps=1, num_runs=5, timeout_seconds=120, lap_timer=timer)

    mine = LapTimerDevice.objects.create(
        device_id=MINE, friendly_name='Target Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, status=DeviceStatus.ONLINE)
    rivals = LapTimerDevice.objects.create(
        device_id=RIVALS, friendly_name='Rival Rover', device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, status=DeviceStatus.ONLINE)

    team  = Team.objects.create(name='Target Team', contest=contest, robot=mine)
    rival = Team.objects.create(name='Rival Team', contest=contest, robot=rivals)
    return category, team, rival


# ── MQTT listener ────────────────────────────────────────────────────────────

received = []
received_lock = threading.Lock()


def start_listener(category_id):
    """Subscribe to both robot topics and the broadcast. Returns the client."""
    topics = [
        (f'robosteam/robot/{MINE}/cmd', 1),
        (f'robosteam/robot/{RIVALS}/cmd', 1),
        (f'robosteam/competition/{category_id}/cmd', 1),
    ]
    ready = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties=None):
        client.subscribe(topics)

    def on_subscribe(client, userdata, mid, reason_codes, properties=None):
        ready.set()

    def on_message(client, userdata, msg):
        with received_lock:
            received.append((msg.topic, json.loads(msg.payload.decode())))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id='targeted-start-check')
    client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    client.on_connect   = on_connect
    client.on_subscribe = on_subscribe
    client.on_message   = on_message
    client.connect(settings.MQTT_BROKER_HOST, settings.MQTT_BROKER_PORT, 60)
    client.loop_start()

    if not ready.wait(10):
        print(f'FAILED: could not subscribe on '
              f'{settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}')
        sys.exit(2)
    return client


def drain():
    """Everything heard so far, then reset — one command per assertion block."""
    time.sleep(SETTLE_SECONDS)
    with received_lock:
        heard = list(received)
        received.clear()
    return heard


def payloads_on(heard, topic):
    return [payload for t, payload in heard if t == topic]


# ── HTTP plumbing ────────────────────────────────────────────────────────────

jar    = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def csrf():
    for c in jar:
        if c.name == 'csrftoken':
            return c.value
    return ''


def get(path):
    with opener.open(BASE + path) as r:
        return r.status, r.read().decode('utf-8', 'replace')


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


def login():
    get('/accounts/login/')
    return post('/accounts/login/',
                {'username': JUDGE, 'password': PASSWORD,
                 'csrfmiddlewaretoken': csrf()},
                referer=BASE + '/accounts/login/')


# ── the check ────────────────────────────────────────────────────────────────

def main():
    category, team, rival = setup()
    client = start_listener(category.id)
    mine_topic      = f'robosteam/robot/{MINE}/cmd'
    rivals_topic    = f'robosteam/robot/{RIVALS}/cmd'
    broadcast_topic = f'robosteam/competition/{category.id}/cmd'

    print(f'\nPhase 5.21 E targeted-command check against {BASE}')
    print(f'broker {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}\n')

    login()

    # ── pre-flight ───────────────────────────────────────────────────────────
    status, body = post(f'/contest/competition/{category.id}/run/create',
                        {'team_id': team.id, 'csrfmiddlewaretoken': csrf()})
    check('judge creates a run', status == 200, body[:200])
    run = Run.objects.filter(team=team, competition=category).first()
    if run is None:
        print('FAILED: no run was created — cannot continue')
        client.loop_stop()
        teardown()
        return 1

    status, body = get(f'/contest/run/{run.id}/preflight')
    data = json.loads(body)
    check('preflight reaches the judge', status == 200)
    check('preflight names the bound robot',
          data['robot'] and data['robot']['friendly_name'] == 'Target Rover', body)
    check('online robot raises no warning', data['warnings'] == [], body)
    check('preflight does not leak the MAC', MINE not in body)

    drain()  # ignore anything that happened during setup

    # ── START ────────────────────────────────────────────────────────────────
    status, body = post(f'/contest/run/{run.id}/start', {'csrfmiddlewaretoken': csrf()})
    check('judge starts the run', status == 200, body[:200])
    heard = drain()

    mine_msgs   = payloads_on(heard, mine_topic)
    rival_msgs  = payloads_on(heard, rivals_topic)
    bcast_msgs  = payloads_on(heard, broadcast_topic)

    check('bound robot receives a targeted START',
          len(mine_msgs) == 1 and mine_msgs[0]['cmd'] == 'START', mine_msgs)
    check('targeted START carries the run_id',
          mine_msgs and mine_msgs[0].get('run_id') == run.id, mine_msgs)
    check("RIVAL TEAM'S ROBOT HEARS NOTHING", rival_msgs == [], rival_msgs)
    check('lap timer still receives the broadcast START',
          len(bcast_msgs) == 1 and bcast_msgs[0]['cmd'] == 'START', bcast_msgs)
    check('broadcast payload is unchanged',
          bcast_msgs and bcast_msgs[0] == {'cmd': 'START', 'run_id': run.id}, bcast_msgs)

    # ── offline robot warns but does not block ───────────────────────────────
    LapTimerDevice.objects.filter(device_id=MINE).update(status=DeviceStatus.OFFLINE)
    status, body = get(f'/contest/run/{run.id}/preflight')
    data = json.loads(body)
    check('offline robot produces a warning', len(data['warnings']) == 1, body)
    check('the warning names the robot',
          data['warnings'] and 'Target Rover' in data['warnings'][0], body)
    LapTimerDevice.objects.filter(device_id=MINE).update(status=DeviceStatus.ONLINE)

    # ── STOP ─────────────────────────────────────────────────────────────────
    drain()
    status, body = post(f'/contest/run/{run.id}/stop', {'csrfmiddlewaretoken': csrf()})
    check('judge voids the run', status == 200, body[:200])
    heard = drain()

    mine_msgs  = payloads_on(heard, mine_topic)
    rival_msgs = payloads_on(heard, rivals_topic)
    bcast_msgs = payloads_on(heard, broadcast_topic)

    check('bound robot receives a targeted STOP',
          len(mine_msgs) == 1 and mine_msgs[0]['cmd'] == 'STOP', mine_msgs)
    check('targeted STOP carries the run_id',
          mine_msgs and mine_msgs[0].get('run_id') == run.id, mine_msgs)
    check("rival team's robot hears nothing on STOP either", rival_msgs == [], rival_msgs)
    check('broadcast STOP payload is unchanged',
          bcast_msgs and bcast_msgs[0] == {'cmd': 'STOP'}, bcast_msgs)

    client.loop_stop()
    client.disconnect()
    teardown()

    print(f'\n{checks - len(failures)}/{checks} checks passed')
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('LIVE TARGETED-COMMAND CHECK PASSED')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        teardown()
        raise
