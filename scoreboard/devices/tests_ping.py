"""
Phase 5.21 section C — the connection test and live status.

Per the Phase G rule the suite never touches the network: HermeticTestRunner
swaps out paho's publish for the whole suite, and anything asserting on a
published command patches mqtt_bridge.publisher.publish.single directly.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contest.models import UserProfile
from devices.models import (
    DevicePing, DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)
from devices.services import (
    device_status_payload, ping_device, record_pong, sweep_offline_devices,
)


def make_robot(mac, name='Rover', owner=None, status=DeviceStatus.ONLINE):
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name=name, device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=owner,
        status=status, last_seen=timezone.now(),
    )


class PingPublishTest(TestCase):
    def setUp(self):
        self.robot = make_robot('11:00:00:00:00:01')

    def test_ping_publishes_to_the_robots_own_topic_with_a_nonce(self):
        with patch('mqtt_bridge.publisher.publish.single') as pub:
            ping = ping_device(self.robot)

        topic = pub.call_args.args[0]
        body  = json.loads(pub.call_args.kwargs['payload'])
        self.assertEqual(topic, f'robosteam/robot/{self.robot.device_id}/cmd')
        self.assertEqual(body['cmd'], 'PING')
        self.assertEqual(body['nonce'], ping.nonce)

    def test_ping_records_the_attempt_unanswered(self):
        with patch('mqtt_bridge.publisher.publish.single'):
            ping = ping_device(self.robot)
        self.assertIsNone(ping.replied_at)
        self.assertIsNone(ping.rtt_ms)

    def test_ping_to_a_lap_timer_uses_the_laptimer_topic(self):
        """Phase 5.21 F — a timer cannot subscribe to `robot/…` to hear a PING.

        Before this, every PING went to the robot topic family, so a lap timer
        could never answer one. That would have surfaced the first time anyone
        tested a timer, with the firmware already flashed.
        """
        timer = LapTimerDevice.objects.create(
            device_id='11:00:00:00:00:09', friendly_name='Track Timer',
            device_type=DeviceType.LAPTIMER,
            registration_status=RegistrationStatus.ACTIVE,
        )
        with patch('mqtt_bridge.publisher.publish.single') as pub:
            ping = ping_device(timer)

        self.assertEqual(
            pub.call_args.args[0], f'robosteam/laptimer/{timer.device_id}/cmd',
        )
        body = json.loads(pub.call_args.kwargs['payload'])
        self.assertEqual(body['cmd'], 'PING')
        self.assertEqual(body['nonce'], ping.nonce)

    def test_a_timers_pong_is_matched_on_its_own_status_topic(self):
        """The reply path already worked for timers — this pins it down."""
        timer = LapTimerDevice.objects.create(
            device_id='11:00:00:00:00:0A', friendly_name='Track Timer 2',
            device_type=DeviceType.LAPTIMER,
            registration_status=RegistrationStatus.ACTIVE,
        )
        with patch('mqtt_bridge.publisher.publish.single'):
            ping = ping_device(timer)

        matched = record_pong(timer.device_id, ping.nonce)
        self.assertIsNotNone(matched)
        self.assertIsNotNone(matched.rtt_ms)


class PongMatchingTest(TestCase):
    def setUp(self):
        self.robot = make_robot('11:00:00:00:00:02')
        with patch('mqtt_bridge.publisher.publish.single'):
            self.ping = ping_device(self.robot)

    def test_a_matching_pong_records_the_round_trip(self):
        ping = record_pong(self.robot.device_id, self.ping.nonce)
        self.assertIsNotNone(ping)
        self.ping.refresh_from_db()
        self.assertIsNotNone(self.ping.replied_at)
        self.assertIsNotNone(self.ping.rtt_ms)

    def test_an_unknown_nonce_is_ignored(self):
        self.assertIsNone(record_pong(self.robot.device_id, 'not-a-real-nonce'))
        self.ping.refresh_from_db()
        self.assertIsNone(self.ping.replied_at)

    def test_a_late_pong_cannot_be_credited_to_a_second_test(self):
        """
        The reason the nonce exists.

        A student presses Test twice. If the first robot's slow reply were matched
        to the newest outstanding ping, the second test would report a round-trip
        it never measured — the one number this feature exists to give them.
        """
        record_pong(self.robot.device_id, self.ping.nonce)
        with patch('mqtt_bridge.publisher.publish.single'):
            second = ping_device(self.robot)

        record_pong(self.robot.device_id, self.ping.nonce)   # the old nonce again
        second.refresh_from_db()
        self.assertIsNone(second.replied_at)

    def test_a_pong_for_another_device_is_not_matched(self):
        other = make_robot('11:00:00:00:00:03')
        self.assertIsNone(record_pong(other.device_id, self.ping.nonce))


class BridgePongHandlingTest(TestCase):
    """The PONG rides on the existing status topic — no extra subscription."""

    def setUp(self):
        from mqtt_bridge.management.commands.mqtt_bridge import handle_device_status
        self.handle = handle_device_status
        self.robot  = make_robot('13:00:00:00:00:01', status=DeviceStatus.OFFLINE)
        with patch('mqtt_bridge.publisher.publish.single'):
            self.ping = ping_device(self.robot)

    def test_a_pong_payload_completes_the_ping(self):
        with patch('devices.services.push_device_update'):
            self.handle(self.robot.device_id, DeviceType.ROBOT,
                        {'cmd': 'PONG', 'nonce': self.ping.nonce})
        self.ping.refresh_from_db()
        self.assertIsNotNone(self.ping.rtt_ms)

    def test_a_plain_heartbeat_brings_the_device_online(self):
        with patch('devices.services.push_device_update'):
            self.handle(self.robot.device_id, DeviceType.ROBOT, {})
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.status, DeviceStatus.ONLINE)

    def test_firmware_version_is_captured_from_the_heartbeat(self):
        with patch('devices.services.push_device_update'):
            self.handle(self.robot.device_id, DeviceType.ROBOT, {'firmware': '1.2.0'})
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.firmware_version, '1.2.0')

    def test_a_repeat_heartbeat_does_not_spam_the_websocket(self):
        """
        A robot beats every 30 s. Pushing on each one would wake every open page
        for a change nobody can see.
        """
        with patch('devices.services.push_device_update'):
            self.handle(self.robot.device_id, DeviceType.ROBOT, {})   # offline -> online
        with patch('mqtt_bridge.management.commands.mqtt_bridge.push_device_update') as push:
            self.handle(self.robot.device_id, DeviceType.ROBOT, {})   # already online
        push.assert_not_called()

    def test_a_robot_first_seen_over_mqtt_needs_no_approval(self):
        with patch('devices.services.push_device_update'):
            self.handle('13:00:00:00:00:09', DeviceType.ROBOT, {})
        self.assertEqual(
            LapTimerDevice.objects.get(device_id='13:00:00:00:00:09').registration_status,
            RegistrationStatus.ACTIVE,
        )

    def test_a_lap_timer_first_seen_over_mqtt_still_needs_approval(self):
        with patch('devices.services.push_device_update'):
            self.handle('13:00:00:00:00:0A', DeviceType.LAPTIMER, {})
        self.assertEqual(
            LapTimerDevice.objects.get(device_id='13:00:00:00:00:0A').registration_status,
            RegistrationStatus.PENDING,
        )


class StatusPayloadTest(TestCase):
    def test_payload_reports_heartbeat_age(self):
        robot = make_robot('11:00:00:00:00:04')
        payload = device_status_payload(robot)
        self.assertEqual(payload['status'], DeviceStatus.ONLINE)
        self.assertIsNotNone(payload['last_seen'])
        self.assertLess(payload['seconds_since_seen'], 5)

    def test_payload_handles_a_device_never_seen(self):
        robot = LapTimerDevice.objects.create(
            device_id='11:00:00:00:00:05', friendly_name='New',
            device_type=DeviceType.ROBOT)
        payload = device_status_payload(robot)
        self.assertIsNone(payload['last_seen'])
        self.assertIsNone(payload['seconds_since_seen'])


class OfflineSweepPushTest(TestCase):
    def test_the_sweep_pushes_the_badge_change(self):
        """
        H7 again, one layer up: the sweep already flipped the DB, but without a
        push an open page keeps showing green until someone reloads it.
        """
        stale = timezone.now() - timedelta(seconds=600)
        robot = make_robot('11:00:00:00:00:06')
        LapTimerDevice.objects.filter(pk=robot.pk).update(last_seen=stale)

        with patch('devices.services.push_device_update') as push:
            changed = sweep_offline_devices(threshold_seconds=90)

        self.assertEqual(changed, 1)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.args[0].device_id, robot.device_id)


class RobotTestViewTest(TestCase):
    def setUp(self):
        group, _  = Group.objects.get_or_create(name='Participant')
        self.alice = User.objects.create_user('alice', password='pw12345!')
        self.alice.groups.add(group)
        UserProfile.objects.create(user=self.alice, requested_role='participant')

        self.bob = User.objects.create_user('bob', password='pw12345!')
        self.bob.groups.add(group)
        UserProfile.objects.create(user=self.bob, requested_role='participant')

        self.robot = make_robot('12:00:00:00:00:01', owner=self.alice)
        self.client.login(username='alice', password='pw12345!')

    def test_testing_my_robot_returns_the_nonce(self):
        with patch('mqtt_bridge.publisher.publish.single'):
            r = self.client.post(reverse('contest:robot_test', args=[self.robot.id]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['nonce'], DevicePing.objects.get().nonce)

    def test_a_broker_failure_is_reported_not_a_500(self):
        """
        A dead broker is the most likely reason a student is testing at all, so
        it has to come back as a sentence rather than an error page.
        """
        with patch('mqtt_bridge.publisher.publish.single', side_effect=OSError('no broker')):
            r = self.client.post(reverse('contest:robot_test', args=[self.robot.id]))
        self.assertEqual(r.status_code, 503)
        self.assertIn('broker', r.json()['error'])

    def test_i_cannot_test_someone_elses_robot(self):
        theirs = make_robot('12:00:00:00:00:02', owner=self.bob)
        with patch('mqtt_bridge.publisher.publish.single') as pub:
            r = self.client.post(reverse('contest:robot_test', args=[theirs.id]))
        self.assertEqual(r.status_code, 404)
        pub.assert_not_called()

    def test_get_is_refused(self):
        with patch('mqtt_bridge.publisher.publish.single') as pub:
            r = self.client.get(reverse('contest:robot_test', args=[self.robot.id]))
        self.assertEqual(r.status_code, 405)
        pub.assert_not_called()

    def test_status_endpoint_returns_freshness_for_my_robot(self):
        r = self.client.get(reverse('contest:robot_status', args=[self.robot.id]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['device_id'], self.robot.device_id)

    def test_status_endpoint_is_owner_scoped(self):
        theirs = make_robot('12:00:00:00:00:03', owner=self.bob)
        r = self.client.get(reverse('contest:robot_status', args=[theirs.id]))
        self.assertEqual(r.status_code, 404)
