"""Phase G3 — the small-issue sweep.

Two things are covered here:

* `validate_points_table` — the Overall Ranking points map is free-form JSON, and
  `scoring.engine._lookup_points` reads it with string keys. A table typed with
  integer keys used to save cleanly and then silently award everyone the fallback
  1 point.
* State-transition races — the last lap event (mqtt_bridge process) and the
  timeout deadline (timeout_runs process) can land at the same moment on the same
  run. Both writers now re-check the state under a row lock.
"""
import secrets
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Run, RunState, Team,
    validate_points_table,
)
from devices.models import LapEvent, LapTimerDevice, RegistrationStatus
from scoring.engine import (
    check_and_void_timed_out_runs, try_finalize_run, void_active_runs_on_disconnect,
)


class ValidatePointsTableTest(TestCase):
    def test_accepts_the_documented_default(self):
        validate_points_table({'1': 10, '2': 8, '3': 6, '4': 4, '5': 2, 'default': 1})

    def test_accepts_empty(self):
        validate_points_table({})

    def test_rejects_integer_keys(self):
        """The failure this validator exists for: {1: 10} never matches str(position)."""
        with self.assertRaises(ValidationError):
            validate_points_table({1: 10, 2: 8})

    def test_rejects_non_numeric_key(self):
        with self.assertRaises(ValidationError):
            validate_points_table({'first': 10})

    def test_rejects_zero_position(self):
        with self.assertRaises(ValidationError):
            validate_points_table({'0': 10})

    def test_rejects_non_integer_points(self):
        with self.assertRaises(ValidationError):
            validate_points_table({'1': '10'})

    def test_rejects_negative_points(self):
        with self.assertRaises(ValidationError):
            validate_points_table({'1': -5})

    def test_rejects_a_list(self):
        with self.assertRaises(ValidationError):
            validate_points_table([10, 8, 6])

    def test_full_clean_surfaces_it_on_the_model(self):
        contest = Contest(name='Bad Table', points_table={1: 10})
        with self.assertRaises(ValidationError) as ctx:
            contest.full_clean()
        self.assertIn('points_table', ctx.exception.error_dict)


class RunTransitionRaceTest(TestCase):
    """Whoever writes first wins; the loser must leave the run alone."""

    def setUp(self):
        self.device = LapTimerDevice.objects.create(
            device_id='CC:CC:CC:CC:CC:01', friendly_name='Race Timer',
            registration_status=RegistrationStatus.ACTIVE,
        )
        self.contest = Contest.objects.create(name='Race Cup', status=ItemStates.OPEN)
        self.comp = Competition.objects.create(
            name='Race Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED, status=ItemStates.OPEN,
            num_laps=1, num_runs=5, timeout_seconds=30,
            lap_timer=self.device, token=secrets.token_hex(8),
        )
        self.team = Team.objects.create(name='Race Team', contest=self.contest)

    def _run(self, state=RunState.ACTIVE, started_seconds_ago=0):
        return Run.objects.create(
            team=self.team, competition=self.comp, duration=0, state=state,
            start_time=timezone.now() - timedelta(seconds=started_seconds_ago),
        )

    def _crossings(self, run, count=2):
        """Seed crossings without firing the post_save signal.

        `bulk_create` skips signals on purpose here: these tests drive
        `try_finalize_run` by hand to control exactly when it runs against a
        given state, which is the whole point of the race they cover.
        """
        base = timezone.now()
        LapEvent.objects.bulk_create([
            LapEvent(
                device=self.device, run=run, competition=self.comp,
                timestamp_utc=base + timedelta(seconds=i * 5),
                sequence=900 + run.pk * 10 + i,
            )
            for i in range(count)
        ])

    def test_finalize_refuses_a_run_voided_underneath_it(self):
        run = self._run()
        self._crossings(run)
        # The timeout sweep got there first, in another process.
        Run.objects.filter(pk=run.pk).update(state=RunState.VOIDED)

        self.assertFalse(try_finalize_run(run))
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)
        self.assertIsNone(run.time_ms)

    def test_timeout_sweep_leaves_a_completed_run_alone(self):
        run = self._run(started_seconds_ago=60)
        self._crossings(run)
        self.assertTrue(try_finalize_run(run))

        # Its deadline has passed, but it finished in time.
        self.assertEqual(check_and_void_timed_out_runs(), 0)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)

    def test_timeout_sweep_still_voids_a_genuinely_stuck_run(self):
        run = self._run(started_seconds_ago=60)
        self.assertEqual(check_and_void_timed_out_runs(), 1)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)

    def test_disconnect_void_skips_a_run_completed_first(self):
        run = self._run()
        self._crossings(run)
        try_finalize_run(run)

        self.assertEqual(void_active_runs_on_disconnect(), 0)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)
