from django.db import migrations


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')

    def perm(app, model, action):
        try:
            ct = ContentType.objects.get(app_label=app, model=model)
            return Permission.objects.get(content_type=ct, codename=f'{action}_{model}')
        except (ContentType.DoesNotExist, Permission.DoesNotExist):
            return None

    # ── Judge group ──────────────────────────────────────────────────────────
    judge_group, _ = Group.objects.get_or_create(name='Judge')
    judge_perms = [
        perm('contest', 'contest', 'view'),
        perm('contest', 'competition', 'view'),
        perm('contest', 'team', 'view'),
        perm('contest', 'run', 'view'),
        perm('contest', 'run', 'add'),
        perm('contest', 'run', 'change'),
        perm('contest', 'result', 'view'),
        perm('contest', 'result', 'change'),
        perm('devices', 'lapttimerdevice', 'view'),
    ]
    judge_group.permissions.set([p for p in judge_perms if p])

    # ── Organiser group ──────────────────────────────────────────────────────
    organiser_group, _ = Group.objects.get_or_create(name='Organiser')
    organiser_perms = list(judge_perms) + [
        perm('contest', 'contest', 'add'),
        perm('contest', 'contest', 'change'),
        perm('contest', 'competition', 'add'),
        perm('contest', 'competition', 'change'),
        perm('contest', 'team', 'add'),
        perm('contest', 'team', 'change'),
        perm('contest', 'team', 'delete'),
        perm('contest', 'result', 'add'),
        perm('contest', 'result', 'delete'),
        perm('public', 'newspost', 'add'),
        perm('public', 'newspost', 'change'),
        perm('public', 'newspost', 'delete'),
        perm('public', 'announcement', 'add'),
        perm('public', 'announcement', 'change'),
        perm('public', 'announcement', 'delete'),
        perm('public', 'staticpage', 'change'),
        perm('public', 'galleryalbum', 'add'),
        perm('public', 'galleryalbum', 'change'),
        perm('public', 'galleryphoto', 'add'),
        perm('public', 'galleryphoto', 'change'),
        perm('public', 'galleryphoto', 'delete'),
    ]
    organiser_group.permissions.set([p for p in organiser_perms if p])

    # ── Administrator group ──────────────────────────────────────────────────
    admin_group, _ = Group.objects.get_or_create(name='Administrator')
    admin_group.permissions.set(Permission.objects.all())


def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['Judge', 'Organiser', 'Administrator']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0004_result_is_manual'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
