from pathlib import Path
from io import BytesIO

from django.conf import settings as django_settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods
from PIL import Image, ImageOps, UnidentifiedImageError

from .models import UserProfile


def guidance():
    return (Path(__file__).resolve().parent.parent / "SOUL.md").read_text(encoding="utf-8")


@login_required
@require_http_methods(["GET", "POST"])
def settings(request):
    error = ""
    notice = ""
    password_form = PasswordChangeForm(request.user, request.POST if request.method == "POST" and request.POST.get("action") == "password" else None)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "password":
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                notice = "Password updated."
                password_form = PasswordChangeForm(user)
        elif action == "remove-avatar":
            UserProfile.objects.filter(user=request.user).update(avatar=None)
            notice = "Profile image removed."
        elif action == "avatar":
            upload = request.FILES.get("avatar")
            if not upload or upload.size > django_settings.AVATAR_MAX_BYTES:
                error = "Choose a PNG or JPEG within the profile image size limit."
            else:
                try:
                    with Image.open(upload) as image:
                        if image.format not in ("PNG", "JPEG") or image.width * image.height > 20_000_000:
                            raise ValueError
                        image = ImageOps.exif_transpose(image)
                        image.thumbnail((256, 256))
                        image = image.convert("RGBA")
                        output = BytesIO()
                        image.save(output, format="PNG")
                    UserProfile.objects.update_or_create(user=request.user, defaults={"avatar": output.getvalue()})
                    notice = "Profile image updated."
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                    error = "Choose a valid PNG or JPEG image."
        else:
            return HttpResponse(status=400)
    return render(request, "account_settings.html", {"soul": guidance(), "password_form": password_form, "error": error, "notice": notice})


@login_required
@require_GET
def avatar(request):
    data = UserProfile.objects.filter(user=request.user).values_list("avatar", flat=True).first()
    if not data:
        return HttpResponse(status=404)
    return HttpResponse(bytes(data), content_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
