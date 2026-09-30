from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class DeviceType(models.TextChoices):
    LAPTIMER = 'LAPTIMER', _('Lap Timer')
    ROBOT    = 'ROBOT',    _('Robot')


class DeviceStatus(models.TextChoices):
    ONLINE  = 'ONLINE',  _('Online')
    OFFLINE = 'OFFLINE', _('Offline')
    ERROR   = 'ERROR',   _('Error')


class RegistrationStatus(models.TextChoices):
    PENDING  = 'PENDING',  _('Pending approval')
    ACTIVE   = 'ACTIVE',   _('Active')
    INACTIVE = 'INACTIVE', _('Inactive')


def validate_device_approved(device):
    """
    Raise ValidationError unless `device` has been approved by an administrator.

    The scoring engine ignores LapEvents from devices that are not ACTIVE (H4),
    so assigning an unapproved timer to a category would silently produce a
    category that can never score. Used by every form that offers a lap_timer
    choice (Django admin and the organiser category form).
    """
    if device is None:
        return device
    if device.registration_status != RegistrationStatus.ACTIVE:
        raise ValidationError(
            f'Device "{device.friendly_name}" is {device.get_registration_status_display()} '
            f'and would not score any runs. Approve it in Devices first.'
        )
    return device


class LapTimerDevice(models.Model):
    device_id     = models.CharField(
        max_length=17, unique=True,
        help_text='MAC address, used as unique device identifier.')
    friendly_name = models.CharField(
        max_length=100,
        help_text='Human-readable name set by operator on device web page.')
    device_type   = models.CharField(
        max_length=10, choices=DeviceType.choices,
        default=DeviceType.LAPTIMER)
    status        = models.CharField(
        max_length=10, choices=DeviceStatus.choices,
        default=DeviceStatus.OFFLINE)
    registration_status = models.CharField(
        max_length=10, choices=RegistrationStatus.choices,
        default=RegistrationStatus.PENDING,
        help_text='Approval state: PENDING=awaiting admin approval, ACTIVE=approved, INACTIVE=deactivated.')
    last_seen     = models.DateTimeField(null=True, blank=True)
    country       = models.CharField(max_length=100, blank=True)
    organisation  = models.CharField(
        max_length=200, blank=True,
        help_text='School or organisation.')
    owner         = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='devices',
        help_text='Participant who claimed this robot. Null = unclaimed. '
                  'Lap timers stay unowned: they are venue infrastructure (Decision 040).')
    claimed_at    = models.DateTimeField(
        null=True, blank=True,
        help_text='When the current owner claimed the device.')
    firmware_version = models.CharField(
        max_length=32, blank=True,
        help_text='Reported by the device in its heartbeat, if the firmware sends it.')
    last_ip       = models.GenericIPAddressField(
        null=True, blank=True,
        help_text='Last address the device registered or reported from (diagnostics only).')

    class Meta:
        verbose_name        = 'Device'
        verbose_name_plural = 'Devices'

    def __str__(self):
        return f'{self.friendly_name} ({self.device_id})'

    @property
    def is_robot(self):
        return self.device_type == DeviceType.ROBOT

    def claim(self, user):
        """
        Bind an unowned device to `user`.

        Claiming is MAC-only and first-come-first-served (Decision 042), so the
        one rule that matters is enforced here rather than in the view: a device
        that already has an owner is never silently re-assigned. Callers get a
        ValidationError, not a stolen robot.
        """
        from django.utils import timezone

        if self.owner_id is not None:
            raise ValidationError('This device has already been claimed.')
        if not self.is_robot:
            raise ValidationError(
                'Lap timers are managed by the organisers and cannot be claimed.'
            )
        self.owner      = user
        self.claimed_at = timezone.now()
        self.save(update_fields=['owner', 'claimed_at'])

    def release(self):
        """
        Return the device to the unclaimed pool and drop every team binding.

        The bindings must go with the ownership: a Team pointing at a robot its
        owner no longer controls would still receive targeted START commands.
        """
        self.teams.update(robot=None)
        self.owner      = None
        self.claimed_at = None
        self.save(update_fields=['owner', 'claimed_at'])


class DevicePing(models.Model):
    """
    One connection test: a PING published to a device and the PONG that came back.

    This exists as a row rather than an in-memory wait because the two halves
    happen in **different processes** — the web process publishes the PING, and
    `mqtt_bridge` receives the PONG. A view cannot block on a reply that will be
    delivered to another process, so the reply is written here and the browser is
    told about it over the `devices` WebSocket group.
    """
    device     = models.ForeignKey(
        LapTimerDevice, on_delete=models.CASCADE, related_name='pings')
    nonce      = models.CharField(
        max_length=32, unique=True,
        help_text='Echoed by the device so a slow PONG cannot be credited to a later PING.')
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='device_pings')
    sent_at    = models.DateTimeField(auto_now_add=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    rtt_ms     = models.PositiveIntegerField(
        null=True, blank=True, help_text='Round-trip time. Null until the PONG arrives.')

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        state = f'{self.rtt_ms} ms' if self.rtt_ms is not None else 'no reply'
        return f'Ping {self.device.device_id} ({state})'


class LapEvent(models.Model):
    device        = models.ForeignKey(
        LapTimerDevice, on_delete=models.CASCADE,
        related_name='lap_events')
    competition   = models.ForeignKey(
        'contest.Competition', on_delete=models.CASCADE,
        related_name='lap_events',
        null=True, blank=True,
        help_text='Category assigned by scoring logic after the run. Null for raw unprocessed events.')
    run           = models.ForeignKey(
        'contest.Run', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='lap_events',
        help_text='The Run this event is assigned to (set by server).')
    timestamp_utc = models.DateTimeField(
        help_text='Hardware timestamp from device (UTC, ISO 8601).')
    sequence      = models.PositiveIntegerField(
        help_text='Monotonically increasing per-device counter for deduplication.')
    received_at   = models.DateTimeField(
        auto_now_add=True,
        help_text='Server receive time (for latency measurement).')
    lap_number    = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='0=start crossing, 1=first lap, 2=second lap, etc. Set by scoring engine.')

    class Meta:
        unique_together = (('device', 'sequence'),)
        ordering        = ['timestamp_utc']

    def __str__(self):
        return f'LapEvent seq={self.sequence} @ {self.timestamp_utc}'
