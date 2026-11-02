"""Restrictions supplement role/object rules; absent rows preserve existing behavior."""
from django.conf import settings
from django.db import models


class UserPermissionPolicy(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='permission_policy')
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    reason = models.CharField(max_length=2000, blank=True)

    def __str__(self):
        return f'Permissions for account {self.user_id} · revision {self.revision}'


class CapabilityRestriction(models.Model):
    policy = models.ForeignKey(UserPermissionPolicy, on_delete=models.CASCADE, related_name='restrictions')
    area = models.CharField(max_length=24)
    action = models.CharField(max_length=24)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['policy', 'area', 'action'], name='unique_user_capability_restriction')]

    def __str__(self):
        return f'{self.area} · {self.action} blocked'
