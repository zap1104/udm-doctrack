from django.db import models


class TagApplicationIntent(models.Model):
    suggestion = models.OneToOneField(
        "documents.MetadataSuggestion", on_delete=models.CASCADE, related_name="application_intent"
    )
    state = models.CharField(max_length=24, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    lease_token = models.CharField(max_length=32, blank=True)
    lease_expires_at = models.DateTimeField(null=True)
    next_attempt_at = models.DateTimeField(null=True)
    last_reason = models.CharField(max_length=128, blank=True)
    result = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Automatic topics for suggestion {self.suggestion_id}: {self.state}"
