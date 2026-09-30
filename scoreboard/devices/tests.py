from django.test import TestCase
from django.utils import timezone
from .models import LapTimerDevice, LapEvent, DeviceType, DeviceStatus


class LapTimerDeviceModelTest(TestCase):
    def setUp(self):
        self.device = LapTimerDevice.objects.create(
            device_id='AA:BB:CC:DD:EE:FF',
            friendly_name='Timer-1',
            device_type=DeviceType.LAPTIMER,
            status=DeviceStatus.OFFLINE,
            country='Bulgaria',
            organisation='ROBO STEAM ACADEMY',
        )

    def test_str(self):
        self.assertEqual(str(self.device), 'Timer-1 (AA:BB:CC:DD:EE:FF)')

    def test_default_status_is_offline(self):
        self.assertEqual(self.device.status, DeviceStatus.OFFLINE)

    def test_default_device_type_is_laptimer(self):
        self.assertEqual(self.device.device_type, DeviceType.LAPTIMER)


class LapEventModelTest(TestCase):
    def setUp(self):
        from contest.models import Contest, Competition, Team, Run, CompetitionType

        self.device = LapTimerDevice.objects.create(
            device_id='11:22:33:44:55:66',
            friendly_name='Timer-2',
        )
        self.contest = Contest.objects.create(name='Test Contest')
        self.competition = Competition.objects.create(
            name='Line Following',
            contest=self.contest,
            competition_type=CompetitionType.TIMED,
        )
        self.team = Team.objects.create(name='Team Alpha', contest=self.contest)
        self.run = Run.objects.create(
            team=self.team,
            competition=self.competition,
            start_time=timezone.now(),
            duration=0,
        )
        self.event = LapEvent.objects.create(
            device=self.device,
            competition=self.competition,
            run=self.run,
            timestamp_utc=timezone.now(),
            sequence=1,
        )

    def test_str(self):
        self.assertIn('LapEvent seq=1', str(self.event))

    def test_unique_together_device_sequence(self):
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            LapEvent.objects.create(
                device=self.device,
                competition=self.competition,
                timestamp_utc=timezone.now(),
                sequence=1,
            )

# ── Phase 5.9: Device self-registration API tests ─────────────────────────────

import json

from django.test import TestCase

from devices.models import DeviceType, LapTimerDevice, RegistrationStatus


class DeviceRegistrationAPITest(TestCase):
    """POST /devices/register/ creates and idempotently updates devices."""

    URL = '/devices/register/'

    def _post(self, payload):
        return self.client.post(
            self.URL,
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_register_new_device_returns_201(self):
        r = self._post({'mac': 'AA:BB:CC:DD:EE:01', 'friendly_name': 'Lab Timer'})
        self.assertEqual(r.status_code, 201)

    def test_register_new_device_creates_record(self):
        self._post({'mac': 'AA:BB:CC:DD:EE:02', 'friendly_name': 'Lab Timer', 'device_type': 'LAPTIMER'})
        self.assertTrue(LapTimerDevice.objects.filter(device_id='AA:BB:CC:DD:EE:02').exists())

    def test_new_device_has_pending_registration_status(self):
        self._post({'mac': 'AA:BB:CC:DD:EE:03', 'friendly_name': 'Lab Timer'})
        dev = LapTimerDevice.objects.get(device_id='AA:BB:CC:DD:EE:03')
        self.assertEqual(dev.registration_status, RegistrationStatus.PENDING)

    def test_duplicate_mac_returns_200(self):
        self._post({'mac': 'AA:BB:CC:DD:EE:04', 'friendly_name': 'Timer A'})
        r = self._post({'mac': 'AA:BB:CC:DD:EE:04', 'friendly_name': 'Timer A Renamed'})
        self.assertEqual(r.status_code, 200)

    def test_duplicate_mac_updates_friendly_name(self):
        self._post({'mac': 'AA:BB:CC:DD:EE:05', 'friendly_name': 'Old Name'})
        self._post({'mac': 'AA:BB:CC:DD:EE:05', 'friendly_name': 'New Name'})
        dev = LapTimerDevice.objects.get(device_id='AA:BB:CC:DD:EE:05')
        self.assertEqual(dev.friendly_name, 'New Name')

    def test_duplicate_mac_does_not_reset_registration_status(self):
        """Re-registration of an approved device must not reset it to PENDING."""
        self._post({'mac': 'AA:BB:CC:DD:EE:06', 'friendly_name': 'Timer'})
        dev = LapTimerDevice.objects.get(device_id='AA:BB:CC:DD:EE:06')
        dev.registration_status = RegistrationStatus.ACTIVE
        dev.save()
        self._post({'mac': 'AA:BB:CC:DD:EE:06', 'friendly_name': 'Timer Updated'})
        dev.refresh_from_db()
        self.assertEqual(dev.registration_status, RegistrationStatus.ACTIVE)

    def test_register_missing_mac_returns_400(self):
        r = self._post({'friendly_name': 'No Mac'})
        self.assertEqual(r.status_code, 400)

    def test_register_missing_friendly_name_returns_400(self):
        r = self._post({'mac': 'AA:BB:CC:DD:EE:07'})
        self.assertEqual(r.status_code, 400)

    def test_register_invalid_device_type_returns_400(self):
        r = self._post({'mac': 'AA:BB:CC:DD:EE:08', 'friendly_name': 'Timer', 'device_type': 'UNKNOWN'})
        self.assertEqual(r.status_code, 400)

    def test_register_robot_device_type(self):
        r = self._post({'mac': 'AA:BB:CC:DD:EE:09', 'friendly_name': 'PicoBot', 'device_type': 'ROBOT'})
        self.assertEqual(r.status_code, 201)
        dev = LapTimerDevice.objects.get(device_id='AA:BB:CC:DD:EE:09')
        self.assertEqual(dev.device_type, DeviceType.ROBOT)

    def test_register_stores_country_and_school(self):
        self._post({
            'mac': 'AA:BB:CC:DD:EE:0A', 'friendly_name': 'Timer BG',
            'country': 'Bulgaria', 'school': 'ROBO STEAM Academy',
        })
        dev = LapTimerDevice.objects.get(device_id='AA:BB:CC:DD:EE:0A')
        self.assertEqual(dev.country, 'Bulgaria')
        self.assertEqual(dev.organisation, 'ROBO STEAM Academy')

    def test_response_body_contains_registration_status(self):
        r = self._post({'mac': 'AA:BB:CC:DD:EE:0B', 'friendly_name': 'Timer'})
        data = r.json()
        self.assertIn('registration_status', data)
        self.assertEqual(data['registration_status'], RegistrationStatus.PENDING)

    def test_get_not_allowed(self):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 405)

    def test_invalid_json_returns_400(self):
        r = self.client.post(self.URL, data='not json', content_type='application/json')
        self.assertEqual(r.status_code, 400)


