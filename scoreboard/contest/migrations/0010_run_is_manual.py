from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contest', '0009_token_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='run',
            name='is_manual',
            field=models.BooleanField(
                default=False,
                help_text='True if the time/score on this run was entered by hand '
                          '(MQTT-fallback path).',
            ),
        ),
    ]
