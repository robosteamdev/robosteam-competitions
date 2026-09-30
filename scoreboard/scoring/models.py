from django.db import models


class OverallResult(models.Model):
    """Aggregated ranking for a team across all categories in a contest."""
    contest = models.ForeignKey('contest.Contest', on_delete=models.CASCADE)
    team = models.ForeignKey('contest.Team', on_delete=models.CASCADE)
    total_points = models.IntegerField(default=0)
    rank = models.IntegerField(null=True, blank=True)
    is_eligible = models.BooleanField(default=False)

    class Meta:
        unique_together = ('contest', 'team')

    def __str__(self):
        return f'OverallResult:{self.team.name}@{self.contest.name} rank={self.rank}'
