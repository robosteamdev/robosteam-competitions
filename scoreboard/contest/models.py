import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from smart_selects.db_fields import ChainedForeignKey


def _new_token():
    """Default value for the unique run-submission tokens on Team and Competition."""
    return uuid.uuid4().hex


def validate_points_table(value):
    """Reject points tables that `scoring.engine._lookup_points` cannot read.

    That lookup does `points_map[str(position)]` and `int(...)` on the result, so
    a table saved with integer keys ({1: 10}) or a non-numeric value silently
    awards everyone the fallback 1 point instead of failing. Catch it at entry.
    """
    if not isinstance(value, dict):
        raise ValidationError('Points table must be a JSON object, e.g. {"1": 10, "2": 8}.')

    for key, points in value.items():
        if key != 'default' and not (isinstance(key, str) and key.isdigit() and int(key) >= 1):
            raise ValidationError(
                'Points table keys must be quoted finishing positions '
                '("1", "2", …) or "default" — got %(key)r.',
                params={'key': key},
            )
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            raise ValidationError(
                'Points for position %(key)s must be a whole number of 0 or more '
                '— got %(points)r.',
                params={'key': key, 'points': points},
            )


class ItemStates(models.TextChoices):
    NEW     = 'NEW',     _('In preparation')
    OPEN    = 'OPEN',    _('Open')
    CLOSED  = 'CLOSED',  _('Closed')
    DELETED = 'DELETED', _('Deleted')


class CompetitionType(models.TextChoices):
    TIMED  = 'TIMED',  _('Timed (hardware lap timer)')
    JUDGED = 'JUDGED', _('Judged (manual score entry)')


class CompetitionState(models.TextChoices):
    WAITING  = 'WAITING',  _('Waiting')
    READY    = 'READY',    _('Ready to start')
    RUNNING  = 'RUNNING',  _('Run in progress')
    FINISHED = 'FINISHED', _('Finished')


class RunState(models.TextChoices):
    PENDING   = 'PENDING',   _('Pending')
    ACTIVE    = 'ACTIVE',    _('In progress')
    COMPLETED = 'COMPLETED', _('Completed')
    VOIDED    = 'VOIDED',    _('Voided (MQTT lost)')
    MANUAL    = 'MANUAL',    _('Manual entry (fallback)')


class Contest(models.Model):
    name        = models.CharField(max_length=200, help_text='Name of the contest.')
    description = models.TextField(default='', blank=True, help_text='Short description, e.g. time and place.')
    status      = models.CharField(max_length=24, choices=ItemStates.choices, default=ItemStates.NEW)
    points_table = models.JSONField(
        default=dict, blank=True,
        validators=[validate_points_table],
        help_text=(
            'Overall Ranking: finishing position to points, as JSON with quoted keys — '
            '{"1": 10, "2": 8, "3": 6, "4": 4, "5": 2, "default": 1}. '
            'Positions not listed score "default", or 1 if there is no "default". '
            'Leave empty for 1 point per position.'
        ),
    )

    class Meta:
        verbose_name        = 'Competition'
        verbose_name_plural = 'Competitions'

    def __str__(self):
        return self.name


class Team(models.Model):
    name        = models.CharField(max_length=200, help_text='Identifier of the team.')
    description = models.TextField(default='', blank=True, help_text='Team members and affiliation.')
    contest     = models.ForeignKey(Contest, on_delete=models.CASCADE, help_text='A team belongs to a contest.')
    token       = models.CharField(unique=True, null=True, max_length=200, default=_new_token, blank=True,
                                   help_text='Token for run submissions.')
    robot       = models.ForeignKey(
        'devices.LapTimerDevice', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='teams',
        help_text='The robot this team is competing with. One robot per team '
                  '(Decision 040); the team maintains this binding itself.',
    )
    robot_assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='robot_overrides',
        help_text='Set only when an organiser or judge overrode the binding. '
                  'Null means the team set it themselves, which is the normal case.',
    )
    robot_assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Two teams in one event must not share a MAC: a single targeted START
            # would command both, and the second team's run would be untraceable.
            # Robots ARE reusable across contests — the same student entering two
            # events gets a Team row in each, both may point at the same device.
            models.UniqueConstraint(
                fields=['contest', 'robot'],
                condition=models.Q(robot__isnull=False),
                name='one_robot_per_team_per_contest',
            ),
        ]

    def clean(self):
        super().clean()
        if self.robot_id is not None:
            from devices.models import DeviceType

            if self.robot.device_type != DeviceType.ROBOT:
                raise ValidationError(
                    {'robot': 'Only a robot can be bound to a team — '
                              f'"{self.robot.friendly_name}" is a lap timer.'}
                )

    def __str__(self):
        return 'Team:{}@{}'.format(self.name, self.contest.name)


