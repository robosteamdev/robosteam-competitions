import json
import logging
import signal
import sys
from datetime import datetime, timezone as dt_timezone

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

from devices.models import (
    DeviceStatus, DeviceType, LapEvent, LapTimerDevice, RegistrationStatus,
)
from devices.services import push_device_update, record_pong

logger = logging.getLogger('mqtt_bridge')

SUBSCRIBE_TOPICS = [
    ('robosteam/laptimer/+/event', 1),
    ('robosteam/laptimer/+/status', 1),
    ('robosteam/robot/+/status', 1),
]


def _upsert_device(mac: str, device_type: str) -> LapTimerDevice:
    device, _ = LapTimerDevice.objects.get_or_create(
        device_id=mac,
        defaults={
            'friendly_name': mac,
            'device_type': device_type,
            # Same rule as the HTTP registration path (Decision 041): a robot that
            # turns up over MQTT before it ever registers is still a robot, and
            # robots need no approval. Timers keep the default PENDING.
            'registration_status': (
                RegistrationStatus.ACTIVE if device_type == DeviceType.ROBOT
                else RegistrationStatus.PENDING
            ),
        },
    )
    return device


def handle_lap_event(mac: str, payload: dict) -> None:
    seq = payload.get('seq')
    ts_str = payload.get('ts')
    if seq is None or not ts_str:
        logger.warning('LapEvent missing seq or ts from %s: %s', mac, payload)
        return

    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except ValueError:
        logger.warning('Bad timestamp from %s: %r', mac, ts_str)
        return

    device = _upsert_device(mac, DeviceType.LAPTIMER)
    was_offline = device.status != DeviceStatus.ONLINE
    device.status = DeviceStatus.ONLINE
    device.last_seen = dj_timezone.now()
    device.save(update_fields=['status', 'last_seen'])
    if was_offline:
        # A beam crossing proves the timer is alive even if its heartbeat is lost.
        push_device_update(device)

    _, created = LapEvent.objects.get_or_create(
        device=device,
        sequence=seq,
        defaults={'timestamp_utc': ts},
    )
    if created:
        logger.info('LapEvent recorded: device=%s seq=%s ts=%s', mac, seq, ts)
    else:
        logger.debug('Duplicate LapEvent ignored: device=%s seq=%s', mac, seq)


def handle_device_status(mac: str, device_type: str, payload: dict = None) -> None:
    payload = payload or {}
    device = _upsert_device(mac, device_type)

    was_offline = device.status != DeviceStatus.ONLINE
    device.status = DeviceStatus.ONLINE
    device.last_seen = dj_timezone.now()
    updated = ['status', 'last_seen']

    # Accept every spelling in the wild: the Phase 7 simulator already sends
    # firmware_version, and the Phase 6 firmware is not written yet.
    firmware = str(
        payload.get('firmware')
        or payload.get('firmware_version')
        or payload.get('fw')
        or ''
    ).strip()
    if firmware and firmware != device.firmware_version:
        device.firmware_version = firmware[:32]
        updated.append('firmware_version')

    device.save(update_fields=updated)
    logger.debug('Heartbeat: %s (%s)', mac, device_type)

    # A PONG rides on the status topic, so it is handled here rather than needing
    # its own subscription.
    if str(payload.get('cmd') or payload.get('reply') or '').upper() == 'PONG':
        nonce = str(payload.get('nonce') or '')
        ping = record_pong(mac, nonce)
        if ping is not None:
            push_device_update(device, rtt_ms=ping.rtt_ms, nonce=nonce)
            return

    # Only push when something a viewer can see actually changed. A robot beating
    # every 30 s would otherwise spam every open page for no visible difference.
    if was_offline or 'firmware_version' in updated:
        push_device_update(device)


def on_connect(client, userdata, connect_flags, reason_code, properties):
    if reason_code == 0:
        logger.info('Connected to MQTT broker')
        for topic, qos in SUBSCRIBE_TOPICS:
            client.subscribe(topic, qos=qos)
            logger.info('Subscribed: %s', topic)
    else:
        logger.error('Connection refused, reason_code=%s', reason_code)


#: Set just before a deliberate client.disconnect() (SIGTERM / SIGINT) so the
#: disconnect callback does not void runs that are still legitimately in progress.
_intentional_disconnect = False


def mark_intentional_disconnect(value: bool = True) -> None:
    """Flag the next disconnect as deliberate (clean shutdown), not a broker outage."""
    global _intentional_disconnect
    _intentional_disconnect = value


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    if _intentional_disconnect:
        logger.info('Disconnected from broker (intentional shutdown); active runs left alone')
        return

    logger.warning('Disconnected from broker, reason_code=%s', reason_code)
    try:
        from scoring.engine import void_active_runs_on_disconnect
        voided = void_active_runs_on_disconnect()
        if voided:
            logger.warning('MQTT disconnect: auto-voided %s active run(s)', voided)
    except Exception:
        logger.exception('Error voiding runs on disconnect')


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning('Unparseable payload on %s: %r', msg.topic, msg.payload)
        return

    parts = msg.topic.split('/')
    if len(parts) != 4:
        return
    _, device_class, mac, event_type = parts

    if device_class == 'laptimer':
        if event_type == 'event':
            handle_lap_event(mac, payload)
        elif event_type == 'status':
            handle_device_status(mac, DeviceType.LAPTIMER, payload)
    elif device_class == 'robot':
        if event_type == 'status':
            handle_device_status(mac, DeviceType.ROBOT, payload)


class Command(BaseCommand):
    help = 'Run the MQTT bridge — subscribes to device topics and writes events to the DB.'

    def handle(self, *args, **options):
        broker = settings.MQTT_BROKER_HOST
        port = settings.MQTT_BROKER_PORT
        username = settings.MQTT_USERNAME
        password = settings.MQTT_PASSWORD

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.username_pw_set(username, password)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect    = on_connect
        client.on_disconnect = on_disconnect
        client.on_message    = on_message

        def _shutdown(signum, frame):
            self.stdout.write('Shutting down MQTT bridge...')
            mark_intentional_disconnect()
            client.disconnect()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)

        self.stdout.write(self.style.SUCCESS(f'Connecting to {broker}:{port}'))
        client.connect(broker, port, keepalive=60)
        client.loop_forever(retry_first_connection=True)
