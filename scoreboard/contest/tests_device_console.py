"""
Phase 5.21 section D — the organiser device console.

Robots are team-owned (Decision 040), so what is tested here is deliberately
narrow: visibility, lap-timer approval without Django admin, and the override
that exists for the two cases a team cannot solve itself.
"""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Team, UserProfile,
)
from devices.models import (
    DeviceStatus, DeviceType, LapTimerDevice, RegistrationStatus,
)


def make_device(mac, device_type=DeviceType.ROBOT, name='Device',
                status=DeviceStatus.ONLINE, approval=RegistrationStatus.ACTIVE):
    return LapTimerDevice.objects.create(
        device_id=mac, friendly_name=name, device_type=device_type,
        status=status, registration_status=approval, last_seen=timezone.now(),
    )


class ConsoleAccessTest(TestCase):
    def setUp(self):
        org_group, _ = Group.objects.get_or_create(name='Organiser')
        self.org = User.objects.create_user('org', password='pw12345!')
        self.org.groups.add(org_group)

        part_group, _ = Group.objects.get_or_create(name='Participant')
        self.part = User.objects.create_user('part', password='pw12345!')
        self.part.groups.add(part_group)
        UserProfile.objects.create(user=self.part, requested_role='participant')

    def test_organiser_can_open_the_console(self):
        self.client.login(username='org', password='pw12345!')
        r = self.client.get(reverse('contest:organise_devices'))
        self.assertEqual(r.status_code, 200)

    def test_a_participant_cannot(self):
        self.client.login(username='part', password='pw12345!')
        r = self.client.get(reverse('contest:organise_devices'))
        self.assertNotEqual(r.status_code, 200)

    def test_anonymous_cannot(self):
        r = self.client.get(reverse('contest:organise_devices'))
        self.assertNotEqual(r.status_code, 200)


class ConsoleListingTest(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name='Organiser')
        self.org = User.objects.create_user('org', password='pw12345!')
        self.org.groups.add(group)
        self.client.login(username='org', password='pw12345!')

        self.robot   = make_device('21:00:00:00:00:01', DeviceType.ROBOT, 'Rover')
        self.pending = make_device('21:00:00:00:00:02', DeviceType.LAPTIMER, 'New Timer',
                                   approval=RegistrationStatus.PENDING)
        self.offline = make_device('21:00:00:00:00:03', DeviceType.ROBOT, 'Dead Bot',
                                   status=DeviceStatus.OFFLINE)

    def test_all_devices_are_listed(self):
        r = self.client.get(reverse('contest:organise_devices'))
        for device in (self.robot, self.pending, self.offline):
            self.assertContains(r, device.device_id)

    def test_pending_filter(self):
        r = self.client.get(reverse('contest:organise_devices'), {'filter': 'pending'})
        self.assertContains(r, self.pending.device_id)
        self.assertNotContains(r, self.robot.device_id)

    def test_offline_filter(self):
        r = self.client.get(reverse('contest:organise_devices'), {'filter': 'offline'})
        self.assertContains(r, self.offline.device_id)
        self.assertNotContains(r, self.robot.device_id)

    def test_timers_filter(self):
        r = self.client.get(reverse('contest:organise_devices'), {'filter': 'timers'})
        self.assertContains(r, self.pending.device_id)
        self.assertNotContains(r, self.robot.device_id)


class ApprovalTest(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name='Organiser')
        self.org = User.objects.create_user('org', password='pw12345!')
        self.org.groups.add(group)
        self.client.login(username='org', password='pw12345!')
        self.timer = make_device('22:00:00:00:00:01', DeviceType.LAPTIMER, 'Timer',
                                 approval=RegistrationStatus.PENDING)

    def test_an_organiser_can_approve_a_timer_without_django_admin(self):
        self.client.post(
            reverse('contest:organise_device_approval', args=[self.timer.id]),
            {'action': 'approve'})
        self.timer.refresh_from_db()
        self.assertEqual(self.timer.registration_status, RegistrationStatus.ACTIVE)

    def test_an_organiser_can_deactivate(self):
        self.timer.registration_status = RegistrationStatus.ACTIVE
        self.timer.save()
        self.client.post(
            reverse('contest:organise_device_approval', args=[self.timer.id]),
            {'action': 'deactivate'})
        self.timer.refresh_from_db()
        self.assertEqual(self.timer.registration_status, RegistrationStatus.INACTIVE)

    def test_an_unknown_action_changes_nothing(self):
        self.client.post(
            reverse('contest:organise_device_approval', args=[self.timer.id]),
            {'action': 'promote'})
        self.timer.refresh_from_db()
        self.assertEqual(self.timer.registration_status, RegistrationStatus.PENDING)

    def test_get_changes_nothing(self):
        self.client.get(reverse('contest:organise_device_approval', args=[self.timer.id]))
        self.timer.refresh_from_db()
        self.assertEqual(self.timer.registration_status, RegistrationStatus.PENDING)


