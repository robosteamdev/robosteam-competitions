from django.contrib import admin

from .models import LapEvent, LapTimerDevice, RegistrationStatus


@admin.action(description='Approve selected devices')
def approve_devices(modeladmin, request, queryset):
    queryset.update(registration_status=RegistrationStatus.ACTIVE)


@admin.action(description='Deactivate selected devices')
def deactivate_devices(modeladmin, request, queryset):
    queryset.update(registration_status=RegistrationStatus.INACTIVE)


@admin.register(LapTimerDevice)
class LapTimerDeviceAdmin(admin.ModelAdmin):
    list_display  = (
        'friendly_name', 'device_id', 'device_type',
        'registration_status', 'status', 'last_seen', 'country', 'organisation',
    )
    list_filter   = ('device_type', 'registration_status', 'status')
    search_fields = ('friendly_name', 'device_id', 'organisation')
    actions       = [approve_devices, deactivate_devices]


@admin.register(LapEvent)
class LapEventAdmin(admin.ModelAdmin):
    list_display = ('device', 'competition', 'run', 'timestamp_utc', 'sequence')
    list_filter  = ('device', 'competition')