class DeviceAdminActionsTest(TestCase):
    """Approve and deactivate admin actions update registration_status."""

    def test_default_registration_status_is_pending(self):
        dev = LapTimerDevice.objects.create(device_id='CC:DD:EE:FF:00:01', friendly_name='T')
        self.assertEqual(dev.registration_status, RegistrationStatus.PENDING)

    def test_approve_action_sets_active(self):
        from devices.admin import approve_devices
        dev = LapTimerDevice.objects.create(device_id='CC:DD:EE:FF:00:02', friendly_name='T')
        approve_devices(None, None, LapTimerDevice.objects.filter(pk=dev.pk))
        dev.refresh_from_db()
        self.assertEqual(dev.registration_status, RegistrationStatus.ACTIVE)

    def test_deactivate_action_sets_inactive(self):
        from devices.admin import deactivate_devices
        dev = LapTimerDevice.objects.create(
            device_id='CC:DD:EE:FF:00:03', friendly_name='T',
            registration_status=RegistrationStatus.ACTIVE,
        )
        deactivate_devices(None, None, LapTimerDevice.objects.filter(pk=dev.pk))
        dev.refresh_from_db()
        self.assertEqual(dev.registration_status, RegistrationStatus.INACTIVE)


# ── Phase F2: offline sweep ───────────────────────────────────────────────────

from datetime import timedelta

from django.test import override_settings

from devices.services import sweep_offline_devices


