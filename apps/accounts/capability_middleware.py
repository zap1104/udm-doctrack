"""Endpoint guards supplement service checks, including legacy GET actions."""
from django.core.exceptions import PermissionDenied

from .capabilities import area_for, require_capability


class CapabilityRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.user.is_authenticated:
            return None
        match = request.resolver_match
        name, namespace = match.url_name, match.namespace
        action = None
        if namespace == 'tracking':
            external = name.startswith('external_')
            short = name.removeprefix('external_')
            area = 'external_tracking' if external else 'tracking'
            action = {'create':'create', 'review':'endorse', 'file_version':'edit_files', 'file_discard':'edit_files',
                'confirm_receipt':'receive', 'bulk_confirm_receipt':'receive', 'add_remark':'work', 'route':'endorse',
                'complete':'complete', 'archive':'file', 'reopen':'reopen', 'grant_access':'share',
                'export':'export', 'correct':'edit_metadata', 'attachment':'download', 'attachment_download':'download'}.get(short)
            if external and short == 'action':
                action = {'receipt':'receive','external-receipt':'receive','external-work':'work','remark':'work',
                    'attachment':'edit_files','route':'endorse','complete':'complete','file':'file','reopen':'reopen','annotation':'work'}.get(view_kwargs.get('action'))
                if action is None:
                    raise PermissionDenied('This action is unavailable.')
        elif namespace == 'documents':
            area = 'external_repository' if name.startswith('external_') else 'repository'
            if 'pk' in view_kwargs and name != 'file_download':
                from apps.documents.models import Document
                parent = Document.objects.filter(pk=view_kwargs['pk']).first()
                if parent is not None:
                    area = area_for(parent)
            action = {'upload':'create','external_upload':'create','review':'edit_metadata','edit':'edit_metadata',
                're_extract':'extract','source_units':'view_content','source_unit':'view_content',
                'tag_predict':'view_content','extraction_status':'view_record','tag_review':'edit_metadata',
                'tag_review_complete':'edit_metadata','training_eligibility':'edit_metadata'}.get(name)
        elif namespace == 'core' and name == 'report_export':
            area = 'tracking'
            action = 'export'
        if action:
            require_capability(request.user, area, action)
        return None
