"""Create throwaway accounts and fixtures for the Phase H role walkthrough.

Everything created here is prefixed `walk` so walk_teardown.py can remove it.
"""
import django, os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'scoreboard.settings')
django.setup()

from django.contrib.auth.models import Group, User

from contest.models import (
    Competition, CompetitionType, Contest, ItemStates, Team,
)
from devices.models import DeviceType, LapTimerDevice, RegistrationStatus

PASSWORD = 'walkthrough-pw-2026'


def make_user(username, group=None, superuser=False):
    User.objects.filter(username=username).delete()
    user = User.objects.create_user(username=username, password=PASSWORD)
    if superuser:
        user.is_superuser = user.is_staff = True
        user.save()
    if group:
        grp, _ = Group.objects.get_or_create(name=group)
        user.groups.add(grp)
    return user


make_user('walk_judge', 'Judge')
make_user('walk_org', 'Organiser')
make_user('walk_admin', superuser=True)

# A pending user for the admin to approve.
pending = make_user('walk_pending')
pending.is_active = False
pending.save()
from contest.models import UserProfile
UserProfile.objects.update_or_create(user=pending, defaults={'requested_role': 'judge'})

# A contest a participant can join, and a TIMED category the judge can run.
Contest.objects.filter(name__startswith='Walk ').delete()
contest = Contest.objects.create(name='Walk Cup', status=ItemStates.OPEN)
device = LapTimerDevice.objects.filter(device_id='AA:BB:CC:DD:EE:01').first()
timed = Competition.objects.create(
    name='Walk Timed', contest=contest, competition_type=CompetitionType.TIMED,
    status=ItemStates.OPEN, num_laps=1, num_runs=9, timeout_seconds=60,
    lap_timer=device,
)
judged = Competition.objects.create(
    name='Walk Judged', contest=contest, competition_type=CompetitionType.JUDGED,
    status=ItemStates.OPEN, num_laps=1, num_runs=9,
)
team = Team.objects.create(name='Walk Team', contest=contest)

# A device the admin can approve.
LapTimerDevice.objects.filter(device_id='WA:LK:00:00:00:01').delete()
LapTimerDevice.objects.create(
    device_id='WA:LK:00:00:00:01', friendly_name='Walk Timer',
    device_type=DeviceType.LAPTIMER, registration_status=RegistrationStatus.PENDING,
)

print(f'contest={contest.id} timed={timed.id} judged={judged.id} team={team.id}')