class OfflineSweepTest(TestCase):
    """Stale heartbeats flip a device ONLINE -> OFFLINE; fresh ones are left alone."""

    def _device(self, mac, seconds_ago=None, status=DeviceStatus.ONLINE):
        last_seen = None if seconds_ago is None else timezone.now() - timedelta(seconds=seconds_ago)
        return LapTimerDevice.objects.create(
            device_id=mac, friendly_name=f'Timer {mac}',
            status=status, last_seen=last_seen,
        )

    def test_stale_online_device_goes_offline(self):
        dev = self._device('DD:00:00:00:00:01', seconds_ago=300)
        self.assertEqual(sweep_offline_devices(), 1)
        dev.refresh_from_db()
        self.assertEqual(dev.status, DeviceStatus.OFFLINE)

    def test_fresh_heartbeat_stays_online(self):
        dev = self._device('DD:00:00:00:00:02', seconds_ago=5)
        self.assertEqual(sweep_offline_devices(), 0)
        dev.refresh_from_db()
        self.assertEqual(dev.status, DeviceStatus.ONLINE)

    def test_never_seen_device_is_untouched(self):
        dev = self._device('DD:00:00:00:00:03', seconds_ago=None, status=DeviceStatus.OFFLINE)
        self.assertEqual(sweep_offline_devices(), 0)
        dev.refresh_from_db()
        self.assertEqual(dev.status, DeviceStatus.OFFLINE)
        self.assertIsNone(dev.last_seen)

    def test_already_offline_device_is_not_counted_again(self):
        self._device('DD:00:00:00:00:04', seconds_ago=300, status=DeviceStatus.OFFLINE)
        self.assertEqual(sweep_offline_devices(), 0)

    def test_explicit_threshold_argument_is_honoured(self):
        dev = self._device('DD:00:00:00:00:05', seconds_ago=20)
        self.assertEqual(sweep_offline_devices(threshold_seconds=10), 1)
        dev.refresh_from_db()
        self.assertEqual(dev.status, DeviceStatus.OFFLINE)

    @override_settings(DEVICE_OFFLINE_SECONDS=600)
    def test_settings_threshold_is_honoured(self):
        dev = self._device('DD:00:00:00:00:06', seconds_ago=300)
        self.assertEqual(sweep_offline_devices(), 0)
        dev.refresh_from_db()
        self.assertEqual(dev.status, DeviceStatus.ONLINE)

    def test_timeout_runs_command_runs_the_sweep(self):
        from io import StringIO
        from django.core.management import call_command
        dev = self._device('DD:00:00:00:00:07', seconds_ago=300)
        out = StringIO()
        call_command('timeout_runs', '--once', stdout=out)
        dev.refresh_from_db()
        self.assertEqual(dev.status, DeviceStatus.OFFLINE)
        self.assertIn('device(s) offline', out.getvalue())


class BoardDeviceBadgeTest(TestCase):
    """The competition board header shows the timer's connectivity and approval state."""

    def setUp(self):
        from contest.models import Competition, CompetitionType, Contest
        self.contest = Contest.objects.create(name='Badge Contest', status='OPEN')
        self.device  = LapTimerDevice.objects.create(
            device_id='DD:11:00:00:00:01', friendly_name='Track Timer',
            status=DeviceStatus.ONLINE, last_seen=timezone.now(),
            registration_status=RegistrationStatus.ACTIVE,
        )
        self.competition = Competition.objects.create(
            name='Line Following', contest=self.contest, status='OPEN',
            competition_type=CompetitionType.TIMED, lap_timer=self.device,
        )

    def _board(self):
        return self.client.get(f'/contest/competition_board/{self.competition.id}').content.decode()

    def test_online_device_shows_online(self):
        self.assertIn('Online', self._board())

    def test_offline_device_shows_offline(self):
        self.device.status = DeviceStatus.OFFLINE
        self.device.save(update_fields=['status'])
        self.assertIn('Offline', self._board())

    def test_approved_device_shows_no_registration_badge(self):
        self.assertNotIn('Pending approval', self._board())

    def test_pending_device_shows_registration_badge(self):
        self.device.registration_status = RegistrationStatus.PENDING
        self.device.save(update_fields=['registration_status'])
        self.assertIn('Pending approval', self._board())


# ── Phase F3: hardened re-registration ────────────────────────────────────────

