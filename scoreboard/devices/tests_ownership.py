"""
Phase 5.21 section A — robot ownership and the team binding.

Covers Decisions 040 (team-owned, one robot per team) and 042 (MAC-only claiming),
at the model layer. The views that drive these are tested in section B.
"""
import json

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from contest.models import Contest, ItemStates, Team
from devices.models import DeviceType, LapTimerDevice, RegistrationStatus


def make_device(mac, device_type=DeviceType.ROBOT, **kwargs):
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name=kwargs.pop('name', f'Device {mac[-2:]}'),
        device_type=device_type,
        registration_status=kwargs.pop('registration_status', RegistrationStatus.ACTIVE),
        **kwargs,
    )


class RobotClaimTest(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user('alice', password='x')
        self.bob   = User.objects.create_user('bob', password='x')
        self.robot = make_device('AA:00:00:00:00:01')

    def test_claiming_an_unowned_robot_binds_it(self):
        self.robot.claim(self.alice)
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.owner, self.alice)
        self.assertIsNotNone(self.robot.claimed_at)

    def test_a_claimed_robot_cannot_be_stolen(self):
        """
        The whole of Decision 042's security lives here.

        Claiming is MAC-only with no verification, so the single rule that keeps
        one team's robot out of another team's hands is that an owned device is
        never silently re-assigned.
        """
        self.robot.claim(self.alice)
        with self.assertRaises(ValidationError):
            self.robot.claim(self.bob)
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.owner, self.alice)

    def test_lap_timers_cannot_be_claimed(self):
        """Timers are venue infrastructure and never appear in the participant UI."""
        timer = make_device('AA:00:00:00:00:02', DeviceType.LAPTIMER)
        with self.assertRaises(ValidationError):
            timer.claim(self.alice)
        timer.refresh_from_db()
        self.assertIsNone(timer.owner)

    def test_release_returns_the_robot_to_the_pool(self):
        self.robot.claim(self.alice)
        self.robot.release()
        self.robot.refresh_from_db()
        self.assertIsNone(self.robot.owner)
        self.assertIsNone(self.robot.claimed_at)

    def test_a_released_robot_can_be_claimed_by_someone_else(self):
        self.robot.claim(self.alice)
        self.robot.release()
        self.robot.claim(self.bob)
        self.robot.refresh_from_db()
        self.assertEqual(self.robot.owner, self.bob)


class TeamRobotBindingTest(TestCase):
    def setUp(self):
        self.contest = Contest.objects.create(name='Spring 2026', status=ItemStates.OPEN)
        self.other   = Contest.objects.create(name='Autumn 2026', status=ItemStates.OPEN)
        self.robot   = make_device('BB:00:00:00:00:01')

    def test_a_team_can_be_bound_to_a_robot(self):
        team = Team.objects.create(name='Alpha', contest=self.contest, robot=self.robot)
        self.assertEqual(team.robot, self.robot)
        self.assertIn(team, self.robot.teams.all())

    def test_two_teams_in_one_contest_cannot_share_a_robot(self):
        """
        A single targeted START would command both robots, and the second team's
        run would be untraceable to hardware.
        """
        Team.objects.create(name='Alpha', contest=self.contest, robot=self.robot)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Team.objects.create(name='Beta', contest=self.contest, robot=self.robot)

    def test_the_same_robot_may_enter_two_different_contests(self):
        """One student, one machine, two events — the normal case, must not be blocked."""
        Team.objects.create(name='Alpha', contest=self.contest, robot=self.robot)
        team2 = Team.objects.create(name='Alpha', contest=self.other, robot=self.robot)
        self.assertEqual(team2.robot, self.robot)
        self.assertEqual(self.robot.teams.count(), 2)

    def test_many_teams_may_have_no_robot(self):
        """The constraint is conditional — unbound teams must not collide with each other."""
        Team.objects.create(name='Alpha', contest=self.contest)
        Team.objects.create(name='Beta',  contest=self.contest)
        self.assertEqual(Team.objects.filter(contest=self.contest, robot__isnull=True).count(), 2)

    def test_a_lap_timer_cannot_be_bound_to_a_team(self):
        timer = make_device('BB:00:00:00:00:02', DeviceType.LAPTIMER)
        team  = Team(name='Gamma', contest=self.contest, robot=timer)
        with self.assertRaises(ValidationError):
            team.full_clean()

    def test_releasing_a_robot_clears_its_team_bindings(self):
        """
        A binding that outlived its ownership would still receive targeted START
        commands for a robot the owner no longer controls.
        """
        alice = User.objects.create_user('alice2', password='x')
        self.robot.claim(alice)
        team = Team.objects.create(name='Alpha', contest=self.contest, robot=self.robot)
        self.robot.release()
        team.refresh_from_db()
        self.assertIsNone(team.robot)

    def test_deleting_a_device_leaves_the_team_intact(self):
        """SET_NULL, not CASCADE — losing a robot must never delete a team's runs."""
        team = Team.objects.create(name='Alpha', contest=self.contest, robot=self.robot)
        self.robot.delete()
        team.refresh_from_db()
        self.assertIsNone(team.robot)
        self.assertEqual(team.name, 'Alpha')