class Competition(models.Model):
    name        = models.CharField(max_length=200, help_text='Name of the category.')
    description = models.TextField(default='', blank=True, help_text='Rules of the competition and description of the task.')
    status      = models.CharField(max_length=24, choices=ItemStates.choices, default=ItemStates.NEW)
    contest     = models.ForeignKey(Contest, on_delete=models.CASCADE, help_text='Parent competition event.')
    runs        = models.ManyToManyField(Team, through='Run', related_name='runs',
                                         help_text='Teams that have runs in this category.')
    token       = models.CharField(unique=True, max_length=200, default=_new_token, blank=True,
                                   help_text='Token for run submissions.')
    competition_type = models.CharField(
        max_length=8, choices=CompetitionType.choices, default=CompetitionType.JUDGED,
        help_text='TIMED: scored by hardware lap timer. JUDGED: scored manually by a judge.'
    )
    state = models.CharField(
        max_length=10, choices=CompetitionState.choices, default=CompetitionState.WAITING,
        help_text='Current operational state of this category.'
    )
    num_laps = models.PositiveSmallIntegerField(
        default=1, help_text='Number of laps per run (TIMED only).'
    )
    timeout_seconds = models.PositiveSmallIntegerField(
        default=120, help_text='Run timeout in seconds (TIMED only).'
    )
    num_runs = models.PositiveSmallIntegerField(
        default=3, help_text='Maximum number of runs allowed per team.'
    )
    lap_timer = models.ForeignKey(
        'devices.LapTimerDevice', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='competitions',
        help_text='Lap timer device assigned to time this category (TIMED only).'
    )

    class Meta:
        verbose_name        = 'Category'
        verbose_name_plural = 'Categories'

    def __str__(self):
        return 'Competition:{}@{}'.format(self.name, self.contest.name)


class Result(models.Model):
    score       = models.IntegerField()
    comment     = models.TextField(default='', blank=True, help_text='Comment to the participation.')
    team        = models.ForeignKey(Team, on_delete=models.CASCADE)
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE)

    is_manual = models.BooleanField(default=False, help_text='True if recorded manually via MQTT-fallback path.')

    class Meta:
        unique_together = (('team', 'competition'),)

    def __str__(self):
        return 'Result:{}@{}'.format(self.team.name, self.competition.name)


class Run(models.Model):
    start_time    = models.DateTimeField()
    duration      = models.IntegerField(blank=True,
                                        help_text='Duration in whole seconds (legacy, kept for compatibility).')
    score         = models.IntegerField(blank=True, null=True, help_text='Assigned by a judge (JUDGED runs).')
    judge_comment = models.CharField(max_length=200, blank=True, help_text='Comment by a judge.')
    team          = models.ForeignKey(Team, on_delete=models.CASCADE, help_text='Who performed the run.')
    competition   = models.ForeignKey(Competition, on_delete=models.CASCADE, help_text='Source category.')
    state         = models.CharField(
        max_length=10, choices=RunState.choices, default=RunState.PENDING,
        help_text='Lifecycle state of this run.'
    )
    time_ms = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Elapsed time in milliseconds from hardware LapEvents (TIMED runs).'
    )
    penalty_time_ms = models.PositiveIntegerField(
        default=0, help_text='Penalty time in milliseconds added to time_ms (TIMED runs).'
    )
    penalty_points = models.PositiveSmallIntegerField(
        default=0, help_text='Penalty points subtracted from score (JUDGED runs).'
    )
    is_best = models.BooleanField(
        default=False,
        help_text='True if this is the team\'s best run in this category (denormalised for display speed).'
    )
    is_manual = models.BooleanField(
        default=False,
        help_text='True if the time/score on this run was entered by hand (MQTT-fallback path).'
    )

    def __str__(self):
        return 'Run:{}@{}'.format(self.start_time, self.team.name)

    @property
    def total_time_ms(self):
        """Raw time plus penalty — the value a TIMED run is ranked by (lowest wins)."""
        if self.time_ms is None:
            return None
        return self.time_ms + self.penalty_time_ms

    @property
    def effective_score(self):
        """Judge score minus penalty points, floored at 0 — how a JUDGED run is ranked."""
        if self.score is None:
            return None
        return max(0, self.score - self.penalty_points)


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('judge',       'Judge'),
        ('organiser',   'Organiser'),
        ('participant', 'Participant / Team'),
    ]
    user           = models.OneToOneField(
        'auth.User', on_delete=models.CASCADE, related_name='profile'
    )
    requested_role = models.CharField(max_length=16, choices=ROLE_CHOICES, default='judge')
    organisation   = models.CharField(max_length=200, blank=True)
    country        = models.CharField(max_length=100, blank=True)
    team_name      = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Team name for Participant accounts. Appears in all competition results.',
    )

    def __str__(self):
        return f'Profile({self.user.username})'


class ContestRegistration(models.Model):
    user    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='contest_registrations',
    )
    contest = models.ForeignKey(
        'Contest',
        on_delete=models.CASCADE,
        related_name='registrations',
    )
    team    = models.OneToOneField(
        'Team',
        on_delete=models.CASCADE,
        related_name='registration',
    )
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'contest')
        verbose_name = 'Competition registration'
        verbose_name_plural = 'Competition registrations'

    def __str__(self):
        return f'{self.user.username} → {self.contest.name}'