class OverrideTest(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name='Organiser')
        self.org = User.objects.create_user('org', password='pw12345!')
        self.org.groups.add(group)
        self.client.login(username='org', password='pw12345!')

        self.contest = Contest.objects.create(name='Cup', status=ItemStates.OPEN)
        self.team    = Team.objects.create(name='Alpha', contest=self.contest)
        self.robot   = make_device('23:00:00:00:00:01', DeviceType.ROBOT, 'Spare')

    def test_override_binds_and_records_who_did_it(self):
        """
        The record is the point: a binding a team did not choose has to be
        distinguishable from one they did.
        """
        self.client.post(reverse('contest:organise_team_robot', args=[self.team.id]),
                         {'robot': self.robot.id})
        self.team.refresh_from_db()
        self.assertEqual(self.team.robot, self.robot)
        self.assertEqual(self.team.robot_assigned_by, self.org)
        self.assertIsNotNone(self.team.robot_assigned_at)

    def test_override_can_clear_the_robot(self):
        self.team.robot = self.robot
        self.team.save()
        self.client.post(reverse('contest:organise_team_robot', args=[self.team.id]),
                         {'robot': ''})
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)

    def test_a_lap_timer_cannot_be_assigned_as_a_robot(self):
        timer = make_device('23:00:00:00:00:02', DeviceType.LAPTIMER, 'Timer')
        r = self.client.post(reverse('contest:organise_team_robot', args=[self.team.id]),
                             {'robot': timer.id})
        self.assertEqual(r.status_code, 404)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)

    def test_a_clash_in_the_same_contest_is_refused_gracefully(self):
        Team.objects.create(name='Beta', contest=self.contest, robot=self.robot)
        r = self.client.post(reverse('contest:organise_team_robot', args=[self.team.id]),
                             {'robot': self.robot.id})
        self.assertEqual(r.status_code, 302)
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)

    def test_a_participant_cannot_override(self):
        part_group, _ = Group.objects.get_or_create(name='Participant')
        part = User.objects.create_user('part', password='pw12345!')
        part.groups.add(part_group)
        UserProfile.objects.create(user=part, requested_role='participant')
        self.client.login(username='part', password='pw12345!')

        self.client.post(reverse('contest:organise_team_robot', args=[self.team.id]),
                         {'robot': self.robot.id})
        self.team.refresh_from_db()
        self.assertIsNone(self.team.robot)


class ReadinessTest(TestCase):
    def setUp(self):
        group, _ = Group.objects.get_or_create(name='Organiser')
        self.org = User.objects.create_user('org', password='pw12345!')
        self.org.groups.add(group)
        self.client.login(username='org', password='pw12345!')

        self.contest = Contest.objects.create(name='Cup', status=ItemStates.OPEN)
        self.timer   = make_device('24:00:00:00:00:01', DeviceType.LAPTIMER, 'Track Timer')
        self.robot   = make_device('24:00:00:00:00:02', DeviceType.ROBOT, 'Rover')
        self.category = Competition.objects.create(
            name='Line Following', contest=self.contest,
            competition_type=CompetitionType.TIMED, status=ItemStates.OPEN,
            lap_timer=self.timer)
        self.team = Team.objects.create(name='Alpha', contest=self.contest,
                                        robot=self.robot)

    def _get(self):
        return self.client.get(reverse('contest:organise_readiness', args=[self.contest.id]))

    def test_everything_healthy_reports_ready(self):
        self.assertTrue(self._get().context['ready'])

    def test_an_offline_timer_is_reported(self):
        LapTimerDevice.objects.filter(pk=self.timer.pk).update(status=DeviceStatus.OFFLINE)
        r = self._get()
        self.assertFalse(r.context['ready'])
        self.assertIn('offline', ' '.join(r.context['rows'][0]['problems']).lower())

    def test_an_unapproved_timer_is_reported(self):
        LapTimerDevice.objects.filter(pk=self.timer.pk).update(
            registration_status=RegistrationStatus.PENDING)
        r = self._get()
        self.assertFalse(r.context['ready'])
        self.assertIn('score nothing', ' '.join(r.context['rows'][0]['problems']))

    def test_a_timed_category_with_no_timer_is_reported(self):
        Competition.objects.filter(pk=self.category.pk).update(lap_timer=None)
        r = self._get()
        self.assertFalse(r.context['ready'])
        self.assertIn('No lap timer', ' '.join(r.context['rows'][0]['problems']))

    def test_a_judged_category_needs_no_timer(self):
        Competition.objects.filter(pk=self.category.pk).update(
            competition_type=CompetitionType.JUDGED, lap_timer=None)
        self.assertTrue(self._get().context['ready'])

    def test_a_team_with_no_robot_is_reported(self):
        Team.objects.filter(pk=self.team.pk).update(robot=None)
        r = self._get()
        self.assertFalse(r.context['ready'])
        self.assertEqual(r.context['team_rows'][0]['problem'], 'No robot bound')

    def test_a_team_with_an_offline_robot_is_reported(self):
        LapTimerDevice.objects.filter(pk=self.robot.pk).update(status=DeviceStatus.OFFLINE)
        r = self._get()
        self.assertFalse(r.context['ready'])
        self.assertEqual(r.context['team_rows'][0]['problem'], 'Robot offline')