class ClaimedDeviceTypeChangeTest(TestCase):
    """Phase 5.21 G — a claimed device cannot change type from `/devices/register/`.

    That endpoint is unauthenticated by design: a device has to be able to
    introduce itself, and `DEVICE_REG_SECRET` is optional. Found by the section G
    read-through and confirmed live before being fixed.

    Every participant view filters on `device_type=ROBOT`, so a claimed robot
    re-registering as a LAPTIMER kept its owner *and* its team binding — still
    receiving targeted START commands — while vanishing from My Robots and
    becoming unreachable by rename, release or test. Recoverable only in Django
    admin. It needs no attacker: flashing lap timer firmware onto the wrong
    board does it.
    """

    def setUp(self):
        self.owner = User.objects.create_user('claim-owner', password='x')
        self.robot = make_device('AA:00:00:00:00:41')
        self.robot.claim(self.owner)

    def _register(self, **payload):
        return self.client.post(
            '/devices/register/',
            data=json.dumps({'mac': self.robot.device_id, **payload}),
            content_type='application/json',
        )

    def test_claimed_robot_keeps_its_type(self):
        response = self._register(friendly_name='Suddenly A Timer',
                                  device_type='LAPTIMER')
        self.assertEqual(response.status_code, 200)

        self.robot.refresh_from_db()
        self.assertEqual(self.robot.device_type, DeviceType.ROBOT)
        self.assertEqual(self.robot.registration_status, RegistrationStatus.ACTIVE)

    def test_claimed_robot_stays_reachable_by_its_owner(self):
        """The consequence that actually bites — this is what stranding meant."""
        self._register(friendly_name='Suddenly A Timer', device_type='LAPTIMER')

        self.assertTrue(
            LapTimerDevice.objects.filter(
                pk=self.robot.pk, owner=self.owner, device_type=DeviceType.ROBOT,
            ).exists(),
            'the owner can no longer reach their own robot',
        )

    def test_other_fields_still_update_on_re_registration(self):
        """The refusal is narrow: only the type is pinned, not the whole record."""
        self._register(friendly_name='Renamed By Firmware', country='SK')

        self.robot.refresh_from_db()
        self.assertEqual(self.robot.friendly_name, 'Renamed By Firmware')
        self.assertEqual(self.robot.country, 'SK')

    def test_an_unclaimed_device_may_still_change_type(self):
        """Nothing is stranded when there is no owner, so the F3 reset stands."""
        spare = make_device('AA:00:00:00:00:42')
        response = self.client.post(
            '/devices/register/',
            data=json.dumps({'mac': spare.device_id, 'friendly_name': 'Now A Timer',
                             'device_type': 'LAPTIMER'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        spare.refresh_from_db()
        self.assertEqual(spare.device_type, DeviceType.LAPTIMER)
        # Becoming a lap timer still lands PENDING — approval cannot be laundered.
        self.assertEqual(spare.registration_status, RegistrationStatus.PENDING)

    def test_releasing_first_allows_the_type_change(self):
        """The documented way out, so the refusal is not a dead end."""
        self.robot.release()
        self._register(friendly_name='Now A Timer', device_type='LAPTIMER')

        self.robot.refresh_from_db()
        self.assertEqual(self.robot.device_type, DeviceType.LAPTIMER)
