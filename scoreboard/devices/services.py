"""Background housekeeping for device connectivity state, and the connection test."""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import DevicePing, DeviceStatus, DeviceType, LapTimerDevice

logger = logging.getLogger(__name__)

#: How long the browser waits for a PONG before falling back to heartbeat freshness.
#: Firmware without a PING handler never replies, so the fallback is the normal
#: path for anything not yet reflashed — not an error case.
PING_TIMEOUT_SECONDS = 8

#: A device is considered offline once no heartbeat or lap event has arrived
#: within this many seconds. Devices publish a heartbeat every 30 s, so the
#: default tolerates two missed beats before flipping the badge.
DEFAULT_OFFLINE_SECONDS = 90


def offline_threshold_seconds():
    return getattr(settings, 'DEVICE_OFFLINE_SECONDS', DEFAULT_OFFLINE_SECONDS)


def sweep_offline_devices(threshold_seconds=None):
    """
    Mark every device whose last heartbeat is stale as OFFLINE.

    Nothing else ever clears the ONLINE flag: the bridge sets it on each
    heartbeat or lap event, so without this sweep a device that is unplugged
    mid-competition keeps showing as online forever (H7). Devices that have
    never been seen keep their stored status.

    Returns the number of devices whose status changed.
    """
    if threshold_seconds is None:
        threshold_seconds = offline_threshold_seconds()

    cutoff = timezone.now() - timedelta(seconds=threshold_seconds)
    stale = LapTimerDevice.objects.filter(
        status=DeviceStatus.ONLINE, last_seen__lt=cutoff,
    )
    macs = list(stale.values_list('device_id', flat=True))
    changed = stale.update(status=DeviceStatus.OFFLINE)
    if changed:
        logger.warning(
            'Marked %s device(s) OFFLINE (no heartbeat for %ss): %s',
            changed, threshold_seconds, ', '.join(macs),
        )
        # The badge has to follow the sweep, or a robot that was unplugged keeps
        # showing green on every open page until someone reloads (H7).
        for device in LapTimerDevice.objects.filter(device_id__in=macs):
            push_device_update(device)
    return changed


# ── Live status ───────────────────────────────────────────────────────────────

def device_status_payload(device):
    """The shape every WebSocket message and status endpoint uses."""
    return {
        'device_id':        device.device_id,
        'friendly_name':    device.friendly_name,
        'status':           device.status,
        'last_seen':        device.last_seen.isoformat() if device.last_seen else None,
        'seconds_since_seen': (
            int((timezone.now() - device.last_seen).total_seconds())
            if device.last_seen else None
        ),
        'firmware_version': device.firmware_version,
    }


def push_device_update(device, **extra):
    """
    Push a device's state to every page watching the `devices` group.

    Called from three processes — the web process on a ping, `mqtt_bridge` on a
    heartbeat or PONG, and `timeout_runs` on the offline sweep — which is why the
    channel layer has to be Redis and not the in-memory one (Phase B).

    `owner_id` is sent alongside the payload so `DeviceConsumer` can decide who
    may see this device. It is routing information and never reaches a browser:
    a participant must not learn the MAC of a robot they do not own, because
    with MAC-only claiming that MAC is the whole credential (Decision 042).
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        payload = device_status_payload(device)
        payload.update(extra)
        async_to_sync(channel_layer.group_send)(
            'devices',
            {
                'type':     'device.update',
                'device':   payload,
                'owner_id': device.owner_id,
            },
        )
    except Exception:
        # Never let a failed push break a heartbeat or a sweep (Phase G rule:
        # log it, never swallow it silently).
        logger.exception('WebSocket push failed for device %s', device.device_id)


# ── Connection test ───────────────────────────────────────────────────────────

def ping_device(device, requested_by=None):
    """
    Publish a PING to `device` and record the attempt.

    Returns the DevicePing. The reply lands in `mqtt_bridge` (a different
    process), which fills in replied_at/rtt_ms and pushes the result over the
    `devices` group — see DevicePing's docstring.

    The PING goes to the device's **own** command topic family: a lap timer
    listens on `robosteam/laptimer/{mac}/cmd`, a robot on `robosteam/robot/…`.
    Sending every PING to the robot topic (as this did before Phase 5.21 F)
    means a timer can never answer one, which would only surface the first time
    someone tested a timer — with the firmware already flashed.
    """
    from mqtt_bridge.publisher import publish_laptimer_command, publish_robot_command

    ping = DevicePing.objects.create(
        device=device, nonce=uuid.uuid4().hex[:16], requested_by=requested_by,
    )
    send = (
        publish_laptimer_command if device.device_type == DeviceType.LAPTIMER
        else publish_robot_command
    )
    send(device.device_id, 'PING', nonce=ping.nonce)
    logger.info(
        'PING sent to %s %s (nonce=%s)',
        device.device_type, device.device_id, ping.nonce,
    )
    return ping


def record_pong(mac, nonce):
    """
    Match a PONG to its PING and record the round-trip. Returns the DevicePing or None.

    Unmatched nonces are dropped rather than credited to the most recent ping: a
    reply that arrives after the browser gave up must not make a later test look
    instantaneous.
    """
    ping = (
        DevicePing.objects
        .filter(nonce=nonce, device__device_id=mac, replied_at__isnull=True)
        .select_related('device')
        .first()
    )
    if ping is None:
        logger.debug('PONG from %s with unknown or already-used nonce %s', mac, nonce)
        return None

    ping.replied_at = timezone.now()
    ping.rtt_ms = max(0, int((ping.replied_at - ping.sent_at).total_seconds() * 1000))
    ping.save(update_fields=['replied_at', 'rtt_ms'])
    logger.info('PONG from %s in %s ms', mac, ping.rtt_ms)
    return ping
