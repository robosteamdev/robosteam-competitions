from datetime import timedelta

from django.contrib.auth.models import Group, User


def _add_to_judge_group(user):
    """Give a test user Judge-group membership so judge_required passes."""
    group, _ = Group.objects.get_or_create(name='Judge')
    user.groups.add(group)
    return user
from django.test import Client, TestCase
from django.utils import timezone

from contest.models import Competition, CompetitionType, Contest, Result, Run, RunState, Team
from devices.models import DeviceType, LapEvent, LapTimerDevice, RegistrationStatus
from scoring.engine import (
    _update_best_judged_result,
    _update_best_timed_result,
    assign_lap_event_to_active_run,
    check_and_void_timed_out_runs,
    crossings_needed,
    score_judged_run,
    try_finalize_run,
)
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def make_device(mac='AA:BB:CC:DD:EE:FF', registration_status=RegistrationStatus.ACTIVE):
    """An approved timer by default — only ACTIVE devices may score runs (H4)."""
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name='Timer', device_type=DeviceType.LAPTIMER,
        registration_status=registration_status,
    )


def make_contest():
    return Contest.objects.create(name='Test Contest')


def make_competition(contest, device=None, num_laps=1, ctype=CompetitionType.TIMED):
    return Competition.objects.create(
        name='Test Category',
        contest=contest,
        competition_type=ctype,
        num_laps=num_laps,
        lap_timer=device,
    )


def make_team(contest, name='Team A'):
    return Team.objects.create(name=name, contest=contest)


def make_run(team, competition, state=RunState.ACTIVE):
    return Run.objects.create(
        team=team,
        competition=competition,
        start_time=timezone.now(),
        duration=0,
        state=state,
    )


def make_lap_event(device, seq, offset_seconds=0, run=None, competition=None):
    ts = timezone.now() + timedelta(seconds=offset_seconds)
    return LapEvent.objects.create(
        device=device, sequence=seq, timestamp_utc=ts, run=run, competition=competition,
    )


# ── TIMED scoring tests ───────────────────────────────────────────────────────

class CrossingsNeededTest(TestCase):
    def test_one_lap_needs_two_crossings(self):
        contest = make_contest()
        comp = Competition.objects.create(name='C', contest=contest, competition_type=CompetitionType.TIMED, num_laps=1)
        run  = make_run(make_team(contest), comp)
        self.assertEqual(crossings_needed(run), 2)

    def test_three_laps_needs_four_crossings(self):
        contest = make_contest()
        comp = Competition.objects.create(name='C', contest=contest, competition_type=CompetitionType.TIMED, num_laps=3)
        run  = make_run(make_team(contest), comp)
        self.assertEqual(crossings_needed(run), 4)


class AssignLapEventTest(TestCase):
    def setUp(self):
        self.device = make_device()
        self.contest = make_contest()
        self.competition = make_competition(self.contest, self.device)
        self.team = make_team(self.contest)
        self.run  = make_run(self.team, self.competition, state=RunState.ACTIVE)

    def test_assigns_event_to_active_run(self):
        event = make_lap_event(self.device, seq=1)
        self.assertEqual(assign_lap_event_to_active_run(event), self.run)
        event.refresh_from_db()
        self.assertEqual(event.run, self.run)
        self.assertEqual(event.competition, self.competition)

    def test_no_active_run_returns_none(self):
        self.run.state = RunState.COMPLETED
        self.run.save()
        self.assertIsNone(assign_lap_event_to_active_run(make_lap_event(self.device, seq=1)))

    def test_unassigned_device_returns_none(self):
        other = make_device(mac='11:22:33:44:55:66')
        self.assertIsNone(assign_lap_event_to_active_run(make_lap_event(other, seq=1)))


class TryFinalizeRunTest(TestCase):
    def setUp(self):
        self.device = make_device()
        self.contest = make_contest()
        self.competition = make_competition(self.contest, self.device, num_laps=1)
        self.team = make_team(self.contest)
        self.run  = make_run(self.team, self.competition, state=RunState.ACTIVE)

    def test_not_finalized_with_one_crossing(self):
        make_lap_event(self.device, seq=1, run=self.run, competition=self.competition)
        self.assertFalse(try_finalize_run(self.run))
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.ACTIVE)

    def test_finalized_with_two_crossings_for_one_lap(self):
        make_lap_event(self.device, seq=1, offset_seconds=0, run=self.run, competition=self.competition)
        make_lap_event(self.device, seq=2, offset_seconds=5, run=self.run, competition=self.competition)
        # The lap-event signal has already finalized it by now; since G3 a second
        # call returns False rather than re-finalizing a run that is no longer ACTIVE.
        self.assertFalse(try_finalize_run(self.run))
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.COMPLETED)
        self.assertAlmostEqual(self.run.time_ms, 5000, delta=50)

    def test_time_uses_first_and_nth_crossing_only(self):
        make_lap_event(self.device, seq=1, offset_seconds=0,  run=self.run, competition=self.competition)
        make_lap_event(self.device, seq=2, offset_seconds=5,  run=self.run, competition=self.competition)
        make_lap_event(self.device, seq=3, offset_seconds=99, run=self.run, competition=self.competition)
        try_finalize_run(self.run)
        self.run.refresh_from_db()
        self.assertAlmostEqual(self.run.time_ms, 5000, delta=50)


class UpdateBestTimedResultTest(TestCase):
    def setUp(self):
        self.device = make_device()
        self.contest = make_contest()
        self.competition = make_competition(self.contest, self.device)
        self.team = make_team(self.contest)

    def _completed_run(self, time_ms, penalty=0):
        run = make_run(self.team, self.competition, state=RunState.COMPLETED)
        run.time_ms = time_ms
        run.penalty_time_ms = penalty
        run.save()
        return run

    def test_fastest_run_marked_best(self):
        slow = self._completed_run(10000)
        fast = self._completed_run(5000)
        _update_best_timed_result(fast)
        fast.refresh_from_db(); slow.refresh_from_db()
        self.assertTrue(fast.is_best)
        self.assertFalse(slow.is_best)

    def test_penalty_included_in_best(self):
        penalised = self._completed_run(5000, penalty=3000)   # total 8000
        clean     = self._completed_run(7000, penalty=0)      # total 7000
        _update_best_timed_result(clean)
        penalised.refresh_from_db(); clean.refresh_from_db()
        self.assertTrue(clean.is_best)
        self.assertFalse(penalised.is_best)

    def test_result_upserted_with_best_total(self):
        run = self._completed_run(6000, penalty=500)
        _update_best_timed_result(run)
        result = Result.objects.get(team=self.team, competition=self.competition)
        self.assertEqual(result.score, 6500)

    def test_result_updated_when_faster_run_arrives(self):
        slow = self._completed_run(10000)
        _update_best_timed_result(slow)
        fast = self._completed_run(5000)
        _update_best_timed_result(fast)
        self.assertEqual(Result.objects.get(team=self.team, competition=self.competition).score, 5000)


# ── JUDGED scoring tests ──────────────────────────────────────────────────────

class ScoreJudgedRunTest(TestCase):
    def setUp(self):
        self.contest     = make_contest()
        self.competition = make_competition(self.contest, ctype=CompetitionType.JUDGED)
        self.team = make_team(self.contest)

    def test_run_marked_completed_with_score(self):
        run = make_run(self.team, self.competition, state=RunState.ACTIVE)
        score_judged_run(run, 85, 'Great performance')
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(run.score, 85)
        self.assertEqual(run.judge_comment, 'Great performance')

    def test_result_created(self):
        run = make_run(self.team, self.competition, state=RunState.ACTIVE)
        score_judged_run(run, 75)
        result = Result.objects.get(team=self.team, competition=self.competition)
        self.assertEqual(result.score, 75)

    def test_score_from_pending_state(self):
        run = make_run(self.team, self.competition, state=RunState.PENDING)
        score_judged_run(run, 90)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)


class UpdateBestJudgedResultTest(TestCase):
    def setUp(self):
        self.contest     = make_contest()
        self.competition = make_competition(self.contest, ctype=CompetitionType.JUDGED)
        self.team = make_team(self.contest)

    def _completed_run(self, score):
        run = make_run(self.team, self.competition, state=RunState.COMPLETED)
        run.score = score
        run.save()
        return run

    def test_highest_score_marked_best(self):
        low  = self._completed_run(60)
        high = self._completed_run(90)
        _update_best_judged_result(high)
        high.refresh_from_db(); low.refresh_from_db()
        self.assertTrue(high.is_best)
        self.assertFalse(low.is_best)

    def test_result_updated_when_higher_score_arrives(self):
        first = self._completed_run(70)
        _update_best_judged_result(first)
        better = self._completed_run(85)
        _update_best_judged_result(better)
        self.assertEqual(Result.objects.get(team=self.team, competition=self.competition).score, 85)

    def test_lower_score_does_not_replace_best(self):
        high = self._completed_run(90)
        _update_best_judged_result(high)
        low = self._completed_run(50)
        _update_best_judged_result(low)
        high.refresh_from_db()
        self.assertTrue(high.is_best)
        self.assertEqual(Result.objects.get(team=self.team, competition=self.competition).score, 90)


