import json
from http.client import HTTPException

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.db.models.functions import Coalesce
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .gateway import GatewayFailure, stream_reply
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
    history = ()
    if conversation is not None:
        later_attempts = Message.objects.filter(prompt_id=OuterRef("prompt_id"), pk__gt=OuterRef("pk"))
        history = conversation.messages.annotate(
            turn_order=Coalesce("prompt_id", "pk"),
            has_later_attempt=Exists(later_attempts),
        ).order_by("turn_order", "pk")
    return render(request, "chat.html", {
        "conversations": conversations,
        "conversation": conversation,
        "history": history,
        "active_model": settings.CHAT_MODEL,
    })


def stream_message(reply, context):
    complete = False
    error = "The model reply was interrupted."
    try:
        for text in stream_reply(context):
            reply.content += text
            reply.save(update_fields=("content",))
            yield json.dumps({"text": text}) + "\n"
        complete = True
    except GatewayFailure as failure:
        error = str(failure)
    except (OSError, ValueError, HTTPException):
        pass
    finally:
        reply.complete = complete
        reply.in_progress = False
        reply.save(update_fields=("complete", "in_progress"))

    result = {"done": True} if complete else {"error": error}
    yield json.dumps(result) + "\n"


def reply_response(reply):
    context = list(
        reply.conversation.messages.filter(complete=True)
        .annotate(turn_order=Coalesce("prompt_id", "pk"))
        .filter(turn_order__lte=reply.prompt_id)
        .order_by("turn_order", "pk")
        .values("role", "content")
    )
    response = StreamingHttpResponse(
        stream_message(reply, context), content_type="application/x-ndjson"
    )
    response["X-Conversation-Id"] = str(reply.conversation_id)
    response["Location"] = reverse("chat-history", args=(reply.conversation_id,))
    return response


def gateway_configured():
    return all((settings.OMNIROUTE_URL, settings.OMNIROUTE_API_KEY, settings.CHAT_MODEL))


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
    if not gateway_configured():
        return JsonResponse({"error": "The model gateway is not configured."}, status=503)

    with transaction.atomic():
        if conversation is None:
            conversation = conversations.create(owner=request.user, title=text[:100])
        prompt = conversation.messages.create(role=Message.Role.USER, content=text)
        reply = conversation.messages.create(
            prompt=prompt, role=Message.Role.ASSISTANT, complete=False, in_progress=True
        )

    return reply_response(reply)


@never_cache
@login_required
@require_POST
def retry(request, conversation_id, reply_id):
    attempt = get_object_or_404(
        Message.objects.select_related("conversation"),
        pk=reply_id,
        conversation_id=conversation_id,
        conversation__owner=request.user,
        role=Message.Role.ASSISTANT,
        prompt__isnull=False,
    )
    if attempt.complete or attempt.in_progress or attempt.prompt.replies.filter(pk__gt=attempt.pk).exists():
        return JsonResponse({"error": "Reload the conversation for the latest reply."}, status=409)
    if not gateway_configured():
        return JsonResponse({"error": "The model gateway is not configured."}, status=503)
    try:
        with transaction.atomic():
            reply = attempt.conversation.messages.create(
                prompt_id=attempt.prompt_id,
                role=Message.Role.ASSISTANT,
                complete=False,
                in_progress=True,
            )
    except IntegrityError:
        return JsonResponse({"error": "A reply already exists for this message."}, status=409)
    return reply_response(reply)
