"""Private, purpose-bound challenges. Authentication secrets are never stored raw."""
import uuid

from django.conf import settings
from django.db import models


class EmailChallenge(models.Model):
    class Purpose(models.TextChoices):
        LOGIN = 'login', 'Sign in'
        REAUTH = 'reauth', 'Confirm identity'
        ENROLL = 'enroll', 'Verify registered email'
        CHANGE = 'change', 'Verify replacement email'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_challenges')
    purpose = models.CharField(max_length=12, choices=Purpose.choices)
    binding = models.CharField(max_length=64, blank=True)
    account_stamp = models.CharField(max_length=64)
    destination = models.EmailField()
    digest = models.CharField(max_length=64, blank=True)
    generation = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    pending_until = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    delivered = models.BooleanField(default=False)
    consumed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    return_url = models.CharField(max_length=2048, default='/')
    backend = models.CharField(max_length=180, default='django.contrib.auth.backends.ModelBackend')

    class Meta:
        indexes = [models.Index(fields=['user', 'purpose', 'created_at'])]

    def __str__(self):
        return f'{self.get_purpose_display()} · account {self.user_id}'
