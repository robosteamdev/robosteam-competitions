"""Phase 5.21 E — a START must reach one robot, not every robot in the category.

Before this, `publish_robot_command()` existed but was never called: START and
STOP went out only on `robosteam/competition/{id}/cmd`, which every robot in
the category is subscribed to. Send off Team Alpha and Bravo's robot drives too.

`Team.robot` (section A) makes targeting possible, so these tests pin down what
goes on which topic. They patch `mqtt_bridge.publisher.publish.single` — the
paho entry point — per the Phase G hermetic rule; the suite never touches a
broker.
"""
import json
import secrets
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team,
)
from devices.models import DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus


def _judge(username='targeting-judge'):
    user = User.objects.create_user(username=username, password='test')
    group, _ = Group.objects.get_or_create(name='Judge')
    user.groups.add(group)
    return user


def _robot(mac, name, status=DeviceStatus.ONLINE, last_seen=None):
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name=name, device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, status=status,
        last_seen=last_seen if last_seen is not None else timezone.now(),
    )


def _topics(single):
    """Every topic published in this patched block, in call order."""
    return [call.args[0] for call in single.call_args_list]


def _payload_for(single, topic):
    """The decoded payload published to `topic`. Fails loudly if it wasn't."""
    for call in single.call_args_list:
        if call.args[0] == topic:
            return json.loads(call.kwargs['payload'])
    raise AssertionError(f'nothing was published to {topic}; got {_topics(single)}')


class TargetingFixture(TestCase):
    """Two teams, two robots, one TIMED category and one JUDGED."""

    def setUp(self):
        self.contest = Contest.objects.create(
            name='Targeting Cup', status=ItemStates.OPEN,
        )
        self.timed = Competition.objects.create(
            name='Line Following', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_laps=1, num_runs=5, timeout_seconds=30,
            token=secrets.token_hex(8),
        )
        self.judged = Competition.objects.create(
            name='Arm Task', contest=self.contest,
            competition_type=CompetitionType.JUDGED,
            status=ItemStates.OPEN, num_laps=1, num_runs=5,
            token=secrets.token_hex(8),
        )
        self.alpha_robot = _robot('AA:AA:AA:AA:AA:01', 'Alpha Bot')
        self.bravo_robot = _robot('BB:BB:BB:BB:BB:02', 'Bravo Bot')

        self.alpha = Team.objects.create(
            name='Team Alpha', contest=self.contest, robot=self.alpha_robot,
        )
        self.bravo = Team.objects.create(
            name='Team Bravo', contest=self.contest, robot=self.bravo_robot,
        )
        self.nobody = Team.objects.create(name='Team Nobody', contest=self.contest)

    def run_for(self, team, competition=None, state=RunState.PENDING):
        return Run.objects.create(
            team=team, competition=competition or self.timed,
            start_time=timezone.now(), duration=0, state=state,
        )

    @property
    def broadcast_topic(self):
        return f'robosteam/competition/{self.timed.id}/cmd'


class StartTargetingTest(TargetingFixture):
    """What a judge's START puts on the wire."""

    def setUp(self):
        super().setUp()
        self.client.force_login(_judge())

    def test_start_publishes_broadcast_and_targeted(self):
        run = self.run_for(self.alpha)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            response = self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(_topics(single)),
            sorted([self.broadcast_topic, 'robosteam/robot/AA:AA:AA:AA:AA:01/cmd']),
        )
        self.assertEqual(
            _payload_for(single, 'robosteam/robot/AA:AA:AA:AA:AA:01/cmd'),
            {'cmd': 'START', 'run_id': run.id},
        )

    def test_start_does_not_reach_another_teams_robot(self):
        """The whole point of the section: Bravo's robot stays still."""
        run = self.run_for(self.alpha)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            self.client.post(f'/contest/run/{run.id}/start')

        self.assertNotIn('robosteam/robot/BB:BB:BB:BB:BB:02/cmd', _topics(single))

    def test_broadcast_payload_is_unchanged(self):
        """Already-flashed lap timers must see exactly what they saw before."""
        run = self.run_for(self.alpha)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(
            _payload_for(single, self.broadcast_topic),
            {'cmd': 'START', 'run_id': run.id},
        )

    def test_team_without_a_robot_publishes_broadcast_only(self):
        run = self.run_for(self.nobody)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            response = self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_topics(single), [self.broadcast_topic])

    def test_judged_category_publishes_nothing_even_with_a_robot(self):
        """A JUDGED category has no timing hardware to arm — it stays silent."""
        run = self.run_for(self.alpha, competition=self.judged)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            self.client.post(f'/contest/run/{run.id}/start')

        single.assert_not_called()

    def test_targeted_publish_failure_leaves_the_run_started(self):
        """A dead broker is a logged warning, never a lost run."""
        run = self.run_for(self.alpha)
        with patch(
            'mqtt_bridge.publisher.publish.single', side_effect=OSError('broker down'),
        ):
            with self.assertLogs('mqtt_bridge.publisher', level='ERROR') as logs:
                response = self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)
        # Both publishes are attempted and both are reported: one failing must
        # not skip the other.
        output = '\n'.join(logs.output)
        self.assertIn('broadcast START', output)
        self.assertIn('targeted START', output)


