from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contest.models import (
    Competition, Contest, ContestRegistration, ItemStates, Run, RunState, Team, UserProfile,
)


def _make_participant(username='participant1', team_name='BG Team A'):
    group, _ = Group.objects.get_or_create(name='Participant')
    user = User.objects.create_user(username=username, password='pass', is_active=True)
    user.groups.add(group)
    UserProfile.objects.create(user=user, requested_role='participant', team_name=team_name)
    return user


def _make_open_contest(name='Open Contest'):
    return Contest.objects.create(name=name, status=ItemStates.OPEN)


class RegistrationParticipantTest(TestCase):

    def test_participant_registration_auto_activates(self):
        """POST with participant role creates active user in Participant group with team_name set."""
        Group.objects.get_or_create(name='Participant')
        resp = self.client.post(reverse('contest:register'), {
            'username': 'newpart',
            'first_name': 'New',
            'last_name': 'Part',
            'email': 'new@example.com',
            'password': 'strongpass1',
            'password2': 'strongpass1',
            'requested_role': 'participant',
            'team_name': 'Alpha Team',
            'consent': 'on',
        })
        self.assertRedirects(resp, reverse('contest:register_participant_done'))
        user = User.objects.get(username='newpart')
        self.assertTrue(user.is_active)
        self.assertIn('Participant', [g.name for g in user.groups.all()])
        self.assertEqual(user.profile.team_name, 'Alpha Team')

    def test_participant_registration_requires_team_name(self):
        """POST with participant role and no team_name returns form error."""
        Group.objects.get_or_create(name='Participant')
        resp = self.client.post(reverse('contest:register'), {
            'username': 'noname',
            'first_name': 'No',
            'last_name': 'Name',
            'email': 'no@example.com',
            'password': 'strongpass1',
            'password2': 'strongpass1',
            'requested_role': 'participant',
            'team_name': '',
            'consent': 'on',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='noname').exists())
        self.assertIn('team_name', resp.context['form'].errors)

    def test_judge_registration_stays_inactive(self):
        """POST with judge role creates inactive user and redirects to pending page."""
        resp = self.client.post(reverse('contest:register'), {
            'username': 'newjudge',
            'first_name': 'Judge',
            'last_name': 'One',
            'email': 'j@example.com',
            'password': 'strongpass1',
            'password2': 'strongpass1',
            'requested_role': 'judge',
            'team_name': '',
            'consent': 'on',
        })
        self.assertRedirects(resp, reverse('contest:register_pending'))
        user = User.objects.get(username='newjudge')
        self.assertFalse(user.is_active)