# ── BUG-03 regression ─────────────────────────────────────────────────────────

class BUG03LapNumberPopulatedTest(TestCase):
    """try_finalize_run must set lap_number on each LapEvent (0=start, 1=lap1, …)."""

    def setUp(self):
        self.device = make_device()
        self.contest = make_contest()
        self.competition = make_competition(self.contest, device=self.device, num_laps=3)
        self.team = make_team(self.contest)

    def _make_events(self, run, count, base_seq=200):
        base_ts = timezone.now()
        for i in range(count):
            LapEvent.objects.create(
                device=self.device, run=run, competition=self.competition,
                timestamp_utc=base_ts + timedelta(seconds=i * 5),
                sequence=base_seq + i,
            )

    def test_lap_numbers_set_after_finalize(self):
        run = make_run(self.team, self.competition)
        self._make_events(run, count=4)  # 3 laps + start crossing
        try_finalize_run(run)
        events = list(LapEvent.objects.filter(run=run).order_by('timestamp_utc'))
        self.assertEqual(len(events), 4)
        for expected, ev in enumerate(events):
            self.assertEqual(
                ev.lap_number, expected,
                f'seq={ev.sequence} expected lap_number={expected}, got {ev.lap_number}'
            )

    def test_lap_number_zero_is_start_crossing(self):
        run = make_run(self.team, self.competition)
        self._make_events(run, count=4, base_seq=300)
        try_finalize_run(run)
        first = LapEvent.objects.filter(run=run).order_by('timestamp_utc').first()
        self.assertEqual(first.lap_number, 0)

    def test_final_crossing_has_correct_lap_number(self):
        run = make_run(self.team, self.competition)
        self._make_events(run, count=4, base_seq=400)
        try_finalize_run(run)
        last = LapEvent.objects.filter(run=run).order_by('timestamp_utc').last()
        self.assertEqual(last.lap_number, 3)  # 3-lap run

    def test_incomplete_run_leaves_lap_number_null(self):
        run = make_run(self.team, self.competition)
        self._make_events(run, count=2, base_seq=500)  # only 2 of 4 crossings
        try_finalize_run(run)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)  # not completed
        events = LapEvent.objects.filter(run=run)
        for ev in events:
            self.assertIsNone(ev.lap_number)  # not set until finalized


# ── Phase 5.5b: Integration tests (signal chain) ──────────────────────────────

class TimedScoringSignalIntegrationTest(TestCase):
    """
    Full TIMED scoring signal chain:
    LapEvent.objects.create() -> post_save signal -> assign_lap_event_to_active_run
    -> try_finalize_run -> Run.COMPLETED + Result upserted.
    """

    def setUp(self):
        self.device = make_device()
        self.contest = make_contest()
        self.competition = make_competition(self.contest, device=self.device, num_laps=2)
        self.team = make_team(self.contest)
        self._base_ts = timezone.now()
        self.run = make_run(self.team, self.competition)  # ACTIVE

    def _save_event(self, seq, offset_secs):
        return LapEvent.objects.create(
            device=self.device,
            timestamp_utc=self._base_ts + timedelta(seconds=offset_secs),
            sequence=seq,
        )

    def test_partial_crossings_do_not_finalize(self):
        self._save_event(seq=1, offset_secs=0)
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.ACTIVE)

    def test_all_crossings_finalize_run(self):
        self._save_event(seq=10, offset_secs=0)   # start crossing
        self._save_event(seq=11, offset_secs=5)   # lap 1
        self._save_event(seq=12, offset_secs=12)  # lap 2 (finish)
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.COMPLETED)
        self.assertEqual(self.run.time_ms, 12000)

    def test_result_upserted_after_finalize(self):
        self._save_event(seq=20, offset_secs=0)
        self._save_event(seq=21, offset_secs=8)
        self._save_event(seq=22, offset_secs=15)
        result = Result.objects.get(team=self.team, competition=self.competition)
        self.assertEqual(result.score, 15000)

    def test_is_best_set_on_only_completed_run(self):
        self._save_event(seq=30, offset_secs=0)
        self._save_event(seq=31, offset_secs=6)
        self._save_event(seq=32, offset_secs=10)
        self.run.refresh_from_db()
        self.assertTrue(self.run.is_best)

    def test_lap_numbers_set_by_signal_chain(self):
        self._save_event(seq=40, offset_secs=0)
        self._save_event(seq=41, offset_secs=7)
        self._save_event(seq=42, offset_secs=13)
        events = list(
            LapEvent.objects.filter(run=self.run).order_by('timestamp_utc')
        )
        self.assertEqual([ev.lap_number for ev in events], [0, 1, 2])


class JudgedScoringSignalIntegrationTest(TestCase):
    """
    Full JUDGED scoring integration: POST to run_score view -> score_judged_run
    -> _update_best_judged_result -> Run.COMPLETED + is_best + Result.
    """

    def setUp(self):
        import secrets
        self.client = Client()
        self.user, _ = User.objects.get_or_create(username='testjudge_judged')
        _add_to_judge_group(self.user)
        self.client.force_login(self.user)
        self.contest = make_contest()
        self.competition = Competition.objects.create(
            name='Judged Cat', contest=self.contest,
            competition_type=CompetitionType.JUDGED,
            num_laps=1, num_runs=3, token=secrets.token_hex(8),
        )
        self.team = make_team(self.contest)

    def _pending_run(self):
        return make_run(self.team, self.competition, state=RunState.PENDING)

    def test_first_score_sets_is_best(self):
        run = self._pending_run()
        self.client.post(f'/contest/run/{run.id}/score', {'score': 75})
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertTrue(run.is_best)

    def test_higher_score_takes_is_best(self):
        r1 = self._pending_run()
        self.client.post(f'/contest/run/{r1.id}/score', {'score': 70})
        r2 = self._pending_run()
        self.client.post(f'/contest/run/{r2.id}/score', {'score': 85})
        r1.refresh_from_db(); r2.refresh_from_db()
        self.assertFalse(r1.is_best)
        self.assertTrue(r2.is_best)

    def test_lower_score_does_not_take_is_best(self):
        r1 = self._pending_run()
        self.client.post(f'/contest/run/{r1.id}/score', {'score': 90})
        r2 = self._pending_run()
        self.client.post(f'/contest/run/{r2.id}/score', {'score': 60})
        r1.refresh_from_db(); r2.refresh_from_db()
        self.assertTrue(r1.is_best)
        self.assertFalse(r2.is_best)

    def test_result_reflects_best_score(self):
        for score in [65, 88, 72]:
            run = self._pending_run()
            self.client.post(f'/contest/run/{run.id}/score', {'score': score})
        result = Result.objects.get(team=self.team, competition=self.competition)
        self.assertEqual(result.score, 88)


class OrphanLapEventSignalTest(TestCase):
    """
    Signal must not crash when no ACTIVE run is found for the device.
    LapEvent stored with run=None.
    """

    def setUp(self):
        self.device = make_device()

    def test_orphan_stored_with_run_none(self):
        ev = LapEvent.objects.create(
            device=self.device,
            timestamp_utc=timezone.now(),
            sequence=999,
        )
        ev.refresh_from_db()
        self.assertIsNone(ev.run)
        self.assertIsNone(ev.competition)

    def test_no_result_created_for_orphan(self):
        LapEvent.objects.create(
            device=self.device,
            timestamp_utc=timezone.now(),
            sequence=998,
        )
        self.assertEqual(Result.objects.count(), 0)


class DeduplicationSignalTest(TestCase):
    """
    Second save with same (device, sequence) must be silently discarded.
    Signal must not fire, LapEvent count must not change.
    """

    def setUp(self):
        self.device = make_device()
        self.contest = make_contest()
        self.competition = make_competition(self.contest, device=self.device, num_laps=1)
        self.team = make_team(self.contest)
        self.run = make_run(self.team, self.competition)

    def test_duplicate_seq_not_saved(self):
        LapEvent.objects.create(
            device=self.device, run=self.run, competition=self.competition,
            timestamp_utc=timezone.now(), sequence=50,
        )
        count_before = LapEvent.objects.count()
        # get_or_create with same seq — simulates mqtt_bridge deduplication
        _, created = LapEvent.objects.get_or_create(
            device=self.device, sequence=50,
            defaults={'timestamp_utc': timezone.now()},
        )
        self.assertFalse(created)
        self.assertEqual(LapEvent.objects.count(), count_before)

    def test_run_not_double_finalized(self):
        """A run that finalizes on crossing N must not change if crossing N is re-delivered."""
        # Finalize with 2 crossings (1-lap run)
        LapEvent.objects.create(
            device=self.device, run=self.run, competition=self.competition,
            timestamp_utc=timezone.now(), sequence=60,
        )
        LapEvent.objects.create(
            device=self.device, run=self.run, competition=self.competition,
            timestamp_utc=timezone.now() + timedelta(seconds=10), sequence=61,
        )
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.COMPLETED)
        time_ms_first = self.run.time_ms

        # Re-deliver crossing 61 (duplicate) — must not alter the run
        LapEvent.objects.get_or_create(
            device=self.device, sequence=61,
            defaults={'timestamp_utc': timezone.now()},
        )
        self.run.refresh_from_db()
        self.assertEqual(self.run.time_ms, time_ms_first)



