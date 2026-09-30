"""
Phase 5.21 section B — the participant "My Robots" views.

The security model here is unusual and worth stating once: claiming is MAC-only
with no verification (Decision 042) and robots need no approval (Decision 041),
so the guards that remain — owner scoping, unowned-only claiming and the claim
rate limit — are load-bearing rather than defence in depth. Most of this file
tests those.
"""
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from contest.models import Contest, ContestRegistration, ItemStates, Team, UserProfile
from contest.participate_views import CLAIM_MAX_ATTEMPTS, normalise_mac
from devices.models import DeviceType, LapTimerDevice, RegistrationStatus


def make_robot(mac, name='Rover', owner=None):
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name=name, device_type=DeviceType.ROBOT,
        registration_status=RegistrationStatus.ACTIVE, owner=owner,
    )


class ParticipantMixin:
    def make_participant(self, username):
        user = User.objects.create_user(username, password='pw12345!')
        group, _ = Group.objects.get_or_create(name='Participant')
        user.groups.add(group)
        UserProfile.objects.create(user=user, requested_role='participant',
                                   team_name=f'{username} team')
        return user


class MacNormalisationTest(TestCase):
    """Students read MACs off a sticker or a serial log — accept what they type."""

    def test_accepted_forms_all_normalise(self):
        for raw in ['AA:BB:CC:DD:EE:01', 'aa:bb:cc:dd:ee:01', 'AA-BB-CC-DD-EE-01',
                    'aabbccddee01', 'AABB.CCDD.EE01', '  aa bb cc dd ee 01  ']:
            self.assertEqual(normalise_mac(raw), 'AA:BB:CC:DD:EE:01', raw)

    def test_rubbish_is_rejected(self):
        for raw in ['', None, 'not a mac', 'AA:BB:CC:DD:EE', 'AA:BB:CC:DD:EE:01:02', 'ZZ:BB:CC:DD:EE:01']:
            self.assertIsNone(normalise_mac(raw), raw)


