#!/usr/bin/env python3
"""
Phase 5.21 section C — live proof of the connection test.

Publishes a real PING through the real broker and waits for `mqtt_bridge` (a
separate process) to record the PONG. Nothing here is mocked: if the round trip
completes, the whole chain works — web process publishes, broker delivers,
simulator answers, bridge matches the nonce and writes the result.

    # terminal 1
    ~/venv/bin/python ../tests/robot_sim.py --mac AA:BB:CC:DD:EE:F1 \
        --broker <broker-address> --port 1883 --user "$MQTT_USERNAME" --password "$MQTT_PASSWORD"
    # terminal 2
    cd ~/competitions/scoreboard && ~/venv/bin/python ../tests/live_ping_check.py
"""
import os
import sys
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scoreboard.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../scoreboard")
django.setup()

from devices.models import (                    # noqa: E402
    DevicePing, DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)
from devices.services import ping_device        # noqa: E402

MAC     = sys.argv[1] if len(sys.argv) > 1 else 'AA:BB:CC:DD:EE:F1'
TIMEOUT = 15


def main():
    device, created = LapTimerDevice.objects.get_or_create(
        device_id=MAC,
        defaults={'friendly_name': 'Live ping target',
                  'device_type': DeviceType.ROBOT,
                  'registration_status': RegistrationStatus.ACTIVE},
    )
    print(f'target: {device.device_id} ({"created" if created else "existing"})')
    print(f'status before: {device.status}, last_seen={device.last_seen}')

    ping = ping_device(device)
    print(f'PING published, nonce={ping.nonce}')

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        ping.refresh_from_db()
        if ping.rtt_ms is not None:
            device.refresh_from_db()
            print(f'\nPONG matched in {ping.rtt_ms} ms')
            print(f'device status now: {device.status}')
            print(f'firmware captured: {device.firmware_version!r}')
            ok = device.status == DeviceStatus.ONLINE
            print('\nLIVE PING CHECK PASSED' if ok else '\nFAILED: device not ONLINE')
            return 0 if ok else 1
        time.sleep(0.25)

    print(f'\nFAILED: no PONG within {TIMEOUT}s')
    print('Is the simulator running, and is mqtt_bridge up with the new code?')
    return 1


if __name__ == '__main__':
    sys.exit(main())