# ── Phase 5.6: Overall Ranking tests ──────────────────────────────────────────

import secrets as _secrets

from contest.models import ItemStates
from scoring.engine import _update_overall_ranking
from scoring.models import OverallResult


def _t(contest, name):
    """Create a team with token=None (field is null=True) to avoid unique collisions."""
    return Team.objects.create(name=name, contest=contest, token=None)


def _c(contest, name, ctype, **kwargs):
    """Create a Competition with a unique token derived from name."""
    return Competition.objects.create(
        name=name, contest=contest, competition_type=ctype,
        token=f'{name}-{_secrets.token_hex(4)}', **kwargs,
    )


class OverallRankingBasicTest(TestCase):
    """Correct points + ranks for 3 teams across 2 categories."""

    def setUp(self):
        self.contest = Contest.objects.create(
            name='Ranking Cup',
            points_table={'1': 10, '2': 8, '3': 6, '4': 4, '5': 2, 'default': 1},
        )
        self.team_a = _t(self.contest, 'Alpha')
        self.team_b = _t(self.contest, 'Bravo')
        self.team_c = _t(self.contest, 'Charlie')
        self.cat_timed  = _c(self.contest, 'Line Following', CompetitionType.TIMED,  num_laps=1)
        self.cat_judged = _c(self.contest, 'Arm Task',       CompetitionType.JUDGED, num_laps=1)

    def _result(self, team, cat, score):
        Result.objects.update_or_create(team=team, competition=cat, defaults={'score': score})

    def test_correct_points_calculated(self):
        # TIMED (low wins): Alpha=15000(1st=10), Bravo=21000(2nd=8), Charlie=27000(3rd=6)
        # JUDGED (high wins): Bravo=90(1st=10), Alpha=80(2nd=8), Charlie=70(3rd=6)
        # Totals: Alpha=18, Bravo=18, Charlie=12
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_b, self.cat_timed, 21000)
        self._result(self.team_c, self.cat_timed, 27000)
        self._result(self.team_b, self.cat_judged, 90)
        self._result(self.team_a, self.cat_judged, 80)
        self._result(self.team_c, self.cat_judged, 70)

        _update_overall_ranking(self.cat_timed)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        rc = OverallResult.objects.get(contest=self.contest, team=self.team_c)

        self.assertEqual(ra.total_points, 18)
        self.assertEqual(rb.total_points, 18)
        self.assertEqual(rc.total_points, 12)

    def test_charlie_ranked_third(self):
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_b, self.cat_timed, 21000)
        self._result(self.team_c, self.cat_timed, 27000)
        self._result(self.team_b, self.cat_judged, 90)
        self._result(self.team_a, self.cat_judged, 80)
        self._result(self.team_c, self.cat_judged, 70)

        _update_overall_ranking(self.cat_timed)

        rc = OverallResult.objects.get(contest=self.contest, team=self.team_c)
        self.assertEqual(rc.rank, 3)
        self.assertTrue(rc.is_eligible)

    def test_tied_teams_get_rank_1_and_2(self):
        # Alpha=18, Bravo=18 — share ranks 1 and 2 in some order
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_b, self.cat_timed, 21000)
        self._result(self.team_c, self.cat_timed, 27000)
        self._result(self.team_b, self.cat_judged, 90)
        self._result(self.team_a, self.cat_judged, 80)
        self._result(self.team_c, self.cat_judged, 70)

        _update_overall_ranking(self.cat_timed)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        self.assertIn(ra.rank, (1, 2))
        self.assertIn(rb.rank, (1, 2))
        self.assertNotEqual(ra.rank, rb.rank)

    def test_all_teams_created_as_overall_results(self):
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_b, self.cat_timed, 21000)
        self._result(self.team_c, self.cat_timed, 27000)
        self._result(self.team_a, self.cat_judged, 80)
        self._result(self.team_b, self.cat_judged, 70)
        self._result(self.team_c, self.cat_judged, 60)

        _update_overall_ranking(self.cat_timed)
        self.assertEqual(OverallResult.objects.filter(contest=self.contest).count(), 3)


class OverallRankingEligibilityTest(TestCase):
    """Team missing a result in any category is ineligible and unranked."""

    def setUp(self):
        self.contest = Contest.objects.create(
            name='Elig Cup', points_table={'1': 10, '2': 8, 'default': 1},
        )
        self.team_a = _t(self.contest, 'Alpha')
        self.team_b = _t(self.contest, 'Bravo')
        self.team_c = _t(self.contest, 'Charlie')
        self.cat_timed  = _c(self.contest, 'LF',  CompetitionType.TIMED,  num_laps=1)
        self.cat_judged = _c(self.contest, 'Arm', CompetitionType.JUDGED, num_laps=1)

    def _result(self, team, cat, score):
        Result.objects.update_or_create(team=team, competition=cat, defaults={'score': score})

    def test_team_missing_one_category_is_ineligible(self):
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_b, self.cat_timed, 21000)
        # Charlie only in judged — missing timed result
        self._result(self.team_c, self.cat_judged, 90)
        self._result(self.team_a, self.cat_judged, 80)
        self._result(self.team_b, self.cat_judged, 70)

        _update_overall_ranking(self.cat_timed)

        rc = OverallResult.objects.get(contest=self.contest, team=self.team_c)
        self.assertFalse(rc.is_eligible)
        self.assertIsNone(rc.rank)

    def test_ineligible_team_has_zero_points(self):
        # Only Alpha has results; Bravo and Charlie have none
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_a, self.cat_judged, 80)

        _update_overall_ranking(self.cat_timed)

        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        rc = OverallResult.objects.get(contest=self.contest, team=self.team_c)
        self.assertEqual(rb.total_points, 0)
        self.assertEqual(rc.total_points, 0)

    def test_eligible_teams_ranked_ineligible_excluded(self):
        self._result(self.team_a, self.cat_timed, 15000)
        self._result(self.team_b, self.cat_timed, 21000)
        self._result(self.team_a, self.cat_judged, 80)
        self._result(self.team_b, self.cat_judged, 70)
        # Charlie: no results at all

        _update_overall_ranking(self.cat_timed)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        rc = OverallResult.objects.get(contest=self.contest, team=self.team_c)

        self.assertEqual(ra.rank, 1)
        self.assertEqual(rb.rank, 2)
        self.assertIsNone(rc.rank)


class OverallRankingCategoryScopeTest(TestCase):
    """C7: DELETED categories must not gate eligibility; NEW drafts still do (decided)."""

    def setUp(self):
        self.contest = Contest.objects.create(
            name='Scope Cup', points_table={'1': 10, '2': 8, 'default': 1},
        )
        self.team_a = _t(self.contest, 'Alpha')
        self.team_b = _t(self.contest, 'Bravo')
        self.cat_one = _c(self.contest, 'LF',  CompetitionType.TIMED,
                          num_laps=1, status=ItemStates.OPEN)
        self.cat_two = _c(self.contest, 'Arm', CompetitionType.JUDGED,
                          num_laps=1, status=ItemStates.OPEN)

    def _result(self, team, cat, score):
        Result.objects.update_or_create(team=team, competition=cat, defaults={'score': score})

    def _results_in_open_categories(self):
        self._result(self.team_a, self.cat_one, 15000)
        self._result(self.team_b, self.cat_one, 21000)
        self._result(self.team_a, self.cat_two, 90)
        self._result(self.team_b, self.cat_two, 80)

    def test_deleted_category_does_not_gate_eligibility(self):
        _c(self.contest, 'Scrapped', CompetitionType.JUDGED,
           num_laps=1, status=ItemStates.DELETED)
        self._results_in_open_categories()

        _update_overall_ranking(self.cat_one)

        for team in (self.team_a, self.team_b):
            row = OverallResult.objects.get(contest=self.contest, team=team)
            self.assertTrue(row.is_eligible, f'{team.name} should be eligible')
            self.assertIsNotNone(row.rank)

    def test_deleted_category_results_do_not_award_points(self):
        deleted = _c(self.contest, 'Scrapped', CompetitionType.JUDGED,
                     num_laps=1, status=ItemStates.DELETED)
        self._results_in_open_categories()
        self._result(self.team_b, deleted, 100)   # would be a 1st place if counted

        _update_overall_ranking(self.cat_one)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        # Alpha 1st in both OPEN categories = 20; Bravo 2nd in both = 16.
        self.assertEqual(ra.total_points, 20)
        self.assertEqual(rb.total_points, 16)

    def test_missing_open_category_still_ineligible(self):
        self._result(self.team_a, self.cat_one, 15000)
        self._result(self.team_b, self.cat_one, 21000)
        self._result(self.team_a, self.cat_two, 90)   # Bravo missing cat_two

        _update_overall_ranking(self.cat_one)

        self.assertTrue(OverallResult.objects.get(contest=self.contest, team=self.team_a).is_eligible)
        self.assertFalse(OverallResult.objects.get(contest=self.contest, team=self.team_b).is_eligible)

    def test_new_draft_category_still_gates_eligibility(self):
        """Decided 2026-07-07: NEW drafts count. Documents current behaviour."""
        _c(self.contest, 'Draft', CompetitionType.JUDGED,
           num_laps=1, status=ItemStates.NEW)
        self._results_in_open_categories()

        _update_overall_ranking(self.cat_one)

        for team in (self.team_a, self.team_b):
            row = OverallResult.objects.get(contest=self.contest, team=team)
            self.assertFalse(row.is_eligible, 'NEW draft category still gates eligibility')

    def test_other_contest_categories_are_ignored(self):
        other = Contest.objects.create(name='Other Cup')
        _c(other, 'Foreign', CompetitionType.JUDGED, num_laps=1,
           status=ItemStates.OPEN)
        self._results_in_open_categories()

        _update_overall_ranking(self.cat_one)

        self.assertTrue(OverallResult.objects.get(contest=self.contest, team=self.team_a).is_eligible)


