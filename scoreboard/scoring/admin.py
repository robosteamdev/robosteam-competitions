from django.contrib import admin

from scoring.models import OverallResult


@admin.register(OverallResult)
class OverallResultAdmin(admin.ModelAdmin):
    list_display = ('contest', 'team', 'rank', 'total_points', 'is_eligible')
    list_filter = ('contest', 'is_eligible')
    ordering = ('contest', 'rank')
    readonly_fields = ('contest', 'team', 'total_points', 'rank', 'is_eligible')