class DeviceReRegistrationHardeningTest(TestCase):
    """A re-registration may rename a device, but never grant or keep approval it shouldn't have."""

    URL = '/devices/register/'

    def _post(self, payload):
        return self.client.post(self.URL, data=json.dumps(payload), content_type='application/json')

    def _approved(self, mac, device_type=DeviceType.LAPTIMER):
        return LapTimerDevice.objects.create(
            device_id=mac, friendly_name='Original', device_type=device_type,
            registration_status=RegistrationStatus.ACTIVE,
        )

    def test_rename_keeps_approval(self):
        dev = self._approved('EE:00:00:00:00:01')
        self._post({'mac': dev.device_id, 'friendly_name': 'Renamed'})
        dev.refresh_from_db()
        self.assertEqual(dev.friendly_name, 'Renamed')
        self.assertEqual(dev.registration_status, RegistrationStatus.ACTIVE)

    def test_becoming_a_robot_lands_active(self):
        """A timer re-registering as a robot needs no approval (Decision 041)."""
        dev = self._approved('EE:00:00:00:00:02', DeviceType.LAPTIMER)
        self._post({'mac': dev.device_id, 'friendly_name': 'Now a robot', 'device_type': 'ROBOT'})
        dev.refresh_from_db()
        self.assertEqual(dev.device_type, DeviceType.ROBOT)
        self.assertEqual(dev.registration_status, RegistrationStatus.ACTIVE)

    def test_becoming_a_lap_timer_resets_to_pending(self):
        """
        The approval-laundering guard, and the reason the F3 type-change reset
        survives Decision 041.

        Robots are auto-approved, so without this a device could register as a
        ROBOT (instant ACTIVE), then re-register as a LAPTIMER and keep that
        ACTIVE — an unreviewed box scoring runs as the authoritative clock.
        """
        dev = self._approved('EE:00:00:00:00:08', DeviceType.ROBOT)
        self._post({'mac': dev.device_id, 'friendly_name': 'Now a timer', 'device_type': 'LAPTIMER'})
        dev.refresh_from_db()
        self.assertEqual(dev.device_type, DeviceType.LAPTIMER)
        self.assertEqual(dev.registration_status, RegistrationStatus.PENDING)

    def test_device_type_change_is_reported_in_the_response(self):
        dev = self._approved('EE:00:00:00:00:03', DeviceType.LAPTIMER)
        r = self._post({'mac': dev.device_id, 'friendly_name': 'X', 'device_type': 'ROBOT'})
        self.assertEqual(r.json()['registration_status'], RegistrationStatus.ACTIVE)

    def test_a_new_robot_registers_active_but_a_new_timer_does_not(self):
        self._post({'mac': 'EE:00:00:00:00:09', 'friendly_name': 'R', 'device_type': 'ROBOT'})
        self._post({'mac': 'EE:00:00:00:00:0A', 'friendly_name': 'T', 'device_type': 'LAPTIMER'})
        self.assertEqual(
            LapTimerDevice.objects.get(device_id='EE:00:00:00:00:09').registration_status,
            RegistrationStatus.ACTIVE,
        )
        self.assertEqual(
            LapTimerDevice.objects.get(device_id='EE:00:00:00:00:0A').registration_status,
            RegistrationStatus.PENDING,
        )

    def test_registration_cannot_grant_approval(self):
        """A PENDING device stays PENDING however it re-registers."""
        self._post({'mac': 'EE:00:00:00:00:04', 'friendly_name': 'New'})
        self._post({
            'mac': 'EE:00:00:00:00:04', 'friendly_name': 'New',
            'registration_status': 'ACTIVE',
        })
        dev = LapTimerDevice.objects.get(device_id='EE:00:00:00:00:04')
        self.assertEqual(dev.registration_status, RegistrationStatus.PENDING)

    def test_omitted_device_type_leaves_the_type_alone(self):
        """A rename payload without device_type must not demote an approved robot."""
        dev = self._approved('EE:00:00:00:00:06', DeviceType.ROBOT)
        self._post({'mac': dev.device_id, 'friendly_name': 'Renamed Robot'})
        dev.refresh_from_db()
        self.assertEqual(dev.device_type, DeviceType.ROBOT)
        self.assertEqual(dev.registration_status, RegistrationStatus.ACTIVE)

    def test_resending_the_same_device_type_keeps_approval(self):
        dev = self._approved('EE:00:00:00:00:07', DeviceType.ROBOT)
        self._post({'mac': dev.device_id, 'friendly_name': 'Same', 'device_type': 'ROBOT'})
        dev.refresh_from_db()
        self.assertEqual(dev.registration_status, RegistrationStatus.ACTIVE)

    def test_country_and_school_still_update(self):
        dev = self._approved('EE:00:00:00:00:05')
        self._post({
            'mac': dev.device_id, 'friendly_name': 'Original',
            'country': 'Slovakia', 'school': 'TUKE',
        })
        dev.refresh_from_db()
        self.assertEqual(dev.country, 'Slovakia')
        self.assertEqual(dev.organisation, 'TUKE')


@override_settings(DEVICE_REG_SECRET='s3cr3t')
class DeviceRegistrationSecretTest(TestCase):
    """When DEVICE_REG_SECRET is configured, registration requires it."""

    URL = '/devices/register/'

    def _post(self, payload):
        return self.client.post(self.URL, data=json.dumps(payload), content_type='application/json')

    def test_correct_secret_is_accepted(self):
        r = self._post({'mac': 'FF:00:00:00:00:01', 'friendly_name': 'T', 'secret': 's3cr3t'})
        self.assertEqual(r.status_code, 201)

    def test_wrong_secret_is_rejected(self):
        r = self._post({'mac': 'FF:00:00:00:00:02', 'friendly_name': 'T', 'secret': 'nope'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(LapTimerDevice.objects.filter(device_id='FF:00:00:00:00:02').exists())

    def test_missing_secret_is_rejected(self):
        r = self._post({'mac': 'FF:00:00:00:00:03', 'friendly_name': 'T'})
        self.assertEqual(r.status_code, 403)

    def test_secret_unset_leaves_registration_open(self):
        with self.settings(DEVICE_REG_SECRET=''):
            r = self._post({'mac': 'FF:00:00:00:00:04', 'friendly_name': 'T'})
        self.assertEqual(r.status_code, 201)
