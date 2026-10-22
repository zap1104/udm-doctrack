from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class SavedDocumentView(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_document_views")
    name = models.CharField(max_length=80)
    collection = models.CharField(max_length=8)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [models.UniqueConstraint(Lower("name"), "owner", name="unique_saved_view_owner_name")]

    def __str__(self):
        return self.name
