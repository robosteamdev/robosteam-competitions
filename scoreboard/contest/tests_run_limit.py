"""Phase E6 — run-limit accounting and the judge "discard queued run" action (H6).

Decision 2026-07-07: a VOIDED run does not consume one of a team's `num_runs`
attempts; once a manual measurement is recorded it becomes MANUAL and does.
"""
import secrets

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team,
)
from contest.views import counted_runs


def _tok():
    return secrets.token_hex(8)


def _judge(username='limit-judge'):
    user = User.objects.create_user(username=username, password='test')
    group, _ = Group.objects.get_or_create(name='Judge')
    user.groups.add(group)
    return user


class _RunLimitBase(TestCase):
    num_runs = 2

    def setUp(self):
        self.contest = Contest.objects.create(name='Limit Cup', status=ItemStates.OPEN)
        self.comp = Competition.objects.create(
            name='Limit Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_laps=1, num_runs=self.num_runs, token=_tok(),
        )
        self.team = Team.objects.create(name='Limit Team', contest=self.contest)
        self.judge = _judge()
        self.client.force_login(self.judge)

    def _run(self, state=RunState.PENDING):
        return Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now(), duration=0, state=state,
        )

    def _create_run(self):
        return self.client.post(
            f'/contest/competition/{self.comp.id}/run/create', {'team_id': self.team.id},
        )

    def _available_team_ids(self):
        r = self.client.get(f'/contest/competition_board/{self.comp.id}')
        return [t.id for t in r.context['available_teams']]


class CountedRunsTest(_RunLimitBase):
    """counted_runs() ignores VOIDED runs only."""

    def test_pending_active_completed_count(self):
        for state in (RunState.PENDING, RunState.ACTIVE, RunState.COMPLETED):
            Run.objects.all().delete()
            self._run(state=state)
            self.assertEqual(counted_runs(self.team, self.comp), 1, state)

    def test_voided_run_does_not_count(self):
        self._run(state=RunState.VOIDED)
        self.assertEqual(counted_runs(self.team, self.comp), 0)

    def test_manual_run_counts(self):
        self._run(state=RunState.MANUAL)
        self.assertEqual(counted_runs(self.team, self.comp), 1)

    def test_other_categories_not_counted(self):
        other = Competition.objects.create(
            name='Other Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_runs=2, token=_tok(),
        )
        Run.objects.create(
            team=self.team, competition=other,
            start_time=timezone.now(), duration=0, state=RunState.COMPLETED,
        )
        self.assertEqual(counted_runs(self.team, self.comp), 0)


class RunLimitEnforcementTest(_RunLimitBase):
    """run_create and the board honour the same counting rule."""

    def test_limit_reached_blocks_creation(self):
        self._run(state=RunState.COMPLETED)
        self._run(state=RunState.COMPLETED)
        r = self._create_run()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Run.objects.filter(team=self.team, competition=self.comp).count(), 2)

    def test_voided_run_frees_the_slot(self):
        self._run(state=RunState.COMPLETED)
        self._run(state=RunState.VOIDED)
        r = self._create_run()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(counted_runs(self.team, self.comp), 2)

    def test_manual_run_consumes_the_slot(self):
        self._run(state=RunState.COMPLETED)
        self._run(state=RunState.MANUAL)
        self.assertEqual(self._create_run().status_code, 400)

    def test_board_hides_team_at_limit(self):
        self._run(state=RunState.COMPLETED)
        self.assertIn(self.team.id, self._available_team_ids())
        self._run(state=RunState.COMPLETED)
        self.assertNotIn(self.team.id, self._available_team_ids())

    def test_board_shows_team_again_after_void(self):
        self._run(state=RunState.COMPLETED)
        voided = self._run(state=RunState.COMPLETED)
        self.assertNotIn(self.team.id, self._available_team_ids())

        voided.state = RunState.VOIDED
        voided.save(update_fields=['state'])
        self.assertIn(self.team.id, self._available_team_ids())

    def test_mobile_panel_uses_same_rule(self):
        self._run(state=RunState.COMPLETED)
        self._run(state=RunState.VOIDED)
        r = self.client.get(f'/contest/judge/{self.comp.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.team.id, [t.id for t in r.context['available_teams']])


class RunDiscardTest(_RunLimitBase):
    """run_discard removes a queued run that was never started."""

    def _discard(self, run_id):
        return self.client.post(f'/contest/run/{run_id}/discard')

    def test_pending_run_discarded(self):
        run = self._run(state=RunState.PENDING)
        r = self._discard(run.id)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Run.objects.filter(pk=run.id).exists())

    def test_discard_frees_the_slot(self):
        self._run(state=RunState.COMPLETED)
        pending = self._run(state=RunState.PENDING)
        self.assertEqual(counted_runs(self.team, self.comp), 2)
        self._discard(pending.id)
        self.assertEqual(counted_runs(self.team, self.comp), 1)

    def test_active_run_not_discardable(self):
        run = self._run(state=RunState.ACTIVE)
        self.assertEqual(self._discard(run.id).status_code, 400)
        self.assertTrue(Run.objects.filter(pk=run.id).exists())

    def test_completed_run_not_discardable(self):
        run = self._run(state=RunState.COMPLETED)
        self.assertEqual(self._discard(run.id).status_code, 400)

    def test_voided_run_not_discardable(self):
        run = self._run(state=RunState.VOIDED)
        self.assertEqual(self._discard(run.id).status_code, 400)

    def test_get_not_allowed(self):
        run = self._run(state=RunState.PENDING)
        r = self.client.get(f'/contest/run/{run.id}/discard')
        self.assertEqual(r.status_code, 405)
        self.assertTrue(Run.objects.filter(pk=run.id).exists())

    def test_unknown_run_404(self):
        self.assertEqual(self._discard(999999).status_code, 404)

    def test_anonymous_redirected_to_login(self):
        run = self._run(state=RunState.PENDING)
        self.client.logout()
        r = self._discard(run.id)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])
        self.assertTrue(Run.objects.filter(pk=run.id).exists())

    def test_participant_forbidden(self):
        run = self._run(state=RunState.PENDING)
        user = User.objects.create_user(username='parti', password='test')
        group, _ = Group.objects.get_or_create(name='Participant')
        user.groups.add(group)
        self.client.force_login(user)
        self.assertEqual(self._discard(run.id).status_code, 403)
        self.assertTrue(Run.objects.filter(pk=run.id).exists())
