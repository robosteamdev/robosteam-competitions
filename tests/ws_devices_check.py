#!/usr/bin/env python3
"""
Phase 5.21 section C — live proof of the devices WebSocket.

Decision 039 is the reason this exists as a live check rather than a unit test:
every board socket silently died after 5 s of idle and 397 unit tests were green
throughout. So this holds a real socket open, waits for a push emitted by a
*different* process (mqtt_bridge, on a PONG), and also stays connected past the
idle window that broke the last one.

    cd ~/competitions/scoreboard && ~/venv/bin/python ../tests/ws_devices_check.py [MAC]
"""
import asyncio
import http.cookiejar
import os
import sys
import urllib.parse
import urllib.request

import django
import websockets

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from django.contrib.auth.models import Group, User      # noqa: E402

from contest.models import UserProfile                  # noqa: E402
from devices.models import (                            # noqa: E402
    DeviceType, LapTimerDevice, RegistrationStatus,
)
from devices.services import ping_device                # noqa: E402

BASE     = 'http://127.0.0.1:8000'
WS_URL   = 'ws://127.0.0.1:8000/ws/devices/'
MAC      = sys.argv[1] if len(sys.argv) > 1 else 'AA:BB:CC:DD:EE:F1'
USERNAME = 'wsdevices_check_user'
PASSWORD = 'ws-devices-pw-2026'
IDLE_HOLD = 8       # must exceed the 5 s that killed the board sockets

failures = []


def check(label, ok, detail=''):
    print(f'  {"ok  " if ok else "FAIL"} {label} {detail}')
    if not ok:
        failures.append(label)


def ensure_user():
    User.objects.filter(username=USERNAME).delete()
    group, _ = Group.objects.get_or_create(name='Participant')
    user = User.objects.create_user(USERNAME, password=PASSWORD)
    user.groups.add(group)
    UserProfile.objects.create(user=user, requested_role='participant')
    return user


def login_session_cookie():
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.open(BASE + '/accounts/login/')
    token = next((c.value for c in jar if c.name == 'csrftoken'), '')
    body  = urllib.parse.urlencode({
        'username': USERNAME, 'password': PASSWORD, 'csrfmiddlewaretoken': token,
    }).encode()
    req = urllib.request.Request(BASE + '/accounts/login/', data=body)
    req.add_header('Referer', BASE + '/accounts/login/')
    opener.open(req)
    return '; '.join(f'{c.name}={c.value}' for c in jar)


async def main(cookie, device):
    print(f'\nDevices WebSocket check against {WS_URL}\n')

    # anonymous must be refused
    try:
        async with websockets.connect(WS_URL) as anon:
            await asyncio.wait_for(anon.recv(), timeout=3)
        check('anonymous is refused', False, '(socket stayed open)')
    except Exception:
        check('anonymous is refused', True)

    # `extra_headers` is the legacy-client spelling; the pinned websockets build
    # routes connect() through websockets.legacy.
    async with websockets.connect(WS_URL, extra_headers={'Cookie': cookie}) as ws:
        check('authenticated socket connects', True)

        # Hold it idle past the window that killed every board socket (Decision 039).
        await asyncio.sleep(IDLE_HOLD)
        still_open = not getattr(ws, 'closed', False)
        check(f'socket survives {IDLE_HOLD}s idle', still_open)

        # The push comes from mqtt_bridge, a different process — this is the
        # cross-process Redis delivery, not a loopback. The ORM call has to leave
        # the event loop, hence to_thread.
        ping = await asyncio.to_thread(ping_device, device)
        print(f'  ..  PING sent (nonce={ping.nonce}), waiting for the bridge to push')
        try:
            message = await asyncio.wait_for(ws.recv(), timeout=15)
            check('push received from the bridge process', True, f'\n       {message}')
            check('push carries the round-trip time', '"rtt_ms"' in message)
            check('push identifies the device', MAC in message)
        except asyncio.TimeoutError:
            check('push received from the bridge process', False, '(timed out)')

    print()
    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('ALL WEBSOCKET CHECKS PASSED')
    return 0


if __name__ == '__main__':
    # All ORM work happens here, outside the event loop.
    user = ensure_user()
    session_cookie = login_session_cookie()
    target, _ = LapTimerDevice.objects.get_or_create(
        device_id=MAC,
        defaults={'friendly_name': 'WS check target', 'device_type': DeviceType.ROBOT,
                  'registration_status': RegistrationStatus.ACTIVE},
    )
    # The checking user has to OWN the target (Phase 5.21 G). The socket used to
    # push every device to every logged-in user, so this check passed without an
    # owner and was quietly asserting the leak. A participant now receives only
    # their own devices, which is also the real scenario: a student watching the
    # badge on their own robot.
    previous_owner = target.owner_id
    LapTimerDevice.objects.filter(pk=target.pk).update(owner=user)
    target.refresh_from_db()
    try:
        code = asyncio.run(main(session_cookie, target))
    finally:
        LapTimerDevice.objects.filter(pk=target.pk).update(owner=previous_owner)
        User.objects.filter(username=USERNAME).delete()
    sys.exit(code)