class OverallRankingTieBreakTest(TestCase):
    """Tie-break: most 1st-place category finishes, then 2nd, etc."""

    def setUp(self):
        self.contest = Contest.objects.create(
            name='Tie Cup', points_table={'1': 10, '2': 8, '3': 6, 'default': 1},
        )
        self.team_a = _t(self.contest, 'Alpha')
        self.team_b = _t(self.contest, 'Bravo')
        self.cat1 = _c(self.contest, 'Cat1', CompetitionType.JUDGED, num_laps=1)
        self.cat2 = _c(self.contest, 'Cat2', CompetitionType.JUDGED, num_laps=1)

    def _result(self, team, cat, score):
        Result.objects.update_or_create(team=team, competition=cat, defaults={'score': score})

    def test_clear_winner_ranked_first(self):
        # Alpha 1st+1st=20pts, Bravo 2nd+2nd=16pts
        self._result(self.team_a, self.cat1, 90)
        self._result(self.team_b, self.cat1, 70)
        self._result(self.team_a, self.cat2, 85)
        self._result(self.team_b, self.cat2, 60)

        _update_overall_ranking(self.cat1)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        self.assertEqual(ra.rank, 1)
        self.assertEqual(rb.rank, 2)

    def test_equal_points_still_assigns_distinct_ranks(self):
        # Alpha: 1st in cat1(10) + 2nd in cat2(8) = 18pts
        # Bravo: 2nd in cat1(8) + 1st in cat2(10) = 18pts — true tie, ranks must be distinct
        self._result(self.team_a, self.cat1, 90)
        self._result(self.team_b, self.cat1, 80)
        self._result(self.team_b, self.cat2, 90)
        self._result(self.team_a, self.cat2, 80)

        _update_overall_ranking(self.cat1)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        self.assertEqual(ra.total_points, 18)
        self.assertEqual(rb.total_points, 18)
        self.assertIn(ra.rank, (1, 2))
        self.assertIn(rb.rank, (1, 2))
        self.assertNotEqual(ra.rank, rb.rank)


class OverallRankingPointsTableTest(TestCase):
    """Custom points_table is applied; fallback to 1 for unlisted positions."""

    def setUp(self):
        self.contest = Contest.objects.create(
            name='Points Cup',
            points_table={'1': 5},  # only 1st gets 5; all others fall back to 1
        )
        self.team_a = _t(self.contest, 'Alpha')
        self.team_b = _t(self.contest, 'Bravo')
        self.team_c = _t(self.contest, 'Charlie')
        self.cat_timed  = _c(self.contest, 'LF',  CompetitionType.TIMED,  num_laps=1)
        self.cat_judged = _c(self.contest, 'Arm', CompetitionType.JUDGED, num_laps=1)

    def _result(self, team, cat, score):
        Result.objects.update_or_create(team=team, competition=cat, defaults={'score': score})

    def test_first_place_gets_configured_points(self):
        # Alpha 1st in both → 5+5=10; Bravo 2nd → 1+1=2; Charlie 3rd → 1+1=2
        self._result(self.team_a, self.cat_timed, 1000)
        self._result(self.team_b, self.cat_timed, 2000)
        self._result(self.team_c, self.cat_timed, 3000)
        self._result(self.team_a, self.cat_judged, 90)
        self._result(self.team_b, self.cat_judged, 80)
        self._result(self.team_c, self.cat_judged, 70)

        _update_overall_ranking(self.cat_timed)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        rc = OverallResult.objects.get(contest=self.contest, team=self.team_c)

        self.assertEqual(ra.total_points, 10)
        self.assertEqual(rb.total_points, 2)
        self.assertEqual(rc.total_points, 2)
        self.assertEqual(ra.rank, 1)

    def test_empty_points_table_falls_back_to_one_per_position(self):
        self.contest.points_table = {}
        self.contest.save()

        self._result(self.team_a, self.cat_timed, 1000)
        self._result(self.team_b, self.cat_timed, 2000)
        self._result(self.team_a, self.cat_judged, 90)
        self._result(self.team_b, self.cat_judged, 80)

        _update_overall_ranking(self.cat_timed)

        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        rb = OverallResult.objects.get(contest=self.contest, team=self.team_b)
        # Each team: 1pt per category × 2 = 2pts each
        self.assertEqual(ra.total_points, 2)
        self.assertEqual(rb.total_points, 2)


class OverallRankingAutoUpdateTest(TestCase):
    """_update_overall_ranking is called automatically via _update_best_timed/judged_result."""

    def setUp(self):
        self.device = make_device()
        self.contest = Contest.objects.create(
            name='Auto Cup', points_table={'1': 10, '2': 8, 'default': 1},
        )
        self.team_a = _t(self.contest, 'Alpha')
        self.team_b = _t(self.contest, 'Bravo')
        self.cat_timed  = _c(self.contest, 'LF',  CompetitionType.TIMED,  num_laps=1, lap_timer=self.device)
        self.cat_judged = _c(self.contest, 'Arm', CompetitionType.JUDGED, num_laps=1)

    def _timed_result(self, team, time_ms):
        run = make_run(team, self.cat_timed, state=RunState.COMPLETED)
        run.time_ms = time_ms
        run.save()
        _update_best_timed_result(run)

    def _judged_result(self, team, score):
        run = make_run(team, self.cat_judged, state=RunState.COMPLETED)
        run.score = score
        run.save()
        _update_best_judged_result(run)

    def test_overall_result_created_after_timed_score(self):
        self._timed_result(self.team_a, 10000)
        self.assertTrue(OverallResult.objects.filter(contest=self.contest).exists())

    def test_overall_result_created_after_judged_score(self):
        self._judged_result(self.team_a, 80)
        self.assertTrue(OverallResult.objects.filter(contest=self.contest).exists())

    def test_ranking_updates_as_results_come_in(self):
        # After timed only: no judged result → ineligible
        self._timed_result(self.team_a, 10000)
        self._timed_result(self.team_b, 20000)
        ra = OverallResult.objects.get(contest=self.contest, team=self.team_a)
        self.assertFalse(ra.is_eligible)

        # After both categories scored: eligible + ranked 1st
        self._judged_result(self.team_a, 90)
        self._judged_result(self.team_b, 70)
        ra.refresh_from_db()
        self.assertTrue(ra.is_eligible)
        self.assertEqual(ra.rank, 1)

# ── Phase 5.8: Auto-timeout tests ─────────────────────────────────────────────

import secrets as _secrets
from datetime import timedelta

from django.utils import timezone

from scoring.engine import check_and_void_timed_out_runs


