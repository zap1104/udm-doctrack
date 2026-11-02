"""Keep raw record inspection within the same restrictive application policy."""
from .capabilities import area_for, capability_allowed, require_capability


class RecordPolicyAdminMixin:
    def get_queryset(self, request):
        from apps.documents.models import Document
        from apps.tracking.models import TrackingRecord

        queryset = super().get_queryset(request)
        name = self.model._meta.model_name
        if name == 'document':
            return queryset.filter(pk__in=Document.objects.visible_to(request.user).values('pk'))
        if name == 'trackingrecord':
            return queryset.filter(pk__in=TrackingRecord.objects.visible_to(request.user).values('pk'))
        if name in {'documentfile', 'documentfileversion'}:
            parents = Document.objects.visible_to(request.user)
            for collection, area in [('OVPA','repository'), ('EXTERNAL','external_repository')]:
                if not capability_allowed(request.user, area, 'view_content'):
                    parents = parents.exclude(collection=collection)
            path = 'document_id__in' if name == 'documentfile' else 'document_file__document_id__in'
            return queryset.filter(**{path: parents.values('pk')})
        if name in {'attachment', 'recordactivity', 'routingstep'}:
            parents = TrackingRecord.objects.visible_to(request.user)
            if name == 'attachment' and not capability_allowed(request.user, 'tracking', 'view_content'):
                return queryset.none()
            return queryset.filter(record_id__in=parents.values('pk'))
        if name == 'externalattachment':
            from apps.tracking.models import ExternalTrackingRecord
            if not capability_allowed(request.user, 'external_tracking', 'view_content'):
                return queryset.none()
            return queryset.filter(record_id__in=ExternalTrackingRecord.objects.visible_to(request.user).values('pk'))
        if name == 'metadatasuggestion':
            parents = Document.objects.visible_to(request.user)
            for collection, area in [('OVPA','repository'), ('EXTERNAL','external_repository')]:
                if not capability_allowed(request.user, area, 'view_content'):
                    parents = parents.exclude(collection=collection)
            return queryset.filter(document_id__in=parents.values('pk'))
        return queryset

    def has_change_permission(self, request, obj=None):
        if obj is not None:
            name = self.model._meta.model_name
            if name in {'document', 'trackingrecord'}:
                if not capability_allowed(request.user, area_for(obj), 'edit_metadata'):
                    return False
            elif name == 'routingstep' and not capability_allowed(request.user, 'tracking', 'endorse'):
                return False
        return super().has_change_permission(request, obj)

    def has_add_permission(self, request):
        name = self.model._meta.model_name
        if name in {'document', 'trackingrecord'}:
            areas = ('repository', 'external_repository') if name == 'document' else ('tracking',)
            if not any(capability_allowed(request.user, area, 'create') for area in areas):
                return False
        elif name == 'routingstep' and not capability_allowed(request.user, 'tracking', 'endorse'):
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        if self.model._meta.model_name == 'routingstep' and not capability_allowed(request.user, 'tracking', 'endorse'):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if self.model._meta.model_name in {'document', 'trackingrecord'}:
            require_capability(request.user, area_for(obj), 'edit_metadata' if change else 'create')
        return super().save_model(request, obj, form, change)

    def get_exclude(self, request, obj=None):
        excluded = list(super().get_exclude(request, obj) or [])
        if obj is not None and self.model._meta.model_name == 'document':
            if not capability_allowed(request.user, area_for(obj), 'view_content'):
                excluded.extend(['ocr_text', 'ocr_notes'])
        return excluded

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and self.model._meta.model_name == 'document':
            fields.append('collection')
        if obj is not None and self.model._meta.model_name == 'trackingrecord':
            if not all(capability_allowed(request.user, 'tracking', action) for action in ('receive','endorse','complete','reopen','file')):
                metadata = {'subject','instructions','remarks','priority','classification','requested_action','document_type','document_types','due_at'}
                fields.extend(field.name for field in self.model._meta.fields if field.name not in metadata)
        return tuple(dict.fromkeys(fields))
