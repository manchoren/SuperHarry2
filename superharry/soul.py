from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET


def guidance():
    return (Path(__file__).resolve().parent.parent / "SOUL.md").read_text(encoding="utf-8")


@login_required
@require_GET
def settings(request):
    return render(request, "account_settings.html", {"soul": guidance()})
