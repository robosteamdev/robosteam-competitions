"""Shared helper for the scenario drivers: a test Client logged in as a judge.

Since Phase A every run/judge endpoint is behind `judge_required`, so a driver
using an anonymous Client silently gets a 302 to the login page instead of
performing the action. Import `judge_client()` and use it for anything that
POSTs to /contest/... endpoints.
"""
from django.contrib.auth.models import Group, User
from django.test import Client

SCENARIO_JUDGE = 'scenario-judge'


def judge_user():
    """Get-or-create the throwaway judge account the scenario drivers use."""
    user, created = User.objects.get_or_create(
        username=SCENARIO_JUDGE, defaults={'is_active': True},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=['password'])
    group, _ = Group.objects.get_or_create(name='Judge')
    user.groups.add(group)
    return user


def judge_client():
    """A Django test Client authenticated as a Judge-group user."""
    client = Client(enforce_csrf_checks=False)
    client.force_login(judge_user())
    return client