class StopTargetingTest(TargetingFixture):
    """Every route out of an ACTIVE run has to reach the robot too."""

    def test_judge_void_targets_the_bound_robot(self):
        self.client.force_login(_judge())
        run = self.run_for(self.alpha, state=RunState.ACTIVE)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            response = self.client.post(f'/contest/run/{run.id}/stop')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _payload_for(single, 'robosteam/robot/AA:AA:AA:AA:AA:01/cmd'),
            {'cmd': 'STOP', 'run_id': run.id},
        )
        # The broadcast STOP carries no run_id and did not gain one.
        self.assertEqual(_payload_for(single, self.broadcast_topic), {'cmd': 'STOP'})

    def test_timeout_void_targets_the_bound_robot(self):
        from scoring.engine import check_and_void_timed_out_runs

        run = Run.objects.create(
            team=self.alpha, competition=self.timed,
            start_time=timezone.now() - timedelta(seconds=60),
            duration=0, state=RunState.ACTIVE,
        )
        with patch('mqtt_bridge.publisher.publish.single') as single:
            voided = check_and_void_timed_out_runs()

        self.assertEqual(voided, 1)
        self.assertEqual(
            _payload_for(single, 'robosteam/robot/AA:AA:AA:AA:AA:01/cmd'),
            {'cmd': 'STOP', 'run_id': run.id, 'reason': 'timeout'},
        )
        self.assertEqual(_payload_for(single, self.broadcast_topic), {'cmd': 'STOP'})

    def test_finalize_stops_the_robot_and_does_not_broadcast(self):
        """A run that completes on its own still has to tell the robot to stop.

        Nothing did this before section E — the run finalized on the finish-line
        crossing and the robot kept driving. No broadcast: the timer just
        published the crossing that ended the run, so it needs no instruction,
        and this path has never sent one.
        """
        from devices.models import LapEvent

        timer = LapTimerDevice.objects.create(
            device_id='CC:CC:CC:CC:CC:03', friendly_name='Sim Timer',
            device_type=DeviceType.LAPTIMER,
            registration_status=RegistrationStatus.ACTIVE,
        )
        self.timed.lap_timer = timer
        self.timed.save(update_fields=['lap_timer'])

        run = self.run_for(self.alpha, state=RunState.ACTIVE)
        start = timezone.now()

        # Go through the real path: the post_save signal assigns each crossing
        # and finalizes on the last one. Calling try_finalize_run by hand
        # afterwards would find the run already COMPLETED and do nothing.
        with patch('mqtt_bridge.publisher.publish.single') as single:
            for i in range(2):  # num_laps=1 → start crossing + 1 lap
                LapEvent.objects.create(
                    device=timer, timestamp_utc=start + timedelta(seconds=5 * i),
                    sequence=100 + i,
                )

        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.time_ms, 5000)
        self.assertEqual(_topics(single), ['robosteam/robot/AA:AA:AA:AA:AA:01/cmd'])
        self.assertEqual(
            json.loads(single.call_args.kwargs['payload']),
            {'cmd': 'STOP', 'run_id': run.id, 'reason': 'finished'},
        )

    def test_disconnect_void_publishes_nothing(self):
        """The broker is what just went away — publishing would only block.

        `publish.single` would sit in a connect attempt per voided run, inside
        the on_disconnect callback, delaying the reconnect that is the only way
        back.
        """
        from scoring.engine import void_active_runs_on_disconnect

        self.run_for(self.alpha, state=RunState.ACTIVE)
        with patch('mqtt_bridge.publisher.publish.single') as single:
            voided = void_active_runs_on_disconnect()

        self.assertEqual(voided, 1)
        single.assert_not_called()


