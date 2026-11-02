from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from apps.core.forms import BootstrapFormMixin

from . import otp


class CodeForm(BootstrapFormMixin, forms.Form):
    code = forms.CharField(label='Email verification code', max_length=12,
        widget=forms.TextInput(attrs={'inputmode': 'numeric', 'autocomplete': 'one-time-code',
                                     'autofocus': True, 'placeholder': '6-digit code'}))


@method_decorator(sensitive_post_parameters('code'), name='dispatch')
@method_decorator(never_cache, name='dispatch')
class EmailCodeView(View):
    def get(self, request):
        challenge = otp.pending(request)
        if challenge is None:
            return redirect('accounts:login')
        return self.page(request, CodeForm(), challenge)

    @staticmethod
    def page(request, form, challenge):
        return render(request, 'accounts/otp.html', {'form': form,
            'destination': otp.masked(challenge.destination) if challenge else '',
            'is_reauth': challenge and challenge.purpose == 'reauth'})

    def post(self, request):
        form = CodeForm(request.POST)
        challenge = otp.pending(request)
        if form.is_valid():
            try:
                return redirect(otp.verify(request, form.cleaned_data['code']))
            except ValidationError as exc:
                form.add_error(None, exc)
        return self.page(request, form, challenge)


class EmailCodeResendView(View):
    def post(self, request):
        try:
            otp.resend(request)
            messages.success(request, 'A new code was sent. Use the latest email.')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
        return redirect('accounts:otp')


class EmailCodeCancelView(View):
    def post(self, request):
        otp.cancel(request)
        return redirect('accounts:profile' if request.user.is_authenticated else 'accounts:login')


@method_decorator(login_required, name='dispatch')
class ReauthenticateView(View):
    def get(self, request):
        return render(request, 'accounts/reauth.html', {'next': otp.safe_return(request, request.GET.get('next'))})

    def post(self, request):
        try:
            otp.begin(request, request.user, return_url=request.POST.get('next'), purpose='reauth')
        except ValidationError as exc:
            messages.error(request, ' '.join(exc.messages))
            return self.get(request)
        return redirect('accounts:otp')


class EmailInvitationView(View):
    def get(self, request, pk, token):
        response = render(request, 'accounts/email_invitation.html', {'validlink': otp.invitation_valid(pk, token)})
        response['Cache-Control'] = 'private, no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response

    def post(self, request, pk, token):
        valid = otp.invitation_valid(pk, token, consume=True)
        response = render(request, 'accounts/email_invitation.html', {'validlink': False, 'confirmed': valid}, status=200 if valid else 400)
        response['Cache-Control'] = 'private, no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class EmailChangeForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label='Replacement email address')
    password = forms.CharField(label='Current password', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))


@method_decorator(login_required, name='dispatch')
@method_decorator(sensitive_post_parameters('password'), name='dispatch')
class EmailChangeView(View):
    def get(self, request):
        if not otp.verified(request, recent=True):
            return redirect('/accounts/reauthenticate/?next=/accounts/email/change/')
        return render(request, 'accounts/email_change.html', {'form': EmailChangeForm()})

    def post(self, request):
        from .email_security import request_email_change

        if not otp.verified(request, recent=True):
            return redirect('/accounts/reauthenticate/?next=/accounts/email/change/')
        form = EmailChangeForm(request.POST)
        if form.is_valid():
            try:
                request_email_change(request, **form.cleaned_data)
                messages.success(request, 'Verify the replacement address using the email invitation. Your current address stays active until then.')
                return redirect('accounts:profile')
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(request, 'accounts/email_change.html', {'form': form})


class RecoveryForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label='Registered email address')
    reason = forms.CharField(label='Identity check / recovery reason', max_length=2000, widget=forms.Textarea(attrs={'rows': 3}))


class AccountEmailSecurityView(View):
    def dispatch(self, request, pk, *args, **kwargs):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import get_object_or_404

        from .models import User

        if not request.user.is_authenticated:
            return redirect('accounts:login')
        self.target = get_object_or_404(User.objects.administrable_by(request.user), pk=pk)
        if not request.user.is_office_admin:
            raise PermissionDenied
        if not otp.verified(request, recent=True):
            return redirect('/accounts/reauthenticate/?next=' + request.path)
        return super().dispatch(request, *args, pk=pk, **kwargs)

    def get(self, request, pk):
        return render(request, 'accounts/email_security.html', {'target': self.target, 'form': RecoveryForm(initial={'email': self.target.email})})

    def post(self, request, pk):
        from .email_security import recover_email

        if request.POST.get('action') == 'invite':
            try:
                otp.invitation(self.target, actor=request.user, request=request, reason='Administrator sent enrollment invitation')
                messages.success(request, 'Verification invitation sent. The account remains unverified until its owner confirms.')
            except ValidationError as exc:
                messages.error(request, ' '.join(exc.messages))
            return redirect(request.path)
        form = RecoveryForm(request.POST)
        if form.is_valid():
            try:
                recover_email(self.target, actor=request.user, request=request, **form.cleaned_data)
                messages.success(request, 'Replacement email saved. The owner must verify it before signing in.')
                return redirect(request.path)
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(request, 'accounts/email_security.html', {'target': self.target, 'form': form})
