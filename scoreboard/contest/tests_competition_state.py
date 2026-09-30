"""Phase E8 — Competition.state machine (fixes H11).

The WAITING/READY/RUNNING/FINISHED badge on the board was a field nothing ever
wrote. These tests drive the real judge endpoints and assert the transitions.
"""
import secrets

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone

from contest.models import (
    Competition, CompetitionState, CompetitionType, Contest, ItemStates,
    Run, RunState, Team,
)
from scoring.engine import record_manual_result, refresh_competition_state


def _tok():
    return secrets.token_hex(8)


class _StateBase(TestCase):
    competition_type = CompetitionType.TIMED
    num_runs = 1

    def setUp(self):
        self.contest = Contest.objects.create(name='State Cup', status=ItemStates.OPEN)
        self.comp = Competition.objects.create(
            name='State Cat', contest=self.contest,
            competition_type=self.competition_type,
            status=ItemStates.OPEN, num_laps=1, num_runs=self.num_runs, token=_tok(),
        )
        self.team = Team.objects.create(name='State Team', contest=self.contest)

        self.judge = User.objects.create_user(username='state-judge', password='test')
        group, _ = Group.objects.get_or_create(name='Judge')
        self.judge.groups.add(group)
        self.client.force_login(self.judge)

    def state(self):
        self.comp.refresh_from_db()
        return self.comp.state

    def _create_run(self):
        self.client.post(
            f'/contest/competition/{self.comp.id}/run/create', {'team_id': self.team.id},
        )
        return Run.objects.filter(team=self.team, competition=self.comp).latest('id')

    def _run(self, state=RunState.PENDING):
        return Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now(), duration=0, state=state,
        )


class CompetitionStateTransitionTest(_StateBase):

    def test_starts_waiting(self):
        self.assertEqual(self.state(), CompetitionState.WAITING)

    def test_queued_run_makes_it_ready(self):
        self._create_run()
        self.assertEqual(self.state(), CompetitionState.READY)

    def test_started_run_makes_it_running(self):
        run = self._create_run()
        self.client.post(f'/contest/run/{run.id}/start')
        self.assertEqual(self.state(), CompetitionState.RUNNING)

    def test_voided_run_returns_to_ready(self):
        run = self._create_run()
        self.client.post(f'/contest/run/{run.id}/start')
        self.client.post(f'/contest/run/{run.id}/stop')
        # A VOIDED run frees the attempt again (E6), so the category is READY.
        self.assertEqual(self.state(), CompetitionState.READY)

    def test_discarding_the_only_run_returns_to_waiting(self):
        run = self._create_run()
        self.assertEqual(self.state(), CompetitionState.READY)
        self.client.post(f'/contest/run/{run.id}/discard')
        self.assertEqual(self.state(), CompetitionState.WAITING)

    def test_all_attempts_used_finishes_it(self):
        run = self._run(state=RunState.COMPLETED)
        run.time_ms = 5000
        run.save(update_fields=['time_ms'])
        refresh_competition_state(self.comp)
        self.assertEqual(self.state(), CompetitionState.FINISHED)

    def test_attempts_left_keeps_it_ready(self):
        self.comp.num_runs = 2
        self.comp.save(update_fields=['num_runs'])
        self._run(state=RunState.COMPLETED)
        refresh_competition_state(self.comp)
        self.assertEqual(self.state(), CompetitionState.READY)

    def test_closed_category_is_finished(self):
        self.comp.status = ItemStates.CLOSED
        self.comp.save(update_fields=['status'])
        refresh_competition_state(self.comp)
        self.assertEqual(self.state(), CompetitionState.FINISHED)

    def test_manual_entry_consumes_the_last_attempt(self):
        run = self._run(state=RunState.VOIDED)
        refresh_competition_state(self.comp)
        self.assertEqual(self.state(), CompetitionState.READY)

        record_manual_result(run, time_ms=8000)
        self.assertEqual(self.state(), CompetitionState.FINISHED)

    def test_second_team_keeps_it_ready(self):
        Team.objects.create(name='Second Team', contest=self.contest)
        run = self._run(state=RunState.COMPLETED)
        run.time_ms = 5000
        run.save(update_fields=['time_ms'])
        refresh_competition_state(self.comp)
        self.assertEqual(self.state(), CompetitionState.READY)


class JudgedCompetitionStateTest(_StateBase):
    competition_type = CompetitionType.JUDGED

    def test_scoring_the_last_attempt_finishes_it(self):
        run = self._create_run()
        self.assertEqual(self.state(), CompetitionState.READY)
        self.client.post(f'/contest/run/{run.id}/score', {'score': 80, 'comment': ''})
        self.assertEqual(self.state(), CompetitionState.FINISHED)


class OrganiserClosesCategoryTest(_StateBase):
    """Closing a category from the organiser UI marks it FINISHED."""

    def setUp(self):
        super().setUp()
        organiser = User.objects.create_user(username='state-org', password='test')
        group, _ = Group.objects.get_or_create(name='Organiser')
        organiser.groups.add(group)
        self.client.force_login(organiser)

    def test_close_from_organiser_form(self):
        self._run(state=RunState.PENDING)
        refresh_competition_state(self.comp)
        self.assertEqual(self.state(), CompetitionState.READY)

        r = self.client.post(f'/contest/organise/category/{self.comp.id}/edit/', {
            'name': self.comp.name,
            'description': '',
            'competition_type': self.comp.competition_type,
            'status': ItemStates.CLOSED,
            'num_runs': self.comp.num_runs,
            'num_laps': self.comp.num_laps,
            'timeout_seconds': self.comp.timeout_seconds,
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.state(), CompetitionState.FINISHED)
