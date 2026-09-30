from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone

from devices.models import LapTimerDevice, LapEvent, DeviceStatus, DeviceType
from mqtt_bridge.management.commands.mqtt_bridge import (
    handle_device_status, handle_lap_event, mark_intentional_disconnect, on_disconnect,
)


class HandleLapEventTest(TestCase):
    MAC = 'AA:BB:CC:DD:EE:FF'

    def test_creates_device_and_lap_event(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:00Z'})
        self.assertEqual(LapTimerDevice.objects.count(), 1)
        self.assertEqual(LapEvent.objects.count(), 1)

    def test_device_auto_registered_with_mac_as_name(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:00Z'})
        device = LapTimerDevice.objects.get(device_id=self.MAC)
        self.assertEqual(device.friendly_name, self.MAC)
        self.assertEqual(device.device_type, DeviceType.LAPTIMER)

    def test_device_marked_online(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:00Z'})
        device = LapTimerDevice.objects.get(device_id=self.MAC)
        self.assertEqual(device.status, DeviceStatus.ONLINE)
        self.assertIsNotNone(device.last_seen)

    def test_duplicate_sequence_ignored(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:00Z'})
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:01Z'})
        self.assertEqual(LapEvent.objects.count(), 1)

    def test_multiple_sequences_recorded(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:00Z'})
        handle_lap_event(self.MAC, {'seq': 2, 'ts': '2026-06-27T10:00:05Z'})
        self.assertEqual(LapEvent.objects.count(), 2)

    def test_missing_seq_ignored(self):
        handle_lap_event(self.MAC, {'ts': '2026-06-27T10:00:00Z'})
        self.assertEqual(LapEvent.objects.count(), 0)

    def test_bad_timestamp_ignored(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': 'not-a-date'})
        self.assertEqual(LapEvent.objects.count(), 0)

    def test_lap_event_competition_is_null(self):
        handle_lap_event(self.MAC, {'seq': 1, 'ts': '2026-06-27T10:00:00Z'})
        event = LapEvent.objects.get()
        self.assertIsNone(event.competition)
        self.assertIsNone(event.run)


class HandleDeviceStatusTest(TestCase):
    MAC = '11:22:33:44:55:66'

    def test_creates_device_on_first_heartbeat(self):
        handle_device_status(self.MAC, DeviceType.LAPTIMER)
        self.assertEqual(LapTimerDevice.objects.count(), 1)

    def test_robot_device_type(self):
        handle_device_status(self.MAC, DeviceType.ROBOT)
        device = LapTimerDevice.objects.get(device_id=self.MAC)
        self.assertEqual(device.device_type, DeviceType.ROBOT)

    def test_existing_device_updated_not_duplicated(self):
        handle_device_status(self.MAC, DeviceType.LAPTIMER)
        handle_device_status(self.MAC, DeviceType.LAPTIMER)
        self.assertEqual(LapTimerDevice.objects.count(), 1)

    def test_device_marked_online(self):
        handle_device_status(self.MAC, DeviceType.LAPTIMER)
        device = LapTimerDevice.objects.get(device_id=self.MAC)
        self.assertEqual(device.status, DeviceStatus.ONLINE)
        self.assertIsNotNone(device.last_seen)


class OnDisconnectTest(TestCase):
    """Phase E5 (H5): a deliberate shutdown must not void runs in progress."""

    def tearDown(self):
        mark_intentional_disconnect(False)

    def _call_on_disconnect(self):
        on_disconnect(None, None, None, 0, None)

    def test_unexpected_disconnect_voids_runs(self):
        with patch('scoring.engine.void_active_runs_on_disconnect', return_value=2) as voider:
            self._call_on_disconnect()
        voider.assert_called_once()

    def test_intentional_disconnect_voids_nothing(self):
        mark_intentional_disconnect()
        with patch('scoring.engine.void_active_runs_on_disconnect') as voider:
            self._call_on_disconnect()
        voider.assert_not_called()

    def test_flag_can_be_cleared(self):
        mark_intentional_disconnect()
        mark_intentional_disconnect(False)
        with patch('scoring.engine.void_active_runs_on_disconnect', return_value=0) as voider:
            self._call_on_disconnect()
        voider.assert_called_once()
