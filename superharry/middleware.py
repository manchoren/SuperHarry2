from urllib.parse import urlsplit

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .models import UserProfile


class RequiredPasswordChangeMiddleware:
    """Limit accounts with temporary passwords to changing password or signing out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and UserProfile.objects.filter(
            user_id=request.user.pk,
            must_change_password=True,
        ).exists():
            allowed_paths = {
                reverse("required-password-change"),
                reverse("logout"),
                reverse("admin:logout"),
            }
            static_path = urlsplit(settings.STATIC_URL).path
            is_static = bool(static_path and request.path_info.startswith(static_path))
            if request.path_info not in allowed_paths and not is_static:
                return redirect("required-password-change")
        return self.get_response(request)
