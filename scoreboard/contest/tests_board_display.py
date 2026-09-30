"""Phase E3 — Final Results ordering and formatting on the competition board (fixes H2).

The CLOSED-category "Final Results" block used to order every category by score
ascending (backwards for JUDGED) and printed TIMED scores as raw milliseconds.
"""
import secrets

from django.test import TestCase

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Result, Team,
)


def _tok():
    return secrets.token_hex(8)


class _ClosedBoardBase(TestCase):
    competition_type = CompetitionType.TIMED

    def setUp(self):
        self.contest = Contest.objects.create(name='Display Contest', status=ItemStates.OPEN)
        self.comp = Competition.objects.create(
            name='Display Cat', contest=self.contest,
            competition_type=self.competition_type,
            status=ItemStates.CLOSED, num_laps=1, num_runs=3, token=_tok(),
        )
        self.board_url    = f'/contest/competition_board/{self.comp.id}'
        self.fragment_url = f'/contest/competition/{self.comp.id}/fragment/'

    def _result(self, name, score):
        team = Team.objects.create(name=name, contest=self.contest)
        return Result.objects.create(team=team, competition=self.comp, score=score)

    def _team_order(self, html, names):
        """Positions of each team name in the rendered page, in listed order."""
        return [html.index(name) for name in names]


class ClosedTimedBoardTest(_ClosedBoardBase):
    competition_type = CompetitionType.TIMED

    def setUp(self):
        super().setUp()
        self._result('Slow', 83250)
        self._result('Fast', 15432)

    def test_results_ordered_fastest_first(self):
        r = self.client.get(self.board_url)
        self.assertEqual(r.status_code, 200)
        names = [res.team.name for res in r.context['results']]
        self.assertEqual(names, ['Fast', 'Slow'])

    def test_fastest_team_rendered_first(self):
        html = self.client.get(self.board_url).content.decode()
        fast, slow = self._team_order(html, ['Fast', 'Slow'])
        self.assertLess(fast, slow)

    def test_times_are_formatted_not_raw_milliseconds(self):
        html = self.client.get(self.board_url).content.decode()
        self.assertIn('1:23.250', html)
        self.assertIn('0:15.432', html)
        self.assertNotIn('83250', html)

    def test_fragment_uses_same_ordering(self):
        r = self.client.get(self.fragment_url)
        self.assertEqual(r.status_code, 200)
        names = [res.team.name for res in r.context['results']]
        self.assertEqual(names, ['Fast', 'Slow'])
        self.assertIn('1:23.250', r.content.decode())


class ClosedJudgedBoardTest(_ClosedBoardBase):
    competition_type = CompetitionType.JUDGED

    def setUp(self):
        super().setUp()
        self._result('Weak', 42)
        self._result('Strong', 95)

    def test_results_ordered_highest_score_first(self):
        r = self.client.get(self.board_url)
        names = [res.team.name for res in r.context['results']]
        self.assertEqual(names, ['Strong', 'Weak'])

    def test_highest_score_rendered_first(self):
        html = self.client.get(self.board_url).content.decode()
        strong, weak = self._team_order(html, ['Strong', 'Weak'])
        self.assertLess(strong, weak)

    def test_scores_shown_as_points_not_time(self):
        html = self.client.get(self.board_url).content.decode()
        self.assertIn('95 pts', html)
        self.assertNotIn('0:00.095', html)

    def test_fragment_uses_same_ordering(self):
        r = self.client.get(self.fragment_url)
        names = [res.team.name for res in r.context['results']]
        self.assertEqual(names, ['Strong', 'Weak'])
