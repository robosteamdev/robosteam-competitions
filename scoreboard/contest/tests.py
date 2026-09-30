import json
import secrets

from django.contrib.auth.models import Group, User
from django.test import Client, TestCase

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Result, Run, RunState, Team,
)
from devices.models import DeviceType, LapTimerDevice


def _tok():
    return secrets.token_hex(8)


def _make_setup(status=ItemStates.OPEN, ctype=CompetitionType.TIMED, num_laps=1, num_runs=3):
    contest = Contest.objects.create(name='Test Contest')
    comp = Competition.objects.create(
        name='Test Category', contest=contest,
        competition_type=ctype, status=status,
        num_laps=num_laps, num_runs=num_runs,
        token=_tok(),
    )
    team = Team.objects.create(name='Team A', contest=contest, token=_tok())
    return contest, comp, team


def _make_active_run(team, comp):
    from django.utils import timezone
    return Run.objects.create(
        team=team, competition=comp,
        start_time=timezone.now(), duration=0,
        state=RunState.ACTIVE,
    )


def _make_pending_run(team, comp):
    from django.utils import timezone
    return Run.objects.create(
        team=team, competition=comp,
        start_time=timezone.now(), duration=0,
        state=RunState.PENDING,
    )


def _judge_user():
    """Create a test judge user (or get existing one), in the Judge group."""
    u, _ = User.objects.get_or_create(username='testjudge')
    u.set_password('test')
    u.save()
    judge_group, _ = Group.objects.get_or_create(name='Judge')
    u.groups.add(judge_group)
    return u


# ── BUG-01 regression ─────────────────────────────────────────────────────────

class BUG01ContestTableTimedOpenTest(TestCase):
    """contest_competitions must show Result.score for TIMED+OPEN categories, not None."""

    def setUp(self):
        self.client = Client()
        self.user = _judge_user()
        self.client.force_login(self.user)
        self.contest, self.comp, self.team = _make_setup(
            status=ItemStates.OPEN, ctype=CompetitionType.TIMED
        )
        Result.objects.create(team=self.team, competition=self.comp, score=15002)

    def test_timed_open_score_rendered_not_none(self):
        resp = self.client.get(f'/contest/contest/{self.contest.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '15002')

    def test_none_not_in_table_for_timed_open(self):
        resp = self.client.get(f'/contest/contest/{self.contest.id}')
        content = resp.content.decode()
        # Should not render a bare "None" cell for a team that has a Result
        self.assertNotIn('> None<', content)
        self.assertNotIn('>None<', content)


class BUG01ContestTableJudgedOpenTest(TestCase):
    """JUDGED+OPEN column still shows correctly after BUG-01 fix."""

    def setUp(self):
        self.client = Client()
        self.user = _judge_user()
        self.client.force_login(self.user)
        self.contest, self.comp, self.team = _make_setup(
            status=ItemStates.OPEN, ctype=CompetitionType.JUDGED
        )
        Result.objects.create(team=self.team, competition=self.comp, score=82)

    def test_judged_open_score_rendered(self):
        resp = self.client.get(f'/contest/contest/{self.contest.id}')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '82')


# ── BUG-02 regression ─────────────────────────────────────────────────────────

