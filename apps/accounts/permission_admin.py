"""Human-readable restrictions and audited record shares in the existing admin site."""
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse

from . import otp
from .capabilities import ACTION_LABELS, APPLICABLE, AREAS, capability_allowed, role_default, set_restrictions
from .models import Office, PermissionUser, User
from .permission_models import UserPermissionPolicy
from .sharing import grant_model, grant_read_access, revoke_read_access


def security_access(request):
    current = User.objects.filter(pk=request.user.pk, is_active=True, role='SYSTEM_ADMIN', is_staff=True).first()
    if current is None:
        raise PermissionDenied('Only system administrators may manage these security settings.')
    request.user = current
    if not otp.verified(request, recent=True):
        return redirect(reverse('accounts:reauthenticate') + '?' + urlencode({'next': request.get_full_path()}))
    return None


class PermissionMatrixForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    reason = forms.CharField(max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, target, **kwargs):
        super().__init__(*args, **kwargs)
        self.target = target
        policy = UserPermissionPolicy.objects.filter(user=target).first()
        self.initial['revision'] = policy.revision if policy else 0
        restricted = set(policy.restrictions.values_list('area', 'action')) if policy else set()
        self.sections = []
        for area, title in AREAS.items():
            rows = []
            for action, label in ACTION_LABELS.items():
                if action not in APPLICABLE[area]:
                    continue
                name = f'{area}__{action}'
                self.fields[name] = forms.ChoiceField(label=label, choices=[('default', 'Use role default'), ('block', 'Block')],
                    initial='block' if (area, action) in restricted else 'default')
                rows.append({'field': self[name], 'default': 'Eligible within existing rules' if role_default(target, area, action) else 'Not allowed by role',
                    'effective': 'Eligible within existing rules' if capability_allowed(target, area, action) else 'Blocked or unavailable'})
            self.sections.append({'title': title, 'rows': rows})

    def restrictions(self):
        return [tuple(name.split('__', 1)) for name, value in self.cleaned_data.items() if '__' in name and value == 'block']


@admin.register(PermissionUser)
class UserPermissionAdmin(admin.ModelAdmin):
    list_display = ('username', 'office', 'role', 'email_ready', 'restriction_count', 'email_security')
    list_filter = ('role', 'office', 'is_active')
    search_fields = ('username', 'first_name', 'last_name', 'email')
    ordering = ('username',)
    actions = None

    @admin.display(boolean=True, description='Email verified')
    def email_ready(self, obj):
        return obj.email_is_verified

    @admin.display(description='Restrictions')
    def restriction_count(self, obj):
        from .permission_models import CapabilityRestriction
        return CapabilityRestriction.objects.filter(policy__user_id=obj.pk).count()

    @admin.display(description='Email setup / recovery')
    def email_security(self, obj):
        from django.utils.html import format_html
        return format_html('<a href="{}">Email security</a>', reverse('accounts:email_security', args=[obj.pk]))

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_system_admin

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        refused = security_access(request)
        if refused:
            return refused
        if not str(object_id).isascii() or not str(object_id).isdigit():
            raise Http404
        target = get_object_or_404(User, pk=int(object_id))
        form = PermissionMatrixForm(request.POST if request.method == 'POST' else None, target=target)
        if request.method == 'POST' and form.is_valid():
            try:
                set_restrictions(target, form.restrictions(), actor=request.user, request=request,
                    expected_revision=form.cleaned_data['revision'], reason=form.cleaned_data['reason'])
                messages.success(request, 'Permissions saved. Existing role, office and custody limits still apply.')
                return redirect(request.path)
            except ValidationError as exc:
                form.add_error(None, exc)
        return TemplateResponse(request, 'admin/accounts/permission_matrix.html', {
            **self.admin_site.each_context(request), 'title': f'Permissions: {target.username}',
            'target': target, 'form': form, 'opts': self.model._meta, 'original': target})


class ShareForm(forms.Form):
    office = forms.ModelChoiceField(queryset=Office.active.all(), required=False)
    user = forms.ModelChoiceField(queryset=User.objects.filter(is_active=True), required=False)
    reason = forms.CharField(max_length=255)

    def clean(self):
        data = super().clean()
        if (data.get('office') is None) == (data.get('user') is None):
            raise ValidationError('Choose one internal office or one user, not both.')
        return data


