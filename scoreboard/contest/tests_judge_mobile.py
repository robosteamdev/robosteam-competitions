import secrets

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from contest.models import Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team


def _tok():
    return secrets.token_hex(8)


def _setup():
    contest = Contest.objects.create(name='Mobile Test Cup', status=ItemStates.OPEN)
    timed = Competition.objects.create(
        name='Line Following', contest=contest,
        competition_type=CompetitionType.TIMED,
        status=ItemStates.OPEN, num_laps=1, num_runs=3, token=_tok(),
    )
    judged = Competition.objects.create(
        name='Arm Task', contest=contest,
        competition_type=CompetitionType.JUDGED,
        status=ItemStates.OPEN, num_laps=1, num_runs=3, token=_tok(),
    )
    team = Team.objects.create(name='Alpha', contest=contest, token=_tok())
    return contest, timed, judged, team


def _judge():
    u, _ = User.objects.get_or_create(username='mobilejudge')
    u.set_password('test')
    u.save()
    judge_group, _ = Group.objects.get_or_create(name='Judge')
    u.groups.add(judge_group)
    return u


class JudgeMobileAuthTest(TestCase):
    """Unauthenticated access redirects to login."""

    def setUp(self):
        _, self.timed, _, _ = _setup()

    def test_select_redirects_anon(self):
        r = self.client.get('/contest/judge/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_panel_redirects_anon(self):
        r = self.client.get(f'/contest/judge/{self.timed.id}/')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])


class JudgeMobileSelectTest(TestCase):
    """Authenticated judge sees OPEN categories on the select screen."""

    def setUp(self):
        self.contest, self.timed, self.judged, _ = _setup()
        self.client.force_login(_judge())

    def test_select_returns_200(self):
        r = self.client.get('/contest/judge/')
        self.assertEqual(r.status_code, 200)

    def test_select_shows_open_categories(self):
        r = self.client.get('/contest/judge/')
        self.assertContains(r, 'Line Following')
        self.assertContains(r, 'Arm Task')

    def test_select_hides_closed_categories(self):
        closed = Competition.objects.create(
            name='Closed Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.CLOSED, num_laps=1, num_runs=3, token=_tok(),
        )
        r = self.client.get('/contest/judge/')
        self.assertNotContains(r, 'Closed Cat')


class JudgeMobilePanelTest(TestCase):
    """Panel and state fragment render correctly."""

    def setUp(self):
        _, self.timed, self.judged, self.team = _setup()
        self.client.force_login(_judge())

    def test_panel_returns_200(self):
        r = self.client.get(f'/contest/judge/{self.timed.id}/')
        self.assertEqual(r.status_code, 200)

    def test_panel_state_fragment_returns_200(self):
        r = self.client.get(f'/contest/judge/{self.timed.id}/state/')
        self.assertEqual(r.status_code, 200)

    def test_panel_shows_team_name_when_no_run(self):
        r = self.client.get(f'/contest/judge/{self.timed.id}/state/')
        self.assertContains(r, 'Alpha')

    def test_panel_404_for_unknown_competition(self):
        r = self.client.get('/contest/judge/99999/')
        self.assertEqual(r.status_code, 404)


class JudgeMobileScoreTest(TestCase):
    """Score entry screen logic."""

    def setUp(self):
        _, self.timed, self.judged, self.team = _setup()
        self.client.force_login(_judge())

    def _pending_run(self, comp):
        return Run.objects.create(
            team=self.team, competition=comp,
            start_time=timezone.now(), duration=0, state=RunState.PENDING,
        )

    def test_score_screen_on_timed_redirects_to_panel(self):
        run = self._pending_run(self.timed)
        r = self.client.get(f'/contest/judge/{self.timed.id}/run/{run.id}/score/')
        self.assertEqual(r.status_code, 302)
        self.assertIn(f'/contest/judge/{self.timed.id}/', r['Location'])

    def test_score_screen_on_judged_returns_200(self):
        run = self._pending_run(self.judged)
        r = self.client.get(f'/contest/judge/{self.judged.id}/run/{run.id}/score/')
        self.assertEqual(r.status_code, 200)

    def test_post_valid_score_completes_run(self):
        run = self._pending_run(self.judged)
        r = self.client.post(
            f'/contest/judge/{self.judged.id}/run/{run.id}/score/',
            {'score': '75'},
        )
        self.assertEqual(r.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.score, 75)

    def test_post_invalid_score_rerenders_form(self):
        run = self._pending_run(self.judged)
        r = self.client.post(
            f'/contest/judge/{self.judged.id}/run/{run.id}/score/',
            {'score': '150'},
        )
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.PENDING)