class AutoTimeoutTest(TestCase):
    """check_and_void_timed_out_runs correctly voids overdue ACTIVE TIMED runs."""

    def setUp(self):
        self.device = make_device(mac='BB:CC:DD:EE:FF:01')
        self.contest = Contest.objects.create(name='Timeout Cup')
        self.comp = Competition.objects.create(
            name='LF', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            timeout_seconds=30, num_laps=1,
            lap_timer=self.device,
            token=_secrets.token_hex(8),
        )
        self.team = Team.objects.create(name='Alpha', contest=self.contest, token=None)

    def _active_run(self, seconds_ago):
        """Create an ACTIVE run that started `seconds_ago` seconds in the past."""
        return Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now() - timedelta(seconds=seconds_ago),
            duration=0, state=RunState.ACTIVE,
        )

    def test_overdue_run_is_voided(self):
        run = self._active_run(seconds_ago=60)  # 60s > 30s timeout
        check_and_void_timed_out_runs()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)

    def test_not_yet_overdue_run_stays_active(self):
        run = self._active_run(seconds_ago=10)  # 10s < 30s timeout
        check_and_void_timed_out_runs()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)

    def test_exactly_at_deadline_is_voided(self):
        # start_time == now - timeout_seconds → deadline == now → overdue
        run = self._active_run(seconds_ago=30)
        check_and_void_timed_out_runs()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)

    def test_returns_count_of_voided_runs(self):
        self._active_run(seconds_ago=60)
        self._active_run(seconds_ago=60)
        count = check_and_void_timed_out_runs()
        self.assertEqual(count, 2)

    def test_returns_zero_when_nothing_overdue(self):
        self._active_run(seconds_ago=10)
        count = check_and_void_timed_out_runs()
        self.assertEqual(count, 0)

    def test_judged_run_not_auto_voided(self):
        judged_comp = Competition.objects.create(
            name='Arm', contest=self.contest,
            competition_type=CompetitionType.JUDGED,
            timeout_seconds=10, token=_secrets.token_hex(8),
        )
        run = Run.objects.create(
            team=self.team, competition=judged_comp,
            start_time=timezone.now() - timedelta(seconds=60),
            duration=0, state=RunState.ACTIVE,
        )
        check_and_void_timed_out_runs()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)

    def test_completed_run_not_re_voided(self):
        run = self._active_run(seconds_ago=60)
        run.state = RunState.COMPLETED
        run.time_ms = 5000
        run.save()
        check_and_void_timed_out_runs()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)

    def test_already_voided_run_not_re_processed(self):
        run = self._active_run(seconds_ago=60)
        run.state = RunState.VOIDED
        run.save()
        count = check_and_void_timed_out_runs()
        self.assertEqual(count, 0)

    def test_pending_run_not_voided(self):
        # PENDING runs are not ACTIVE so must not be touched
        run = Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now() - timedelta(seconds=60),
            duration=0, state=RunState.PENDING,
        )
        check_and_void_timed_out_runs()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.PENDING)

    def test_different_timeout_per_competition(self):
        # comp has timeout=30; comp2 has timeout=120
        comp2 = Competition.objects.create(
            name='LF2', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            timeout_seconds=120, num_laps=1,
            token=_secrets.token_hex(8),
        )
        run1 = self._active_run(seconds_ago=60)   # overdue (30s timeout)
        run2 = Run.objects.create(
            team=self.team, competition=comp2,
            start_time=timezone.now() - timedelta(seconds=60),
            duration=0, state=RunState.ACTIVE,
        )

        count = check_and_void_timed_out_runs()
        self.assertEqual(count, 1)

        run1.refresh_from_db()
        run2.refresh_from_db()
        self.assertEqual(run1.state, RunState.VOIDED)
        self.assertEqual(run2.state, RunState.ACTIVE)

# ── Phase 5.10: MQTT Fallback / Manual Mode tests ─────────────────────────────

import secrets as _secrets

from django.test import TestCase
from django.utils import timezone

from contest.models import Competition, CompetitionType, Contest, Result, Run, RunState, Team
from devices.models import LapTimerDevice
from scoring.engine import record_manual_result, void_active_runs_on_disconnect


def _make_device(mac='AA:BB:CC:DD:EE:F0'):
    d, _ = LapTimerDevice.objects.get_or_create(device_id=mac, defaults={'friendly_name': mac})
    return d


def _contest():
    return Contest.objects.create(name='Fallback Cup')


def _comp(contest, name='LF', ctype=CompetitionType.TIMED):
    return Competition.objects.create(
        name=name, contest=contest,
        competition_type=ctype,
        token=_secrets.token_hex(8),
    )


def _team(contest, name='Alpha'):
    return Team.objects.create(name=name, contest=contest, token=None)


def _run(team, comp, state=RunState.ACTIVE):
    return Run.objects.create(
        team=team, competition=comp,
        start_time=timezone.now(), duration=0, state=state,
    )


class VoidOnDisconnectTest(TestCase):
    """void_active_runs_on_disconnect voids ACTIVE TIMED runs only (E5/H5)."""

    def setUp(self):
        self.contest = _contest()
        self.comp = _comp(self.contest)
        self.team = _team(self.contest)

    def test_voids_active_run(self):
        run = _run(self.team, self.comp, state=RunState.ACTIVE)
        void_active_runs_on_disconnect()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)

    def test_returns_count_of_voided_runs(self):
        _run(self.team, self.comp, state=RunState.ACTIVE)
        team2 = _team(self.contest, 'Bravo')
        comp2 = _comp(self.contest, 'LF2', CompetitionType.TIMED)
        _run(team2, comp2, state=RunState.ACTIVE)
        count = void_active_runs_on_disconnect()
        self.assertEqual(count, 2)

    def test_judged_active_run_survives_disconnect(self):
        judged = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        run = _run(self.team, judged, state=RunState.ACTIVE)
        count = void_active_runs_on_disconnect()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)
        self.assertEqual(count, 0)

    def test_only_timed_runs_counted_in_mixed_contest(self):
        _run(self.team, self.comp, state=RunState.ACTIVE)          # TIMED
        judged = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        _run(_team(self.contest, 'Bravo'), judged, state=RunState.ACTIVE)
        self.assertEqual(void_active_runs_on_disconnect(), 1)

    def test_returns_zero_when_no_active_runs(self):
        _run(self.team, self.comp, state=RunState.PENDING)
        count = void_active_runs_on_disconnect()
        self.assertEqual(count, 0)

    def test_completed_run_not_voided(self):
        run = _run(self.team, self.comp, state=RunState.COMPLETED)
        void_active_runs_on_disconnect()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.COMPLETED)

    def test_pending_run_not_voided(self):
        run = _run(self.team, self.comp, state=RunState.PENDING)
        void_active_runs_on_disconnect()
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.PENDING)


class RecordManualResultTest(TestCase):
    """record_manual_result stores result on VOIDED runs correctly."""

    def setUp(self):
        self.contest = _contest()
        self.timed_comp = _comp(self.contest, 'LF', CompetitionType.TIMED)
        self.judged_comp = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        self.team = _team(self.contest)

    def _voided_run(self, comp):
        return _run(self.team, comp, state=RunState.VOIDED)

    def test_timed_manual_result_creates_result(self):
        run = self._voided_run(self.timed_comp)
        record_manual_result(run, time_ms=12500)
        self.assertTrue(Result.objects.filter(team=self.team, competition=self.timed_comp).exists())

    def test_judged_manual_result_creates_result(self):
        run = self._voided_run(self.judged_comp)
        record_manual_result(run, score=75)
        self.assertTrue(Result.objects.filter(team=self.team, competition=self.judged_comp).exists())

    def test_timed_stores_correct_time(self):
        run = self._voided_run(self.timed_comp)
        record_manual_result(run, time_ms=8000)
        r = Result.objects.get(team=self.team, competition=self.timed_comp)
        self.assertEqual(r.score, 8000)

    def test_judged_stores_correct_score(self):
        run = self._voided_run(self.judged_comp)
        record_manual_result(run, score=88)
        r = Result.objects.get(team=self.team, competition=self.judged_comp)
        self.assertEqual(r.score, 88)

    def test_result_marked_as_manual(self):
        run = self._voided_run(self.timed_comp)
        result = record_manual_result(run, time_ms=5000)
        self.assertTrue(result.is_manual)

    def test_non_voided_run_raises_value_error(self):
        run = _run(self.team, self.timed_comp, state=RunState.COMPLETED)
        with self.assertRaises(ValueError):
            record_manual_result(run, time_ms=5000)

    def test_timed_missing_time_ms_raises(self):
        run = self._voided_run(self.timed_comp)
        with self.assertRaises(ValueError):
            record_manual_result(run, score=50)

    def test_judged_missing_score_raises(self):
        run = self._voided_run(self.judged_comp)
        with self.assertRaises(ValueError):
            record_manual_result(run, time_ms=5000)

    def test_judged_score_out_of_range_raises(self):
        run = self._voided_run(self.judged_comp)
        with self.assertRaises(ValueError):
            record_manual_result(run, score=101)

    def test_timed_zero_time_raises(self):
        run = self._voided_run(self.timed_comp)
        with self.assertRaises(ValueError):
            record_manual_result(run, time_ms=0)

    def test_idempotent_second_call_updates_result(self):
        run = self._voided_run(self.timed_comp)
        record_manual_result(run, time_ms=10000)
        run2 = self._voided_run(self.timed_comp)
        record_manual_result(run2, time_ms=9000)
        results = Result.objects.filter(team=self.team, competition=self.timed_comp)
        self.assertEqual(results.count(), 1)
        self.assertEqual(results.first().score, 9000)

    def test_comment_stored_on_result(self):
        run = self._voided_run(self.timed_comp)
        record_manual_result(run, time_ms=5000, comment='Stopwatch measurement')
        r = Result.objects.get(team=self.team, competition=self.timed_comp)
        self.assertEqual(r.comment, 'Stopwatch measurement')


