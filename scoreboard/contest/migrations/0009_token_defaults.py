import uuid

from django.db import migrations, models

import contest.models


def backfill_tokens(apps, schema_editor):
    """Give every Team / Competition without a token a unique one (C6)."""
    for model_name in ('Team', 'Competition'):
        model = apps.get_model('contest', model_name)
        blank = model.objects.filter(models.Q(token='') | models.Q(token__isnull=True))
        for row in blank:
            row.token = uuid.uuid4().hex
            row.save(update_fields=['token'])


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0008_add_participant_group'),
    ]

    operations = [
        migrations.AlterField(
            model_name='competition',
            name='token',
            field=models.CharField(blank=True, default=contest.models._new_token,
                                   help_text='Token for run submissions.',
                                   max_length=200, unique=True),
        ),
        migrations.AlterField(
            model_name='team',
            name='token',
            field=models.CharField(blank=True, default=contest.models._new_token,
                                   help_text='Token for run submissions.',
                                   max_length=200, null=True, unique=True),
        ),
        migrations.RunPython(backfill_tokens, migrations.RunPython.noop),
    ]