class ParticipantDashboardTest(TestCase):

    def test_dashboard_unauthenticated_redirects(self):
        resp = self.client.get(reverse('contest:participate_dashboard'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_dashboard_as_participant_returns_200(self):
        user = _make_participant()
        self.client.force_login(user)
        resp = self.client.get(reverse('contest:participate_dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_as_non_participant_redirects_to_index(self):
        """A judge visiting /participate/ is redirected to the contest index."""
        judge = User.objects.create_user(username='judge', password='pass', is_active=True)
        self.client.force_login(judge)
        resp = self.client.get(reverse('contest:participate_dashboard'))
        self.assertRedirects(resp, reverse('contest:index'))


class ParticipantJoinLeaveTest(TestCase):

    def setUp(self):
        Group.objects.get_or_create(name='Participant')
        self.user    = _make_participant()
        self.contest = _make_open_contest()

    def test_join_creates_registration_and_team(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('contest:participate_join', args=[self.contest.id])
        )
        self.assertRedirects(resp, reverse('contest:participate_dashboard'))
        self.assertEqual(
            ContestRegistration.objects.filter(user=self.user, contest=self.contest).count(), 1
        )
        reg = ContestRegistration.objects.get(user=self.user, contest=self.contest)
        self.assertEqual(reg.team.name, 'BG Team A')
        self.assertEqual(reg.team.contest, self.contest)

    def test_join_idempotent(self):
        self.client.force_login(self.user)
        url = reverse('contest:participate_join', args=[self.contest.id])
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(
            ContestRegistration.objects.filter(user=self.user, contest=self.contest).count(), 1
        )

    def test_join_as_non_participant_redirects_to_index(self):
        other = User.objects.create_user(username='other', password='pass', is_active=True)
        self.client.force_login(other)
        resp = self.client.post(
            reverse('contest:participate_join', args=[self.contest.id])
        )
        self.assertRedirects(resp, reverse('contest:index'))
        self.assertEqual(ContestRegistration.objects.count(), 0)

    def test_leave_deletes_registration_and_team(self):
        self.client.force_login(self.user)
        self.client.post(reverse('contest:participate_join', args=[self.contest.id]))
        self.assertEqual(ContestRegistration.objects.count(), 1)
        resp = self.client.post(
            reverse('contest:participate_leave', args=[self.contest.id])
        )
        self.assertRedirects(resp, reverse('contest:participate_dashboard'))
        self.assertEqual(ContestRegistration.objects.count(), 0)
        self.assertEqual(Team.objects.filter(contest=self.contest).count(), 0)

    def test_leave_running_contest_blocked(self):
        """Cannot withdraw once a competition is RUNNING."""
        self.contest.status = ItemStates.OPEN
        self.contest.save()
        self.client.force_login(self.user)
        self.client.post(reverse('contest:participate_join', args=[self.contest.id]))
        # Transition to RUNNING after join
        self.contest.status = 'CLOSED'
        self.contest.save()
        self.client.post(reverse('contest:participate_leave', args=[self.contest.id]))
        self.assertEqual(ContestRegistration.objects.count(), 1)


class LeaveWithRunsTest(TestCase):
    """Phase E7 (H6): a team that has competed cannot be deleted by the participant."""

    def setUp(self):
        self.user    = _make_participant()
        self.contest = _make_open_contest()
        self.client.force_login(self.user)
        self.client.post(reverse('contest:participate_join', args=[self.contest.id]))
        self.team = Team.objects.get(contest=self.contest)

    def _add_run(self, state=RunState.COMPLETED):
        competition = Competition.objects.create(
            name='Cat', contest=self.contest, status=ItemStates.OPEN,
        )
        return Run.objects.create(
            team=self.team, competition=competition,
            start_time=timezone.now(), duration=0, state=state,
        )

    def _leave(self):
        return self.client.post(reverse('contest:participate_leave', args=[self.contest.id]))

    def test_leave_blocked_when_team_has_runs(self):
        self._add_run()
        resp = self._leave()
        self.assertRedirects(resp, reverse('contest:participate_dashboard'))
        self.assertEqual(ContestRegistration.objects.count(), 1)
        self.assertTrue(Team.objects.filter(pk=self.team.pk).exists())

    def test_leave_blocked_even_for_a_voided_run(self):
        self._add_run(state=RunState.VOIDED)
        self._leave()
        self.assertTrue(Team.objects.filter(pk=self.team.pk).exists())

    def test_blocked_leave_explains_why(self):
        self._add_run()
        resp = self.client.post(
            reverse('contest:participate_leave', args=[self.contest.id]), follow=True,
        )
        self.assertContains(resp, 'contact the organiser')

    def test_leave_still_works_without_runs(self):
        self._leave()
        self.assertEqual(ContestRegistration.objects.count(), 0)
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())


class AccountCancelTest(TestCase):

    def test_cancel_unauthenticated_redirects_to_login(self):
        resp = self.client.get(reverse('contest:account_cancel'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])

    def test_cancel_post_deactivates_and_logs_out(self):
        user = User.objects.create_user(username='victim', password='pass', is_active=True)
        self.client.force_login(user)
        resp = self.client.post(reverse('contest:account_cancel'))
        self.assertRedirects(resp, reverse('contest:account_cancelled'))
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        # Session should be cleared — subsequent request is unauthenticated
        me_resp = self.client.get(reverse('contest:participate_dashboard'))
        self.assertEqual(me_resp.status_code, 302)
        self.assertIn('/accounts/login/', me_resp['Location'])