class ManualRunStateTest(TestCase):
    """Phase E4 (H3): a manual entry is stored on the Run and moves it to MANUAL."""

    def setUp(self):
        self.contest = _contest()
        self.timed_comp  = _comp(self.contest, 'LF',  CompetitionType.TIMED)
        self.judged_comp = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        self.team = _team(self.contest)

    def test_timed_value_stored_on_run(self):
        run = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        record_manual_result(run, time_ms=7200, comment='Stopwatch')
        run.refresh_from_db()
        self.assertEqual(run.time_ms, 7200)
        self.assertEqual(run.state, RunState.MANUAL)
        self.assertTrue(run.is_manual)
        self.assertEqual(run.judge_comment, 'Stopwatch')

    def test_judged_value_stored_on_run(self):
        run = _run(self.team, self.judged_comp, state=RunState.VOIDED)
        record_manual_result(run, score=64)
        run.refresh_from_db()
        self.assertEqual(run.score, 64)
        self.assertEqual(run.state, RunState.MANUAL)
        self.assertTrue(run.is_manual)

    def test_manual_run_can_be_best(self):
        run = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        record_manual_result(run, time_ms=7200)
        run.refresh_from_db()
        self.assertTrue(run.is_best)

    def test_manual_state_only_reachable_from_voided(self):
        for state in (RunState.PENDING, RunState.ACTIVE, RunState.COMPLETED, RunState.MANUAL):
            run = _run(self.team, self.timed_comp, state=state)
            with self.assertRaises(ValueError):
                record_manual_result(run, time_ms=5000)
            run.refresh_from_db()
            self.assertEqual(run.state, state)


class ManualVsAutomatedResultTest(TestCase):
    """Phase E4 (H3): manual entries take part in normal best-run selection."""

    def setUp(self):
        self.contest = _contest()
        self.timed_comp  = _comp(self.contest, 'LF',  CompetitionType.TIMED)
        self.judged_comp = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        self.team = _team(self.contest)

    def _completed_timed(self, time_ms):
        run = _run(self.team, self.timed_comp, state=RunState.COMPLETED)
        run.time_ms = time_ms
        run.save(update_fields=['time_ms'])
        _update_best_timed_result(run)
        return run

    def _completed_judged(self, score):
        run = _run(self.team, self.judged_comp, state=RunState.PENDING)
        score_judged_run(run, score)
        return run

    def _result(self, comp):
        return Result.objects.get(team=self.team, competition=comp)

    def test_slower_manual_does_not_clobber_better_automated(self):
        self._completed_timed(5000)
        voided = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        record_manual_result(voided, time_ms=9000)

        result = self._result(self.timed_comp)
        self.assertEqual(result.score, 5000)
        self.assertFalse(result.is_manual)
        voided.refresh_from_db()
        self.assertFalse(voided.is_best)

    def test_faster_manual_wins(self):
        automated = self._completed_timed(9000)
        voided = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        record_manual_result(voided, time_ms=5000, comment='Stopwatch')

        result = self._result(self.timed_comp)
        self.assertEqual(result.score, 5000)
        self.assertTrue(result.is_manual)
        self.assertEqual(result.comment, 'Stopwatch')
        voided.refresh_from_db(); automated.refresh_from_db()
        self.assertTrue(voided.is_best)
        self.assertFalse(automated.is_best)

    def test_later_better_automated_supersedes_manual(self):
        voided = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        record_manual_result(voided, time_ms=9000)
        self.assertTrue(self._result(self.timed_comp).is_manual)

        faster = self._completed_timed(6000)

        result = self._result(self.timed_comp)
        self.assertEqual(result.score, 6000)
        self.assertFalse(result.is_manual)
        faster.refresh_from_db(); voided.refresh_from_db()
        self.assertTrue(faster.is_best)
        self.assertFalse(voided.is_best)

    def test_manual_penalty_counts_towards_best(self):
        self._completed_timed(8000)
        voided = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        voided.penalty_time_ms = 3000
        voided.save(update_fields=['penalty_time_ms'])
        record_manual_result(voided, time_ms=7000)   # total 10000 — worse than 8000

        self.assertEqual(self._result(self.timed_comp).score, 8000)

    def test_lower_judged_manual_does_not_clobber(self):
        self._completed_judged(90)
        voided = _run(self.team, self.judged_comp, state=RunState.VOIDED)
        record_manual_result(voided, score=40)

        result = self._result(self.judged_comp)
        self.assertEqual(result.score, 90)
        self.assertFalse(result.is_manual)

    def test_higher_judged_manual_wins(self):
        self._completed_judged(50)
        voided = _run(self.team, self.judged_comp, state=RunState.VOIDED)
        record_manual_result(voided, score=85)

        result = self._result(self.judged_comp)
        self.assertEqual(result.score, 85)
        self.assertTrue(result.is_manual)


class ManualEntryViewTest(TestCase):
    """POST /contest/run/<id>/manual_entry records result on VOIDED run."""

    def setUp(self):
        self.contest = Contest.objects.create(name='View Fallback Cup')
        self.timed_comp = Competition.objects.create(
            name='LF View', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            token=_secrets.token_hex(8),
        )
        self.judged_comp = Competition.objects.create(
            name='Arm View', contest=self.contest,
            competition_type=CompetitionType.JUDGED,
            token=_secrets.token_hex(8),
        )
        self.team = Team.objects.create(name='Alpha View', contest=self.contest, token=None)
        self.user, _ = User.objects.get_or_create(username='testjudge_manual')
        _add_to_judge_group(self.user)
        self.client.force_login(self.user)

    def _voided_run(self, comp):
        return Run.objects.create(
            team=self.team, competition=comp,
            start_time=timezone.now(), duration=0, state=RunState.VOIDED,
        )

    def test_timed_manual_entry_redirects(self):
        run = self._voided_run(self.timed_comp)
        r = self.client.post(f'/contest/run/{run.id}/manual_entry', {'manual_time_ms': '7500'})
        self.assertEqual(r.status_code, 302)

    def test_judged_manual_entry_redirects(self):
        run = self._voided_run(self.judged_comp)
        r = self.client.post(f'/contest/run/{run.id}/manual_entry', {'score': '82'})
        self.assertEqual(r.status_code, 302)

    def test_non_voided_run_returns_400(self):
        run = Run.objects.create(
            team=self.team, competition=self.timed_comp,
            start_time=timezone.now(), duration=0, state=RunState.PENDING,
        )
        r = self.client.post(f'/contest/run/{run.id}/manual_entry', {'manual_time_ms': '7500'})
        self.assertEqual(r.status_code, 400)

    def test_missing_time_ms_returns_400(self):
        run = self._voided_run(self.timed_comp)
        r = self.client.post(f'/contest/run/{run.id}/manual_entry', {})
        self.assertEqual(r.status_code, 400)

    def test_invalid_judged_score_returns_400(self):
        run = self._voided_run(self.judged_comp)
        r = self.client.post(f'/contest/run/{run.id}/manual_entry', {'score': '200'})
        self.assertEqual(r.status_code, 400)

    def test_unknown_run_returns_404(self):
        r = self.client.post('/contest/run/99999/manual_entry', {'manual_time_ms': '5000'})
        self.assertEqual(r.status_code, 404)

    def test_timed_result_created_after_view(self):
        run = self._voided_run(self.timed_comp)
        self.client.post(f'/contest/run/{run.id}/manual_entry', {'manual_time_ms': '6200'})
        self.assertTrue(Result.objects.filter(team=self.team, competition=self.timed_comp).exists())


class TimeoutWebSocketPushTest(TestCase):
    """Phase B: the auto-timeout void path pushes a WebSocket update on the channel layer.

    Uses the in-memory layer active under test — proves _void_timed_out_run()
    group-sends a 'competition.update', the same signal browsers rely on for
    live board refresh (in real deployment this crosses processes via Redis).
    """

    def setUp(self):
        import secrets
        self.contest = make_contest()
        self.comp = Competition.objects.create(
            name='Timeout Push Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            num_laps=1, num_runs=3, timeout_seconds=1,
            token=secrets.token_hex(8),
        )
        self.team = make_team(self.contest)

    def test_timeout_void_pushes_ws_update(self):
        run = Run.objects.create(
            team=self.team, competition=self.comp,
            start_time=timezone.now() - timedelta(seconds=30),
            duration=0, state=RunState.ACTIVE,
        )
        layer = get_channel_layer()
        async_to_sync(layer.group_add)(f'competition_{self.comp.id}', 'ws-timeout-test')

        voided = check_and_void_timed_out_runs()

        self.assertEqual(voided, 1)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.VOIDED)
        msg = async_to_sync(layer.receive)('ws-timeout-test')
        self.assertEqual(msg['type'], 'competition.update')
        self.assertEqual(msg['competition_id'], self.comp.id)


