from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm, UserCreationForm

from apps.core.forms import BootstrapFormMixin, ColourInput
from apps.core.models import NotificationPreference

from .models import Office, User


class SignInForm(BootstrapFormMixin, AuthenticationForm):
    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={
                               "autofocus": True, "placeholder": "Enter username", "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"placeholder": "Enter password", "autocomplete": "current-password"}),
    )

    error_messages = {
        "invalid_login": "That username and password do not match an account. Check both and try again.",
        "inactive": "This account is deactivated. Ask the system administrator to reactivate it.",
    }


class AdminUserCreateForm(BootstrapFormMixin, UserCreationForm):
    """Accounts are created here — there is no public registration page."""

    first_name = forms.CharField(max_length=60)
    last_name = forms.CharField(max_length=60)
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name",
                  "email", "office", "role", "position", "phone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["office"].queryset = Office.active.all()
        self.fields["office"].required = True
        self.fields["must_change_password"] = forms.BooleanField(
            required=False, initial=True, label="Ask the user to change this password at first sign-in"
        )
        self.fields["must_change_password"].widget.attrs["class"] = "form-check-input"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = self.cleaned_data.get(
            "must_change_password", True)
        if commit:
            user.save()
        return user


class AdminUserUpdateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = (
            "first_name", "last_name", "email", "office", "role", "position", "phone", "is_active",
        )

    def __init__(self, *args, editing_self: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.editing_self = editing_self
        self.fields["office"].queryset = Office.active.all()
        self.fields["is_active"].label = "Account is active"

    def clean(self):
        """Stop an administrator locking everyone out of administration.

        The suspend/reactivate button already refuses to act on your own
        account, but this form could do both of the same things — drop your own
        role to USER, or clear "Account is active" — and there is no screen left
        that could undo either afterwards. Only the shell could, which for this
        project means nobody could.
        """
        cleaned = super().clean()
        if not self.editing_self:
            return cleaned
        if cleaned.get("role") != User.Role.ADMIN:
            self.add_error(
                "role",
                "You cannot remove your own administrator role. Ask another "
                "administrator to change it for you.",
            )
        if not cleaned.get("is_active"):
            self.add_error("is_active", "You cannot deactivate your own account.")
        return cleaned


class AdminSetPasswordForm(BootstrapFormMixin, SetPasswordForm):
    """Administrator sets a new password for someone who is locked out."""


class OfficeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Office
        fields = (
            "code", "name", "short_name", "cluster", "parent", "head_name", "email", "location",
            "colour", "sort_order", "is_active",
        )
        widgets = {"colour": ColourInput()}
        help_texts = {
            "code": "Appears inside every tracking number, so keep it short and stable."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # A colour input has no empty state — left blank it posts #000000, which
        # would read as a deliberate choice of black. Showing the colour the
        # office would be given anyway means whatever is saved is what was seen.
        if not self.initial.get("colour") and not getattr(self.instance, "colour", ""):
            self.initial["colour"] = Office.next_free_colour()
        self.fields["colour"].widget.attrs["class"] = "form-control form-control-colour"

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "position")


class NotificationPreferenceForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = NotificationPreference
        fields = ("in_app_enabled",)
        labels = {
            "in_app_enabled": "Show in-app notifications",
        }
