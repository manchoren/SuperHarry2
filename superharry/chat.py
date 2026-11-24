import json
from http.client import HTTPException

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .gateway import stream_reply
from .models import Conversation, Message


@never_cache
@login_required
@require_GET
def page(request, conversation_id=None):
    conversations = Conversation.objects.filter(owner=request.user).order_by("-pk")
    conversation = (
        get_object_or_404(conversations, pk=conversation_id)
        if conversation_id is not None
        else None
    )
    return render(request, "chat.html", {
        "conversations": conversations,
        "conversation": conversation,
        "active_model": settings.CHAT_MODEL,
    })


def stream_message(reply, context):
    chunks = []
    complete = False
    try:
        for text in stream_reply(context):
            chunks.append(text)
            yield json.dumps({"text": text}) + "\n"
        complete = True
    except (OSError, ValueError, HTTPException):
        pass
    finally:
        reply.content = "".join(chunks)
        reply.complete = complete
        reply.save(update_fields=("content", "complete"))

    result = {"done": True} if complete else {"error": "The model reply was interrupted."}
    yield json.dumps(result) + "\n"


@never_cache
@login_required
@require_POST
def send(request, conversation_id=None):
    conversations = Conversation.objects.filter(owner=request.user)
    conversation = (
        get_object_or_404(conversations, pk=conversation_id)
        if conversation_id is not None
        else None
    )
    text = request.POST.get("message", "").strip()
    if not text:
        return JsonResponse({"error": "Enter a message."}, status=400)
    if not all((settings.OMNIROUTE_URL, settings.OMNIROUTE_API_KEY, settings.CHAT_MODEL)):
        return JsonResponse({"error": "The model gateway is not configured."}, status=503)

    with transaction.atomic():
        if conversation is None:
            conversation = conversations.create(owner=request.user, title=text[:100])
        conversation.messages.create(role=Message.Role.USER, content=text)
        reply = conversation.messages.create(role=Message.Role.ASSISTANT, complete=False)
        context = list(conversation.messages.filter(complete=True).values("role", "content"))

    response = StreamingHttpResponse(
        stream_message(reply, context), content_type="application/x-ndjson"
    )
    response["X-Conversation-Id"] = str(conversation.pk)
    response["Location"] = reverse("chat-history", args=(conversation.pk,))
    return response