# ── Phase F1: only approved devices may score (H4) ────────────────────────────

class DeviceApprovalGateTest(TestCase):
    """LapEvents from a device that is not ACTIVE never advance or finalize a run."""

    def setUp(self):
        self.device  = make_device(registration_status=RegistrationStatus.PENDING)
        self.contest = make_contest()
        self.competition = make_competition(self.contest, self.device, num_laps=1)
        self.team = make_team(self.contest)
        self.run  = make_run(self.team, self.competition, state=RunState.ACTIVE)

    def test_pending_device_event_is_not_assigned(self):
        event = make_lap_event(self.device, seq=1)
        self.assertIsNone(assign_lap_event_to_active_run(event))
        event.refresh_from_db()
        self.assertIsNone(event.run)

    def test_pending_device_event_is_still_recorded_for_audit(self):
        make_lap_event(self.device, seq=1)
        self.assertEqual(LapEvent.objects.filter(device=self.device).count(), 1)

    def test_inactive_device_event_is_not_assigned(self):
        self.device.registration_status = RegistrationStatus.INACTIVE
        self.device.save(update_fields=['registration_status'])
        self.assertIsNone(assign_lap_event_to_active_run(make_lap_event(self.device, seq=2)))

    def test_pending_device_events_do_not_finalize_the_run(self):
        make_lap_event(self.device, seq=1, offset_seconds=0)
        make_lap_event(self.device, seq=2, offset_seconds=10)
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.ACTIVE)
        self.assertIsNone(self.run.time_ms)
        self.assertFalse(Result.objects.filter(team=self.team, competition=self.competition).exists())

    def test_after_approval_events_score_normally(self):
        self.device.registration_status = RegistrationStatus.ACTIVE
        self.device.save(update_fields=['registration_status'])
        make_lap_event(self.device, seq=1, offset_seconds=0)
        make_lap_event(self.device, seq=2, offset_seconds=10)
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.COMPLETED)
        self.assertAlmostEqual(self.run.time_ms, 10000, delta=50)