class BUG02RunCreateStatusGuardTest(TestCase):
    """run_create must return 400 when competition.status is not OPEN."""

    def setUp(self):
        self.client = Client()
        self.user = _judge_user()
        self.client.force_login(self.user)

    def test_run_create_open_competition_succeeds(self):
        _, comp, team = _make_setup(status=ItemStates.OPEN)
        resp = self.client.post(
            f'/contest/competition/{comp.id}/run/create',
            {'team_id': team.id},
        )
        self.assertIn(resp.status_code, [200, 302])
        self.assertEqual(Run.objects.filter(team=team, competition=comp).count(), 1)

    def test_run_create_closed_competition_rejected(self):
        _, comp, team = _make_setup(status=ItemStates.CLOSED)
        resp = self.client.post(
            f'/contest/competition/{comp.id}/run/create',
            {'team_id': team.id},
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn('not open', data['error'])
        self.assertEqual(Run.objects.filter(team=team, competition=comp).count(), 0)

    def test_run_create_new_status_rejected(self):
        _, comp, team = _make_setup(status=ItemStates.NEW)
        resp = self.client.post(
            f'/contest/competition/{comp.id}/run/create',
            {'team_id': team.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_run_limit_still_enforced_for_open(self):
        _, comp, team = _make_setup(status=ItemStates.OPEN, num_runs=1)
        # First run: succeeds
        self.client.post(
            f'/contest/competition/{comp.id}/run/create',
            {'team_id': team.id},
        )
        # Second run: exceeds limit
        resp = self.client.post(
            f'/contest/competition/{comp.id}/run/create',
            {'team_id': team.id},
        )
        self.assertEqual(resp.status_code, 400)

    def test_run_start_completed_rejected(self):
        _, comp, team = _make_setup(status=ItemStates.OPEN)
        run = _make_pending_run(team, comp)
        run.state = RunState.COMPLETED
        run.save()
        resp = self.client.post(f'/contest/run/{run.id}/start')
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn('COMPLETED', data['error'])

    def test_run_stop_completed_rejected(self):
        _, comp, team = _make_setup(status=ItemStates.OPEN)
        run = _make_pending_run(team, comp)
        run.state = RunState.COMPLETED
        run.save()
        resp = self.client.post(f'/contest/run/{run.id}/stop')
        self.assertEqual(resp.status_code, 400)


# ── Score validation guards ───────────────────────────────────────────────────

class RunScoreValidationTest(TestCase):
    """run_score must reject invalid scores and wrong competition type."""

    def setUp(self):
        self.client = Client()
        self.user = _judge_user()
        self.client.force_login(self.user)
        _, self.judged_comp, self.team = _make_setup(
            status=ItemStates.OPEN, ctype=CompetitionType.JUDGED
        )
        _, self.timed_comp, _ = _make_setup(
            status=ItemStates.OPEN, ctype=CompetitionType.TIMED
        )

    def _pending_run(self, comp):
        return _make_pending_run(self.team, comp)

    def test_score_zero_rejected(self):
        run = self._pending_run(self.judged_comp)
        resp = self.client.post(f'/contest/run/{run.id}/score', {'score': 0})
        self.assertEqual(resp.status_code, 400)

    def test_score_101_rejected(self):
        run = self._pending_run(self.judged_comp)
        resp = self.client.post(f'/contest/run/{run.id}/score', {'score': 101})
        self.assertEqual(resp.status_code, 400)

    def test_score_on_timed_run_rejected(self):
        run = _make_pending_run(self.team, self.timed_comp)
        resp = self.client.post(f'/contest/run/{run.id}/score', {'score': 75})
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn('JUDGED', data['error'])

    def test_valid_score_accepted(self):
        run = self._pending_run(self.judged_comp)
        resp = self.client.post(f'/contest/run/{run.id}/score', {'score': 75})
        self.assertIn(resp.status_code, [200, 302])
        run.refresh_from_db()
        self.assertEqual(run.score, 75)
        self.assertEqual(run.state, RunState.COMPLETED)

# ── Phase 5.7: WebSocket consumer + fragment view tests ───────────────────────

from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase

from scoreboard.asgi import application as asgi_app


class CompetitionConsumerConnectTest(SimpleTestCase):
    """Consumer accepts connections and joins the competition group."""

    async def test_consumer_connects(self):
        communicator = WebsocketCommunicator(asgi_app, '/ws/competition/1/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_consumer_accepts_different_competition_ids(self):
        c1 = WebsocketCommunicator(asgi_app, '/ws/competition/1/')
        c2 = WebsocketCommunicator(asgi_app, '/ws/competition/99/')
        ok1, _ = await c1.connect()
        ok2, _ = await c2.connect()
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        await c1.disconnect()
        await c2.disconnect()

    async def test_consumer_receives_competition_update(self):
        communicator = WebsocketCommunicator(asgi_app, '/ws/competition/42/')
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            'competition_42',
            {'type': 'competition.update', 'competition_id': 42},
        )

        response = await communicator.receive_json_from(timeout=2)
        self.assertEqual(response['competition_id'], 42)
        await communicator.disconnect()

    async def test_update_not_delivered_to_wrong_competition(self):
        c1 = WebsocketCommunicator(asgi_app, '/ws/competition/1/')
        c2 = WebsocketCommunicator(asgi_app, '/ws/competition/2/')
        await c1.connect()
        await c2.connect()

        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            'competition_1',
            {'type': 'competition.update', 'competition_id': 1},
        )

        msg = await c1.receive_json_from(timeout=2)
        self.assertEqual(msg['competition_id'], 1)
        self.assertTrue(await c2.receive_nothing(timeout=0.5))

        await c1.disconnect()
        await c2.disconnect()


class BoardFragmentViewTest(TestCase):
    """competition_board_fragment view returns 200 with run table HTML."""

    def setUp(self):
        self.contest = Contest.objects.create(name='Frag Test Cup')
        self.comp = Competition.objects.create(
            name='Frag Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED, num_laps=1,
            status=ItemStates.OPEN, token=_tok(),
        )
        self.team = Team.objects.create(name='Team A', contest=self.contest, token=None)

    def test_fragment_returns_200(self):
        r = self.client.get(f'/contest/competition/{self.comp.id}/fragment/')
        self.assertEqual(r.status_code, 200)

    def test_fragment_contains_team_name_in_dropdown(self):
        # The Create Run dropdown is a judge-only control (Phase D show_controls).
        self.client.force_login(_judge_user())
        r = self.client.get(f'/contest/competition/{self.comp.id}/fragment/')
        self.assertContains(r, 'Team A')

    def test_fragment_404_for_unknown_competition(self):
        r = self.client.get('/contest/competition/99999/fragment/')
        self.assertEqual(r.status_code, 404)

    def test_fragment_shows_run_state(self):
        from django.utils import timezone
        Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now(), duration=0, state=RunState.PENDING,
        )
        r = self.client.get(f'/contest/competition/{self.comp.id}/fragment/')
        self.assertContains(r, 'Team A')
        self.assertContains(r, 'Pending')
