from django.db.models.signals import post_save
from django.dispatch import receiver

from devices.models import LapEvent


@receiver(post_save, sender=LapEvent)
def on_lap_event_saved(sender, instance, created, **kwargs):
    if not created:
        return
    from scoring.engine import assign_lap_event_to_active_run, try_finalize_run
    run = assign_lap_event_to_active_run(instance)
    if run:
        try_finalize_run(run)
