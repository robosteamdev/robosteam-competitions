"""Phase A — authorization & role integrity tests.

Covers the C1 finding: judge action endpoints and the mobile-judge views must be
restricted to Judge/Organiser/Administrator (or superuser). Anonymous users are
redirected to login; authenticated non-judges (Participants) get 403.

Also covers the Phase A registration hardening: duplicate emails are rejected and
the data-processing consent checkbox is mandatory.
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


class _Base(TestCase):
    def setUp(self):
        self.contest = Contest.objects.create(name='Authz Contest')
        self.timed = Competition.objects.create(
            name='Timed Cat', contest=self.contest,
            competition_type=CompetitionType.TIMED,
            status=ItemStates.OPEN, num_laps=1, num_runs=5, token=_tok(),
        )
        self.judged = Competition.objects.create(
            name='Judged Cat', contest=self.contest,
            competition_type=CompetitionType.JUDGED,
            status=ItemStates.OPEN, num_runs=5, token=_tok(),
        )
        self.team = Team.objects.create(name='Authz Team', contest=self.contest, token=_tok())

        self.pending_timed = Run.objects.create(
            team=self.team, competition=self.timed,
            start_time=timezone.now(), duration=0, state=RunState.PENDING,
        )
        self.active_timed = Run.objects.create(
            team=self.team, competition=self.timed,
            start_time=timezone.now(), duration=0, state=RunState.ACTIVE,
        )
        self.voided_timed = Run.objects.create(
            team=self.team, competition=self.timed,
            start_time=timezone.now(), duration=0, state=RunState.VOIDED,
        )
        self.pending_judged = Run.objects.create(
            team=self.team, competition=self.judged,
            start_time=timezone.now(), duration=0, state=RunState.PENDING,
        )

    # endpoint URL helpers
    def _protected_get_urls(self):
        return [
            ('judge_select',      '/contest/judge/'),
            ('judge_panel',       f'/contest/judge/{self.timed.id}/'),
            ('judge_panel_state', f'/contest/judge/{self.timed.id}/state/'),
            ('judge_score',       f'/contest/judge/{self.judged.id}/run/{self.pending_judged.id}/score/'),
        ]

    def _protected_post_urls(self):
        return [
            ('run_create',   f'/contest/competition/{self.timed.id}/run/create', {'team_id': self.team.id}),
            ('run_start',    f'/contest/run/{self.pending_judged.id}/start', {}),
            ('run_stop',     f'/contest/run/{self.active_timed.id}/stop', {}),
            ('run_score',    f'/contest/run/{self.pending_judged.id}/score', {'score': '50'}),
            ('manual_entry', f'/contest/run/{self.voided_timed.id}/manual_entry', {'manual_time_ms': '1000'}),
        ]


class AuthzAnonymousTest(_Base):
    """Anonymous users get redirected to login on every protected endpoint."""

    def test_anonymous_get_endpoints_redirect_to_login(self):
        for name, url in self._protected_get_urls():
            with self.subTest(endpoint=name):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 302, name)
                self.assertIn('/accounts/login/', r['Location'], name)

    def test_anonymous_post_endpoints_redirect_to_login(self):
        for name, url, data in self._protected_post_urls():
            with self.subTest(endpoint=name):
                r = self.client.post(url, data)
                self.assertEqual(r.status_code, 302, name)
                self.assertIn('/accounts/login/', r['Location'], name)


class AuthzParticipantDeniedTest(_Base):
    """A logged-in Participant (non-judge) is forbidden (403) from every endpoint."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('participant1', password='pass', is_active=True)
        self.user.groups.add(_group('Participant'))
        self.client.force_login(self.user)

    def test_participant_denied_get_endpoints(self):
        for name, url in self._protected_get_urls():
            with self.subTest(endpoint=name):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 403, name)

    def test_participant_denied_run_create(self):
        r = self.client.post(f'/contest/competition/{self.timed.id}/run/create', {'team_id': self.team.id})
        self.assertEqual(r.status_code, 403)

    def test_participant_denied_run_start(self):
        r = self.client.post(f'/contest/run/{self.pending_judged.id}/start')
        self.assertEqual(r.status_code, 403)

    def test_participant_denied_run_stop(self):
        r = self.client.post(f'/contest/run/{self.active_timed.id}/stop')
        self.assertEqual(r.status_code, 403)

    def test_participant_denied_run_score(self):
        r = self.client.post(f'/contest/run/{self.pending_judged.id}/score', {'score': '50'})
        self.assertEqual(r.status_code, 403)

    def test_participant_denied_manual_entry(self):
        r = self.client.post(f'/contest/run/{self.voided_timed.id}/manual_entry', {'manual_time_ms': '1000'})
        self.assertEqual(r.status_code, 403)


