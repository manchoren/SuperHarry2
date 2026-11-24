from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .models import ChatInput


@never_cache
@login_required
@require_http_methods(("GET", "POST"))
def manage(request):
    error = None
    if request.method == "POST":
        incoming = request.FILES.get("file")
        if incoming and incoming.size <= 2 * 1024 * 1024:
            payload = incoming.read(2 * 1024 * 1024 + 1)
            name = incoming.name[:255]
            media = None
            if name.lower().endswith((".txt", ".csv")) and len(payload) <= 256 * 1024:
                try:
                    payload.decode("utf-8")
                    media = "text/plain"
                except UnicodeDecodeError:
                    pass
            elif payload.startswith(b"\x89PNG\r\n\x1a\n"):
                media = "image/png"
            elif payload.startswith(b"\xff\xd8\xff"):
                media = "image/jpeg"
            if media:
                ChatInput.objects.create(owner=request.user, name=name, media_type=media, payload=payload)
                return redirect("inputs")
        error = "Choose a UTF-8 text or CSV file (up to 256 KiB), or a PNG or JPEG image (up to 2 MiB)."
    items = ChatInput.objects.filter(owner=request.user).defer("payload").order_by("-pk")
    return render(request, "inputs.html", {"inputs": items, "error": error}, status=400 if error else 200)


@never_cache
@login_required
@require_POST
def delete(request, pk):
    item = get_object_or_404(ChatInput, pk=pk, owner=request.user)
    item.delete()
    return redirect("inputs")
