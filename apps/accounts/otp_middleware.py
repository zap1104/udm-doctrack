from urllib.parse import urlencode

from django.contrib.auth import logout
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from . import otp


class EmailOTPRequiredMiddleware:
    """No alternate endpoint may turn a password-only session into record access."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if otp.required() and request.user.is_authenticated and not otp.verified(request):
            target = otp.safe_return(request, request.get_full_path())
            logout(request)
            location = reverse('accounts:login') + '?' + urlencode({'next': target})
            response = HttpResponse(status=401) if request.headers.get('HX-Request') or request.headers.get('Accept') == 'application/json' else redirect(location)
            if request.headers.get('HX-Request'):
                response['HX-Redirect'] = location
            response['Cache-Control'] = 'private, no-store'
            return response
        return self.get_response(request)