class AuthzJudgeAllowedTest(_Base):
    """A Judge-group user is allowed through every endpoint."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user('judge1', password='pass', is_active=True)
        self.user.groups.add(_group('Judge'))
        self.client.force_login(self.user)

    def test_judge_allowed_get_endpoints(self):
        for name, url in self._protected_get_urls():
            with self.subTest(endpoint=name):
                r = self.client.get(url)
                self.assertEqual(r.status_code, 200, name)

    def test_judge_allowed_run_create(self):
        before = Run.objects.filter(team=self.team, competition=self.timed).count()
        r = self.client.post(f'/contest/competition/{self.timed.id}/run/create', {'team_id': self.team.id})
        self.assertIn(r.status_code, (200, 302))
        self.assertEqual(Run.objects.filter(team=self.team, competition=self.timed).count(), before + 1)

    def test_judge_allowed_run_start(self):
        # JUDGED run avoids the MQTT publish path — keeps the test hermetic.
        r = self.client.post(f'/contest/run/{self.pending_judged.id}/start')
        self.assertEqual(r.status_code, 200)
        self.pending_judged.refresh_from_db()
        self.assertEqual(self.pending_judged.state, RunState.ACTIVE)


class AuthzOtherRolesAllowedTest(_Base):
    """Organiser and superuser also satisfy judge_required (the decorator's other paths)."""

    def test_organiser_allowed_judge_select(self):
        user = User.objects.create_user('org1', password='pass', is_active=True)
        user.groups.add(_group('Organiser'))
        self.client.force_login(user)
        r = self.client.get('/contest/judge/')
        self.assertEqual(r.status_code, 200)

    def test_superuser_allowed_run_create(self):
        admin = User.objects.create_superuser('super1', password='pass')
        self.client.force_login(admin)
        r = self.client.post(f'/contest/competition/{self.timed.id}/run/create', {'team_id': self.team.id})
        self.assertIn(r.status_code, (200, 302))


class AuthzRegistrationHardeningTest(TestCase):
    """Phase A registration hardening: duplicate email + mandatory consent."""

    def _base_payload(self, **over):
        data = {
            'username': 'brandnew',
            'first_name': 'Bran', 'last_name': 'New',
            'email': 'brandnew@example.com',
            'password': 'strongpass1', 'password2': 'strongpass1',
            'requested_role': 'judge',
            'consent': 'on',
        }
        data.update(over)
        return data

    def test_duplicate_email_rejected(self):
        User.objects.create_user('existing', email='dupe@example.com', password='x')
        r = self.client.post('/contest/register/', self._base_payload(username='someoneelse', email='DUPE@example.com'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'email already exists')
        self.assertFalse(User.objects.filter(username='someoneelse').exists())

    def test_consent_required(self):
        payload = self._base_payload()
        payload.pop('consent')
        r = self.client.post('/contest/register/', payload)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username='brandnew').exists())
        self.assertIn('consent', r.context['form'].errors)


class RobotActionEndpointRemovedTest(TestCase):
    """Phase F4 (H7): the unauthenticated token-based run API is gone for good.

    It let anyone holding a team token create and stop runs without a judge,
    without the run limit and without the hardware timer. Robots are driven over
    MQTT instead.
    """

    def test_robot_action_url_no_longer_resolves(self):
        r = self.client.post(
            '/contest/robot_action',
            data='{"teamtoken": "x", "competitiontoken": "y", "action": "start"}',
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 404)

    def test_view_is_gone_from_the_module(self):
        from contest import views
        self.assertFalse(hasattr(views, 'robot_action'))