def record_sharing(request, admin_site):
    from apps.documents.models import Document
    from apps.tracking.models import ExternalTrackingRecord, TrackingRecord

    refused = security_access(request)
    if refused:
        return refused
    data = request.POST if request.method == 'POST' else request.GET
    if any(len(data.getlist(key)) > 1 for key in ('kind', 'record', 'q')):
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('Choose one record-sharing view at a time.')
    kind = data.get('kind', 'tracking')
    if kind not in AREAS:
        raise Http404
    external = kind.startswith('external_')
    repository = 'repository' in kind
    model = Document if repository else ExternalTrackingRecord if external else TrackingRecord
    records = model.objects.visible_to(request.user)
    if repository:
        records = records.filter(collection='EXTERNAL' if external else 'OVPA', is_active=True)
    query = data.get('q', '').strip()[:255]
    if query:
        from django.db.models import Q
        fields = Q(title__icontains=query) | Q(reference_number__icontains=query) if repository else (
            Q(title__icontains=query) | Q(tracking_number__icontains=query) if external else Q(subject__icontains=query) | Q(tracking_number__icontains=query))
        records = records.filter(fields)
    selected = data.get('record', '')
    parent = None
    if selected:
        if not selected.isascii() or not selected.isdigit() or len(selected) > 18:
            raise Http404
        parent = get_object_or_404(records, pk=int(selected))
    form = ShareForm(request.POST if request.method == 'POST' and data.get('action') == 'grant' else None)
    error = ''
    if request.method == 'POST':
        if parent is None:
            raise Http404
        try:
            if data.get('action') == 'grant' and form.is_valid():
                grant_read_access(parent, user=request.user, office=form.cleaned_data['office'],
                    target_user=form.cleaned_data['user'], reason=form.cleaned_data['reason'])
            elif data.get('action') == 'revoke':
                grant_id = data.get('grant', '')
                if not grant_id.isascii() or not grant_id.isdigit() or len(grant_id) > 18:
                    raise ValidationError('Choose a valid explicit share.')
                revoke_read_access(parent, int(grant_id), user=request.user, reason=data.get('reason'))
            else:
                raise ValidationError('Choose an action and complete the sharing form.')
            messages.success(request, 'Explicit sharing updated. Inherited access is unchanged.')
            return redirect(request.path + '?' + urlencode({'kind': kind, 'record': parent.pk}))
        except ValidationError as exc:
            error = ' '.join(exc.messages)
    grants = []
    inherited = []
    if parent:
        key = 'document' if repository else 'record'
        grants = grant_model(parent).objects.filter(**{key: parent}).select_related('office', 'user', 'granted_by')
        inherited.append(('System administrators', 'Existing administrative read authority; account restrictions still apply.'))
        if repository:
            inherited.extend([('Filing office', str(parent.office)), ('Original uploader', str(parent.uploaded_by)),
                ('Visibility', parent.get_access_level_display())])
            history = parent.tracking_origin
        else:
            inherited.append(('Recorded by', str(parent.created_by)))
            office = parent.managing_office if external else parent.originating_office
            inherited.append(('Recording office' if external else 'Originating office', str(office)))
            history = parent
        if history is not None:
            offices = set()
            for step in history.routing_steps.select_related('from_office', 'to_office'):
                offices.update(str(office) for office in (step.from_office, step.to_office) if office is not None)
            if offices:
                inherited.append(('Internal routing participants', '; '.join(sorted(offices))))
    return TemplateResponse(request, 'admin/accounts/record_sharing.html', {
        **admin_site.each_context(request), 'title': 'Record sharing', 'areas': AREAS.items(),
        'kind': kind, 'query': query, 'records': records.order_by('-pk')[:50], 'parent': parent,
        'grants': grants, 'inherited': inherited, 'form': form, 'error': error, 'capped': records.count() > 50})


class ReadOnlyShareAdmin(admin.ModelAdmin):
    """Legacy raw grant URLs remain inspectable; writes use the audited table."""
    actions = None

    def get_queryset(self, request):
        from apps.documents.models import Document
        from apps.tracking.models import ExternalTrackingRecord, TrackingRecord

        queryset = super().get_queryset(request)
        parent = self.model._meta.get_field('document' if self.model._meta.model_name == 'documentaccessgrant' else 'record').related_model
        if parent is Document:
            return queryset.filter(document_id__in=Document.objects.visible_to(request.user).values('pk'))
        model = ExternalTrackingRecord if parent is ExternalTrackingRecord else TrackingRecord
        return queryset.filter(record_id__in=model.objects.visible_to(request.user).values('pk'))

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