class PreflightViewTest(TargetingFixture):
    """`/contest/run/<id>/preflight` — warns about an offline robot, never blocks."""

    def setUp(self):
        super().setUp()
        self.client.force_login(_judge())

    def test_online_robot_produces_no_warning(self):
        run = self.run_for(self.alpha)
        data = self.client.get(f'/contest/run/{run.id}/preflight').json()

        self.assertEqual(data['warnings'], [])
        self.assertEqual(data['robot']['friendly_name'], 'Alpha Bot')
        self.assertEqual(data['team'], 'Team Alpha')

    def test_offline_robot_warns_and_names_it(self):
        self.alpha_robot.status = DeviceStatus.OFFLINE
        self.alpha_robot.last_seen = timezone.now() - timedelta(minutes=4)
        self.alpha_robot.save(update_fields=['status', 'last_seen'])

        run = self.run_for(self.alpha)
        data = self.client.get(f'/contest/run/{run.id}/preflight').json()

        self.assertEqual(len(data['warnings']), 1)
        self.assertIn('Alpha Bot', data['warnings'][0])
        self.assertIn('OFFLINE', data['warnings'][0])
        self.assertIn('4 min ago', data['warnings'][0])

    def test_offline_robot_does_not_block_the_start(self):
        """Locked decision: offline detection lags 90 s, so a gate would stall a heat."""
        self.alpha_robot.status = DeviceStatus.OFFLINE
        self.alpha_robot.save(update_fields=['status'])

        run = self.run_for(self.alpha)
        with patch('mqtt_bridge.publisher.publish.single'):
            response = self.client.post(f'/contest/run/{run.id}/start')

        self.assertEqual(response.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)

    def test_team_without_a_robot_is_not_a_warning(self):
        """The ordinary case for an organiser-created team — not worth a dialog."""
        run = self.run_for(self.nobody)
        data = self.client.get(f'/contest/run/{run.id}/preflight').json()

        self.assertIsNone(data['robot'])
        self.assertEqual(data['warnings'], [])

    def test_judged_category_never_warns(self):
        """No MQTT goes out for a JUDGED run, so a robot warning would be noise."""
        self.alpha_robot.status = DeviceStatus.OFFLINE
        self.alpha_robot.save(update_fields=['status'])

        run = self.run_for(self.alpha, competition=self.judged)
        data = self.client.get(f'/contest/run/{run.id}/preflight').json()

        self.assertEqual(data['warnings'], [])

    def test_mac_address_is_never_returned(self):
        """Judges and organisers work with friendly names everywhere (CLAUDE.md)."""
        run = self.run_for(self.alpha)
        body = self.client.get(f'/contest/run/{run.id}/preflight').content.decode()

        self.assertNotIn('AA:AA:AA:AA:AA:01', body)

    def test_anonymous_cannot_read_preflight(self):
        run = self.run_for(self.alpha)
        self.client.logout()
        response = self.client.get(f'/contest/run/{run.id}/preflight')

        self.assertIn(response.status_code, (302, 403))

    def test_participant_cannot_read_preflight(self):
        run = self.run_for(self.alpha)
        self.client.logout()
        participant = User.objects.create_user(username='pf-participant', password='test')
        group, _ = Group.objects.get_or_create(name='Participant')
        participant.groups.add(group)
        self.client.force_login(participant)

        response = self.client.get(f'/contest/run/{run.id}/preflight')
        self.assertIn(response.status_code, (302, 403))
