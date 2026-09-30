"""Phase G1 (H9) — what actually goes on the wire when a judge starts or stops a run.

Before this, every publish was wrapped in `except Exception: pass` and no test
patched the publisher, so the START/STOP commands were completely unverified:
a wrong topic or a broken payload would have passed the whole suite. These tests
patch `mqtt_bridge.publisher.publish.single` (the paho entry point) and assert
the topic, the JSON payload and the connection arguments.
"""
import json
import secrets
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.utils import timezone

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team,
)
from mqtt_bridge.publisher import publish_competition_command, publish_robot_command


def _judge(username='mqtt-judge'):
    user = User.objects.create_user(username=username, password='test')
    group, _ = Group.objects.get_or_create(name='Judge')
    user.groups.add(group)
    return user


@override_settings(
    MQTT_BROKER_HOST='broker.test', MQTT_BROKER_PORT=1883,
    MQTT_USERNAME='test-mqtt-user', MQTT_PASSWORD='test-mqtt-password',
)
class PublisherPayloadTest(TestCase):
    """The publisher helpers, called directly."""

    def test_competition_command_topic_and_payload(self):
        with patch('mqtt_bridge.publisher.publish.single') as single:
            publish_competition_command(7, 'START', run_id=42)

        topic = single.call_args.args[0]
        kwargs = single.call_args.kwargs
        self.assertEqual(topic, 'robosteam/competition/7/cmd')
        self.assertEqual(json.loads(kwargs['payload']), {'cmd': 'START', 'run_id': 42})
        self.assertEqual(kwargs['hostname'], 'broker.test')
        self.assertEqual(kwargs['port'], 1883)
        self.assertEqual(kwargs['qos'], 1)
        self.assertEqual(
            kwargs['auth'], {'username': 'test-mqtt-user', 'password': 'test-mqtt-password'},
        )

    def test_robot_command_topic_and_payload(self):
        with patch('mqtt_bridge.publisher.publish.single') as single:
            publish_robot_command('A4:CF:12:B7:3D:E1', 'STOP')

        self.assertEqual(
            single.call_args.args[0], 'robosteam/robot/A4:CF:12:B7:3D:E1/cmd',
        )
        self.assertEqual(json.loads(single.call_args.kwargs['payload']), {'cmd': 'STOP'})


class RunCommandPublishTest(TestCase):
    """The judge endpoints that trigger a publish."""

    def setUp(self):
        self.contest = Contest.objects.create(name='MQTT Cup', status=ItemStates.OPEN)
        self.timed = Competition.objects.create(
            name='Timed Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_laps=1, num_runs=5,
            token=secrets.token_hex(8),
        )
        self.judged = Competition.objects.create(
            name='Judged Cat', contest=self.contest,
            competition_type=CompetitionType.JUDGED,
            status=ItemStates.OPEN, num_laps=1, num_runs=5,
            token=secrets.token_hex(8),
        )
        self.team = Team.objects.create(name='MQTT Team', contest=self.contest)
        self.client.force_login(_judge())

    def _run(self, competition, state=RunState.PENDING):
        return Run.objects.create(
            team=self.team, competition=competition,
            start_time=timezone.now(), duration=0, state=state,
        )

    def test_start_publishes_start_command(self):
        run = self._run(self.timed)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            response = self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(response.status_code, 200)
        single.assert_called_once()
        self.assertEqual(
            single.call_args.args[0], f'robosteam/competition/{self.timed.id}/cmd',
        )
        self.assertEqual(
            json.loads(single.call_args.kwargs['payload']),
            {'cmd': 'START', 'run_id': run.id},
        )

    def test_stop_publishes_stop_command(self):
        run = self._run(self.timed, state=RunState.ACTIVE)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            response = self.client.post(f'/contest/run/{run.id}/stop')

        self.assertEqual(response.status_code, 200)
        single.assert_called_once()
        self.assertEqual(
            single.call_args.args[0], f'robosteam/competition/{self.timed.id}/cmd',
        )
        self.assertEqual(json.loads(single.call_args.kwargs['payload']), {'cmd': 'STOP'})

    def test_judged_run_publishes_nothing(self):
        """A judged category has no timer to arm — no command should go out."""
        run = self._run(self.judged)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            self.client.post(f'/contest/run/{run.id}/start')
        single.assert_not_called()

    def test_broker_failure_does_not_break_the_run(self):
        """The judge's START still takes effect (and is logged) with the broker down.

        The logger moved from `contest.views` to `mqtt_bridge.publisher` in
        Phase 5.21 E: START now fans out to two topics, and each has to survive
        the other failing, so the handling belongs next to the publish.
        """
        run = self._run(self.timed)
        with patch(
            'mqtt_bridge.publisher.publish.single', side_effect=OSError('broker down'),
        ):
            with self.assertLogs('mqtt_bridge.publisher', level='ERROR') as logs:
                response = self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)
        self.assertIn('MQTT publish failed', '\n'.join(logs.output))


class TimeoutVoidPublishTest(TestCase):
    """The auto-timeout sweep must send STOP as well (scoring.engine)."""

    def setUp(self):
        self.contest = Contest.objects.create(name='Timeout Cup', status=ItemStates.OPEN)
        self.comp = Competition.objects.create(
            name='Timeout Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_laps=1, num_runs=5, timeout_seconds=30,
            token=secrets.token_hex(8),
        )
        self.team = Team.objects.create(name='Timeout Team', contest=self.contest)

    def test_timed_out_run_publishes_stop(self):
        from datetime import timedelta

        from scoring.engine import check_and_void_timed_out_runs

        run = Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now() - timedelta(seconds=60),
            duration=0, state=RunState.ACTIVE,
        )
        with patch('mqtt_bridge.publisher.publish.single') as single:
            voided = check_and_void_timed_out_runs()

        self.assertEqual(voided, 1)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)
        single.assert_called_once()
        self.assertEqual(
            json.loads(single.call_args.kwargs['payload']), {'cmd': 'STOP'},
        )
