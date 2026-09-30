"""Remove everything role_walkthrough_setup.py and role_walkthrough.py created."""
import django, os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'scoreboard.settings')
django.setup()

from django.contrib.auth.models import User

from contest.models import Contest
from devices.models import LapTimerDevice

users = User.objects.filter(username__startswith='walk_')
print(f'users: {list(users.values_list("username", flat=True))}')
users.delete()

contests = Contest.objects.filter(name__startswith='Walk ')
print(f'contests: {list(contests.values_list("name", flat=True))}')
contests.delete()

devices = LapTimerDevice.objects.filter(device_id__startswith='WA:LK:')
print(f'devices: {list(devices.values_list("device_id", flat=True))}')
devices.delete()

print('walkthrough fixtures removed')
