from django.db import migrations


def create_participant_group(apps, schema_editor):
    Group      = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    participant, _ = Group.objects.get_or_create(name='Participant')
    view_perms = Permission.objects.filter(
        codename__in=[
            'view_contest', 'view_competition',
            'view_result',  'view_team', 'view_run',
        ]
    )
    participant.permissions.set(view_perms)


def remove_participant_group(apps, schema_editor):
    apps.get_model('auth', 'Group').objects.filter(name='Participant').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('contest', '0007_participant_and_registration'),
    ]
    operations = [
        migrations.RunPython(create_participant_group, remove_participant_group),
    ]
