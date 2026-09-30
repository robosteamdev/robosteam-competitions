"""Phase D — public read-only scoreboard tests (fixes C3).

Read-only board/cross-table/ranking/team pages are public; every judge control
is gated behind `show_controls`. Anonymous users see results but no run controls;
judges see the controls; POST endpoints remain judge-only (Phase A regression).
"""
import secrets

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team,
)


def _tok():
    return secrets.token_hex(8)


def _group(name):
    g, _ = Group.objects.get_or_create(name=name)
    return g


class _BoardBase(TestCase):
    def setUp(self):
        self.contest = Contest.objects.create(name='Public Contest', status=ItemStates.OPEN)
        self.comp = Competition.objects.create(
            name='Public Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_laps=1, num_runs=3, token=_tok(),
        )
        self.team = Team.objects.create(name='Public Team', contest=self.contest, token=_tok())
        self.run = Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now(), duration=0, state=RunState.PENDING,
        )
        self.board_url = f'/contest/competition_board/{self.comp.id}'
        self.fragment_url = f'/contest/competition/{self.comp.id}/fragment/'


class PublicReadAccessTest(_BoardBase):
    """All read-only pages return 200 for anonymous users."""

    def test_board_public(self):
        self.assertEqual(self.client.get(self.board_url).status_code, 200)

    def test_fragment_public(self):
        self.assertEqual(self.client.get(self.fragment_url).status_code, 200)

    def test_crosstable_public(self):
        self.assertEqual(self.client.get(f'/contest/contest/{self.contest.id}').status_code, 200)

    def test_ranking_public(self):
        self.assertEqual(self.client.get(f'/contest/contest/{self.contest.id}/ranking/').status_code, 200)

    def test_team_public(self):
        self.assertEqual(self.client.get(f'/contest/team/{self.team.id}').status_code, 200)


class ControlsGatingTest(_BoardBase):
    """Controls are hidden from anonymous/participant, shown to judges."""

    def test_anonymous_board_has_no_controls(self):
        body = self.client.get(self.board_url).content.decode()
        self.assertNotIn('Create Run', body)
        self.assertNotIn('startRun(', body)

    def test_anonymous_fragment_has_no_create_form(self):
        body = self.client.get(self.fragment_url).content.decode()
        self.assertNotIn('Create Run', body)
        self.assertNotIn('/run/create', body)

    def test_participant_board_has_no_controls(self):
        u = User.objects.create_user('part1', password='pass', is_active=True)
        u.groups.add(_group('Participant'))
        self.client.force_login(u)
        body = self.client.get(self.board_url).content.decode()
        self.assertNotIn('Create Run', body)
        self.assertNotIn('startRun(', body)

    def test_judge_board_has_controls(self):
        u = User.objects.create_user('judge1', password='pass', is_active=True)
        u.groups.add(_group('Judge'))
        self.client.force_login(u)
        body = self.client.get(self.board_url).content.decode()
        self.assertIn('Create Run', body)
        self.assertIn('startRun(', body)


class PublicPostStillProtectedTest(_BoardBase):
    """Read pages public, but POST endpoints still redirect anonymous to login."""

    def test_anonymous_post_run_create_redirects_login(self):
        r = self.client.post(f'/contest/competition/{self.comp.id}/run/create', {'team_id': self.team.id})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])
