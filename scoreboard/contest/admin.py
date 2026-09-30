from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User, Group
from django.forms import TextInput, Textarea, NumberInput
import django.db

from devices.models import validate_device_approved

from .models import ContestRegistration, Run, Competition, Contest, Result, Team, UserProfile


class Admin(admin.ModelAdmin):
    formfield_overrides = {
        django.db.models.TextField: {'widget': Textarea(attrs={'rows': 2, 'cols': 40})},
    }


class Inline(admin.TabularInline):
    show_change_link = True
    formfield_overrides = {
        django.db.models.TextField:   {'widget': TextInput(attrs={'style': 'width: 10em'})},
        django.db.models.CharField:   {'widget': TextInput(attrs={'style': 'width: 10em'})},
        django.db.models.FloatField:  {'widget': NumberInput(attrs={'style': 'width: 3em'})},
        django.db.models.IntegerField:{'widget': NumberInput(attrs={'style': 'width: 3em'})},
    }
    extra = 1


class ResultInline(Inline):
    model = Result


class RunInline(Inline):
    model = Run
    fields = ('team', 'start_time', 'duration', 'score', 'judge_comment', 'state', 'time_ms', 'penalty_time_ms', 'is_best')


class TeamInline(Inline):
    model = Team


class CompetitionAdminForm(forms.ModelForm):
    class Meta:
        model  = Competition
        fields = '__all__'

    def clean_lap_timer(self):
        return validate_device_approved(self.cleaned_data.get('lap_timer'))


class CompetitionAdmin(Admin):
    form          = CompetitionAdminForm
    list_display  = ('name', 'contest', 'competition_type', 'state', 'status', 'num_runs', 'num_laps', 'timeout_seconds', 'lap_timer')
    list_filter   = ('competition_type', 'state', 'status')
    fields        = ('name', 'description', 'contest', 'competition_type', 'state', 'status',
                     'num_runs', 'num_laps', 'timeout_seconds', 'lap_timer', 'token')
    inlines       = [ResultInline, RunInline]


class ContestAdmin(Admin):
    list_display = ('name', 'status')
    fields       = ('name', 'description', 'status', 'points_table')
    inlines      = [TeamInline]


# ── User management with approval workflow ────────────────────────────────────

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'
    extra = 0


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display  = ('username', 'email', 'first_name', 'last_name', 'is_active',
                     'get_requested_role', 'get_groups')
    list_filter   = ('is_active', 'groups')
    actions       = ['approve_users']

    def get_requested_role(self, obj):
        try:
            return obj.profile.requested_role
        except UserProfile.DoesNotExist:
            return '—'
    get_requested_role.short_description = 'Requested role'

    def get_groups(self, obj):
        return ', '.join(obj.groups.values_list('name', flat=True)) or '—'
    get_groups.short_description = 'Groups'

    @admin.action(description='Approve selected users (activate + assign to requested group)')
    def approve_users(self, request, queryset):
        approved = 0
        for user in queryset:
            user.is_active = True
            user.save(update_fields=['is_active'])
            try:
                role  = user.profile.requested_role
                group = Group.objects.get(name=role.capitalize())
                user.groups.add(group)
            except (UserProfile.DoesNotExist, Group.DoesNotExist):
                pass
            approved += 1
        self.message_user(request, f'{approved} user(s) approved and activated.')


@admin.register(ContestRegistration)
class ContestRegistrationAdmin(admin.ModelAdmin):
    list_display  = ('user', 'contest', 'team', 'registered_at')
    list_filter   = ('contest',)
    raw_id_fields = ('user', 'contest', 'team')


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
# UserProfile is edited through UserProfileInline on the user page — a second
# standalone entry only offers a way to create an orphan profile.
admin.site.register(Contest, ContestAdmin)
admin.site.register(Competition, CompetitionAdmin)