class RobotsListAccessTest(ParticipantMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.alice = self.make_participant('alice')

    def test_anonymous_is_sent_to_login(self):
        r = self.client.get(reverse('contest:robots_list'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login', r.url)

    def test_a_non_participant_is_turned_away(self):
        User.objects.create_user('judge1', password='pw12345!')
        self.client.login(username='judge1', password='pw12345!')
        r = self.client.get(reverse('contest:robots_list'))
        self.assertRedirects(r, reverse('contest:index'))

    def test_a_participant_sees_the_page(self):
        self.client.login(username='alice', password='pw12345!')
        r = self.client.get(reverse('contest:robots_list'))
        self.assertEqual(r.status_code, 200)

    def test_another_participants_robot_is_never_rendered(self):
        bob = self.make_participant('bob')
        make_robot('CC:00:00:00:00:01', 'Bobs Robot', owner=bob)
        self.client.login(username='alice', password='pw12345!')
        r = self.client.get(reverse('contest:robots_list'))
        self.assertNotContains(r, 'CC:00:00:00:00:01')
        self.assertNotContains(r, 'Bobs Robot')

    def test_unclaimed_robots_are_never_listed(self):
        """Decision 042: you must already know the MAC. The page must not be a directory."""
        make_robot('CC:00:00:00:00:02', 'Unclaimed Spare')
        self.client.login(username='alice', password='pw12345!')
        r = self.client.get(reverse('contest:robots_list'))
        self.assertNotContains(r, 'CC:00:00:00:00:02')
        self.assertNotContains(r, 'Unclaimed Spare')


class RobotClaimViewTest(ParticipantMixin, TestCase):
    URL = None

    def setUp(self):
        cache.clear()
        self.URL   = reverse('contest:robot_claim')
        self.alice = self.make_participant('alice')
        self.client.login(username='alice', password='pw12345!')

    def test_claiming_an_unowned_robot_works(self):
        robot = make_robot('DD:00:00:00:00:01')
        self.client.post(self.URL, {'mac': 'dd-00-00-00-00-01'})
        robot.refresh_from_db()
        self.assertEqual(robot.owner, self.alice)

    def test_claiming_someone_elses_robot_fails(self):
        bob   = self.make_participant('bob')
        robot = make_robot('DD:00:00:00:00:02', owner=bob)
        self.client.post(self.URL, {'mac': robot.device_id})
        robot.refresh_from_db()
        self.assertEqual(robot.owner, bob)

    def test_claiming_a_lap_timer_fails(self):
        timer = LapTimerDevice.objects.create(
            device_id='DD:00:00:00:00:03', friendly_name='Track timer',
            device_type=DeviceType.LAPTIMER,
        )
        self.client.post(self.URL, {'mac': timer.device_id})
        timer.refresh_from_db()
        self.assertIsNone(timer.owner)

    def test_an_unknown_mac_reports_that_it_has_not_connected(self):
        r = self.client.post(self.URL, {'mac': 'DD:00:00:00:00:99'}, follow=True)
        self.assertContains(r, 'has connected to the server yet')

    def test_a_malformed_mac_is_rejected_before_any_lookup(self):
        r = self.client.post(self.URL, {'mac': 'my robot'}, follow=True)
        self.assertContains(r, 'not a MAC address')

    def test_get_does_not_claim(self):
        robot = make_robot('DD:00:00:00:00:04')
        self.client.get(self.URL)
        robot.refresh_from_db()
        self.assertIsNone(robot.owner)

    def test_the_claim_rate_limit_stops_address_walking(self):
        for i in range(CLAIM_MAX_ATTEMPTS):
            self.client.post(self.URL, {'mac': f'DD:00:00:00:01:{i:02X}'})
        robot = make_robot('DD:00:00:00:00:05')
        r = self.client.post(self.URL, {'mac': robot.device_id}, follow=True)
        self.assertContains(r, 'Too many claim attempts')
        robot.refresh_from_db()
        self.assertIsNone(robot.owner)


class RobotManageViewTest(ParticipantMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.alice = self.make_participant('alice')
        self.bob   = self.make_participant('bob')
        self.robot = make_robot('EE:00:00:00:00:01', 'Rover', owner=self.alice)
        self.client.login(username='alice', password='pw12345!')

    def test_rename(self):
        self.client.post(reverse('contest:robot_rename', args=[self.robot.id]),
                         {'friendly_name': 'Speedy'})
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.friendly_name, 'Speedy')

    def test_an_empty_name_is_refused(self):
        self.client.post(reverse('contest:robot_rename', args=[self.robot.id]),
                         {'friendly_name': '   '})
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.friendly_name, 'Rover')

    def test_i_cannot_rename_someone_elses_robot(self):
        theirs = make_robot('EE:00:00:00:00:02', 'Bobs', owner=self.bob)
        r = self.client.post(reverse('contest:robot_rename', args=[theirs.id]),
                             {'friendly_name': 'Mine now'})
        self.assertEqual(r.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.friendly_name, 'Bobs')

    def test_i_cannot_release_someone_elses_robot(self):
        theirs = make_robot('EE:00:00:00:00:03', owner=self.bob)
        r = self.client.post(reverse('contest:robot_release', args=[theirs.id]))
        self.assertEqual(r.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.owner, self.bob)

    def test_release_frees_the_robot(self):
        self.client.post(reverse('contest:robot_release', args=[self.robot.id]))
        self.robot.refresh_from_db()
        self.assertIsNone(self.robot.owner)


class RobotBindViewTest(ParticipantMixin, TestCase):
    def setUp(self):
        cache.clear()
        self.alice   = self.make_participant('alice')
        self.bob     = self.make_participant('bob')
        self.contest = Contest.objects.create(name='Spring', status=ItemStates.OPEN)
        self.team    = Team.objects.create(name='alice team', contest=self.contest)
        self.reg     = ContestRegistration.objects.create(
            user=self.alice, contest=self.contest, team=self.team)
        self.robot   = make_robot('FF:00:00:00:00:01', 'Rover', owner=self.alice)
        self.client.login(username='alice', password='pw12345!')

    def _bind(self, value):
        return self.client.post(
            reverse('contest:robot_bind', args=[self.contest.id]), {'robot': value})

    def test_binding_my_robot_to_my_team(self):
        self._bind(self.robot.id)
        self.team.refresh_from_db()
        self.assertEqual(self.team.robot, self.robot)

    def test_binding_empty_clears_the_robot(self):
        self.team.robot = self.robot
        self.team.save()
        self._bind('')
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)

    def test_i_cannot_bind_a_robot_i_do_not_own(self):
        theirs = make_robot('FF:00:00:00:00:02', owner=self.bob)
        r = self._bind(theirs.id)
        self.assertEqual(r.status_code, 404)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)

    def test_i_cannot_bind_into_a_contest_i_have_not_joined(self):
        other = Contest.objects.create(name='Autumn', status=ItemStates.OPEN)
        r = self.client.post(reverse('contest:robot_bind', args=[other.id]),
                             {'robot': self.robot.id})
        self.assertEqual(r.status_code, 404)

    def test_a_robot_already_used_by_another_team_here_is_refused_gracefully(self):
        """
        Reachable via the organiser override. The unique constraint must surface
        as a message, not a 500.
        """
        rival = Team.objects.create(name='rivals', contest=self.contest, robot=self.robot)
        r = self._bind(self.robot.id)
        self.assertEqual(r.status_code, 302)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)
        rival.refresh_from_db()
        self.assertEqual(rival.robot, self.robot)