class LapTimerAssignmentValidationTest(TestCase):
    """Forms refuse an unapproved device as a category's lap_timer."""

    def setUp(self):
        self.contest = make_contest()

    def _category_form(self, device):
        from contest.organise_views import CategoryForm
        return CategoryForm({
            'name': 'Line Following', 'description': '',
            'competition_type': CompetitionType.TIMED, 'status': 'OPEN',
            'num_runs': 3, 'num_laps': 1, 'timeout_seconds': 120,
            'lap_timer': device.pk,
        })

    def test_organiser_form_rejects_pending_device(self):
        form = self._category_form(make_device(registration_status=RegistrationStatus.PENDING))
        self.assertFalse(form.is_valid())
        self.assertIn('lap_timer', form.errors)

    def test_organiser_form_accepts_active_device(self):
        form = self._category_form(make_device(registration_status=RegistrationStatus.ACTIVE))
        self.assertTrue(form.is_valid(), form.errors)

    def test_admin_form_rejects_inactive_device(self):
        from contest.admin import CompetitionAdminForm
        device = make_device(registration_status=RegistrationStatus.INACTIVE)
        form = CompetitionAdminForm({
            'name': 'C', 'description': '', 'contest': self.contest.pk,
            'competition_type': CompetitionType.TIMED, 'state': 'WAITING', 'status': 'OPEN',
            'num_runs': 3, 'num_laps': 1, 'timeout_seconds': 120,
            'lap_timer': device.pk, 'token': 'admintoken1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('lap_timer', form.errors)

    def test_no_device_is_allowed(self):
        from contest.organise_views import CategoryForm
        form = CategoryForm({
            'name': 'Arm Task', 'description': '',
            'competition_type': CompetitionType.JUDGED, 'status': 'OPEN',
            'num_runs': 3, 'num_laps': 1, 'timeout_seconds': 120,
        })
        self.assertTrue(form.is_valid(), form.errors)


# ── Phase F5: penalties (H1) ──────────────────────────────────────────────────

from scoring.engine import record_penalty


class PenaltyModelTest(TestCase):
    """Run.total_time_ms and Run.effective_score are the values runs are ranked by."""

    def setUp(self):
        self.contest = _contest()
        self.timed_comp  = _comp(self.contest, 'LF',  CompetitionType.TIMED)
        self.judged_comp = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        self.team = _team(self.contest)

    def test_total_time_adds_penalty(self):
        run = _run(self.team, self.timed_comp, state=RunState.COMPLETED)
        run.time_ms, run.penalty_time_ms = 10000, 5000
        self.assertEqual(run.total_time_ms, 15000)

    def test_total_time_is_none_without_a_measurement(self):
        self.assertIsNone(_run(self.team, self.timed_comp).total_time_ms)

    def test_effective_score_subtracts_penalty(self):
        run = _run(self.team, self.judged_comp, state=RunState.COMPLETED)
        run.score, run.penalty_points = 80, 15
        self.assertEqual(run.effective_score, 65)

    def test_effective_score_floors_at_zero(self):
        run = _run(self.team, self.judged_comp, state=RunState.COMPLETED)
        run.score, run.penalty_points = 20, 50
        self.assertEqual(run.effective_score, 0)

    def test_effective_score_is_none_without_a_score(self):
        self.assertIsNone(_run(self.team, self.judged_comp).effective_score)


class RecordPenaltyTest(TestCase):
    """record_penalty validates input and re-runs the best-run selection."""

    def setUp(self):
        self.contest = _contest()
        self.timed_comp  = _comp(self.contest, 'LF',  CompetitionType.TIMED)
        self.judged_comp = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        self.team = _team(self.contest)

    def _completed_timed(self, time_ms=10000):
        run = _run(self.team, self.timed_comp, state=RunState.COMPLETED)
        run.time_ms = time_ms
        run.save(update_fields=['time_ms'])
        _update_best_timed_result(run)
        return run

    def _completed_judged(self, score=80):
        run = _run(self.team, self.judged_comp, state=RunState.COMPLETED)
        run.score = score
        run.save(update_fields=['score'])
        _update_best_judged_result(run)
        return run

    def test_timed_penalty_seconds_stored_as_ms(self):
        run = self._completed_timed()
        record_penalty(run, penalty_seconds='2.5')
        run.refresh_from_db()
        self.assertEqual(run.penalty_time_ms, 2500)

    def test_timed_penalty_updates_the_result(self):
        run = self._completed_timed(10000)
        record_penalty(run, penalty_seconds=5)
        result = Result.objects.get(team=self.team, competition=self.timed_comp)
        self.assertEqual(result.score, 15000)

    def test_judged_penalty_updates_the_result(self):
        run = self._completed_judged(80)
        record_penalty(run, penalty_points=30)
        result = Result.objects.get(team=self.team, competition=self.judged_comp)
        self.assertEqual(result.score, 50)

    def test_judged_result_floors_at_zero(self):
        run = self._completed_judged(20)
        record_penalty(run, penalty_points=90)
        result = Result.objects.get(team=self.team, competition=self.judged_comp)
        self.assertEqual(result.score, 0)

    def test_penalty_replaces_rather_than_accumulates(self):
        run = self._completed_timed()
        record_penalty(run, penalty_seconds=5)
        record_penalty(run, penalty_seconds=2)
        run.refresh_from_db()
        self.assertEqual(run.penalty_time_ms, 2000)

    def test_zero_clears_a_penalty(self):
        run = self._completed_timed()
        record_penalty(run, penalty_seconds=5)
        record_penalty(run, penalty_seconds=0)
        run.refresh_from_db()
        self.assertEqual(run.penalty_time_ms, 0)
        self.assertEqual(
            Result.objects.get(team=self.team, competition=self.timed_comp).score, 10000
        )

    def test_penalty_changes_which_timed_run_is_best(self):
        fast = self._completed_timed(10000)
        slow = _run(self.team, self.timed_comp, state=RunState.COMPLETED)
        slow.time_ms = 11000
        slow.save(update_fields=['time_ms'])
        _update_best_timed_result(slow)
        # 10.0s + 3s penalty = 13.0s, so the 11.0s run becomes the best one.
        record_penalty(fast, penalty_seconds=3)
        fast.refresh_from_db()
        slow.refresh_from_db()
        self.assertFalse(fast.is_best)
        self.assertTrue(slow.is_best)
        self.assertEqual(
            Result.objects.get(team=self.team, competition=self.timed_comp).score, 11000
        )

    def test_penalty_changes_which_judged_run_is_best(self):
        high = self._completed_judged(90)
        low  = _run(self.team, self.judged_comp, state=RunState.COMPLETED)
        low.score = 70
        low.save(update_fields=['score'])
        _update_best_judged_result(low)
        # 90 - 40 = 50, so the un-penalised 70 becomes the best run.
        record_penalty(high, penalty_points=40)
        high.refresh_from_db()
        low.refresh_from_db()
        self.assertFalse(high.is_best)
        self.assertTrue(low.is_best)
        self.assertEqual(
            Result.objects.get(team=self.team, competition=self.judged_comp).score, 70
        )

    def test_active_run_penalty_is_applied_when_it_finalizes(self):
        run = _run(self.team, self.timed_comp, state=RunState.ACTIVE)
        record_penalty(run, penalty_seconds=4)
        run.refresh_from_db()
        run.time_ms = 8000
        run.state   = RunState.COMPLETED
        run.save(update_fields=['time_ms', 'state'])
        _update_best_timed_result(run)
        self.assertEqual(
            Result.objects.get(team=self.team, competition=self.timed_comp).score, 12000
        )

    def test_pending_run_cannot_be_penalised(self):
        run = _run(self.team, self.timed_comp, state=RunState.PENDING)
        with self.assertRaises(ValueError):
            record_penalty(run, penalty_seconds=1)

    def test_voided_run_cannot_be_penalised(self):
        run = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        with self.assertRaises(ValueError):
            record_penalty(run, penalty_seconds=1)

    def test_manual_run_can_be_penalised(self):
        run = _run(self.team, self.timed_comp, state=RunState.VOIDED)
        record_manual_result(run, time_ms=9000)
        record_penalty(run, penalty_seconds=1)
        self.assertEqual(
            Result.objects.get(team=self.team, competition=self.timed_comp).score, 10000
        )

    def test_negative_penalty_raises(self):
        run = self._completed_timed()
        with self.assertRaises(ValueError):
            record_penalty(run, penalty_seconds=-1)

    def test_non_numeric_penalty_raises(self):
        run = self._completed_timed()
        with self.assertRaises(ValueError):
            record_penalty(run, penalty_seconds='abc')

    def test_missing_penalty_raises(self):
        run = self._completed_timed()
        with self.assertRaises(ValueError):
            record_penalty(run)

    def test_absurd_timed_penalty_raises(self):
        run = self._completed_timed()
        with self.assertRaises(ValueError):
            record_penalty(run, penalty_seconds=99999)

    def test_judged_penalty_over_100_raises(self):
        run = self._completed_judged()
        with self.assertRaises(ValueError):
            record_penalty(run, penalty_points=101)

    def test_penalty_flows_through_to_the_overall_ranking(self):
        from scoring.models import OverallResult

        # Own contest with a single category, so both teams are eligible overall.
        contest = Contest.objects.create(name='Penalty Ranking Cup')
        comp    = _comp(contest, 'Only Category', CompetitionType.TIMED)
        alpha   = _team(contest, name='Alpha')
        beta    = _team(contest, name='Beta')

        def _finish(team, time_ms):
            run = _run(team, comp, state=RunState.COMPLETED)
            run.time_ms = time_ms
            run.save(update_fields=['time_ms'])
            _update_best_timed_result(run)
            return run

        fast = _finish(alpha, 10000)
        _finish(beta, 11000)
        self.assertEqual(OverallResult.objects.get(contest=contest, team=alpha).rank, 1)

        record_penalty(fast, penalty_seconds=5)   # 15.0s — now the slowest
        self.assertEqual(OverallResult.objects.get(contest=contest, team=beta).rank, 1)
        self.assertEqual(OverallResult.objects.get(contest=contest, team=alpha).rank, 2)


class PenaltyViewTest(TestCase):
    """POST /contest/run/<id>/penalty — judge-only, validating, JSON."""

    def setUp(self):
        self.contest = _contest()
        self.timed_comp  = _comp(self.contest, 'LF',  CompetitionType.TIMED)
        self.judged_comp = _comp(self.contest, 'Arm', CompetitionType.JUDGED)
        self.team = _team(self.contest)
        self.judge = _add_to_judge_group(
            User.objects.create_user('penjudge', password='pw12345678')
        )
        self.client = Client()
        self.client.force_login(self.judge)

    def _completed(self, comp, **fields):
        run = _run(self.team, comp, state=RunState.COMPLETED)
        for key, value in fields.items():
            setattr(run, key, value)
        run.save()
        return run

    def test_judge_can_set_a_timed_penalty(self):
        run = self._completed(self.timed_comp, time_ms=10000)
        r = self.client.post(f'/contest/run/{run.id}/penalty', {'penalty_seconds': '3'})
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.penalty_time_ms, 3000)

    def test_judge_can_set_a_judged_penalty(self):
        run = self._completed(self.judged_comp, score=90)
        r = self.client.post(f'/contest/run/{run.id}/penalty', {'penalty_points': '25'})
        self.assertEqual(r.status_code, 200)
        run.refresh_from_db()
        self.assertEqual(run.penalty_points, 25)

    def test_invalid_penalty_returns_400(self):
        run = self._completed(self.timed_comp, time_ms=10000)
        r = self.client.post(f'/contest/run/{run.id}/penalty', {'penalty_seconds': '-4'})
        self.assertEqual(r.status_code, 400)

    def test_get_is_not_allowed(self):
        run = self._completed(self.timed_comp, time_ms=10000)
        self.assertEqual(self.client.get(f'/contest/run/{run.id}/penalty').status_code, 405)

    def test_non_judge_gets_403(self):
        run = self._completed(self.timed_comp, time_ms=10000)
        participant = User.objects.create_user('penpart', password='pw12345678')
        client = Client()
        client.force_login(participant)
        r = client.post(f'/contest/run/{run.id}/penalty', {'penalty_seconds': '3'})
        self.assertEqual(r.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        run = self._completed(self.timed_comp, time_ms=10000)
        r = Client().post(f'/contest/run/{run.id}/penalty', {'penalty_seconds': '3'})
        self.assertEqual(r.status_code, 302)
        self.assertIn('/accounts/login/', r['Location'])

    def test_score_form_accepts_a_penalty(self):
        run = _run(self.team, self.judged_comp, state=RunState.ACTIVE)
        self.client.post(f'/contest/run/{run.id}/score', {'score': '80', 'penalty_points': '20'})
        run.refresh_from_db()
        self.assertEqual(run.penalty_points, 20)
        self.assertEqual(
            Result.objects.get(team=self.team, competition=self.judged_comp).score, 60
        )

    def test_score_form_rejects_an_out_of_range_penalty(self):
        run = _run(self.team, self.judged_comp, state=RunState.ACTIVE)
        r = self.client.post(
            f'/contest/run/{run.id}/score', {'score': '80', 'penalty_points': '500'}
        )
        self.assertEqual(r.status_code, 400)
        run.refresh_from_db()
        self.assertEqual(run.state, RunState.ACTIVE)

    def _open_timed_run(self):
        """A completed run in an OPEN category — the board only offers controls then."""
        self.timed_comp.status = ItemStates.OPEN
        self.timed_comp.save(update_fields=['status'])
        run = self._completed(self.timed_comp, time_ms=10000)
        _update_best_timed_result(run)
        return run

    def test_timed_board_offers_a_penalty_box_to_a_judge(self):
        run  = self._open_timed_run()
        html = self.client.get(f'/contest/competition_board/{self.timed_comp.id}').content.decode()
        self.assertIn(f'setPenalty({run.id})', html)
        self.assertIn(f'pen-{run.id}', html)

    def test_timed_board_hides_the_penalty_box_from_the_public(self):
        self._open_timed_run()
        html = Client().get(f'/contest/competition_board/{self.timed_comp.id}').content.decode()
        self.assertNotIn('setPenalty(', html)

    def test_board_shows_the_effective_score(self):
        run = self._completed(self.judged_comp, score=90, penalty_points=30)
        _update_best_judged_result(run)
        html = self.client.get(f'/contest/competition_board/{self.judged_comp.id}').content.decode()
        self.assertIn('60', html)

    def test_mobile_score_screen_accepts_a_penalty(self):
        run = _run(self.team, self.judged_comp, state=RunState.ACTIVE)
        self.client.post(
            f'/contest/judge/{self.judged_comp.id}/run/{run.id}/score/',
            {'score': '70', 'penalty_points': '10'},
        )
        run.refresh_from_db()
        self.assertEqual(run.penalty_points, 10)
        self.assertEqual(run.effective_score, 60)

    def test_mobile_panel_offers_a_penalty_box_after_a_timed_run(self):
        run = self._completed(self.timed_comp, time_ms=10000)
        _update_best_timed_result(run)
        html = self.client.get(f'/contest/judge/{self.timed_comp.id}/').content.decode()
        self.assertIn('Penalty &mdash; last run', html)
        self.assertIn('doPenalty(', html)

    def test_mobile_panel_has_no_penalty_box_before_any_run(self):
        html = self.client.get(f'/contest/judge/{self.timed_comp.id}/').content.decode()
        self.assertNotIn('doPenalty(', html)

    def test_mobile_score_screen_shows_a_penalty_field(self):
        run = _run(self.team, self.judged_comp, state=RunState.ACTIVE)
        html = self.client.get(
            f'/contest/judge/{self.judged_comp.id}/run/{run.id}/score/'
        ).content.decode()
        self.assertIn('penalty_points', html)
