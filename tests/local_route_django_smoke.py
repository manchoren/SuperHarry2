"""Exercise chat over HTTP with a real but isolated Ollama gateway route."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.test import Client

    call_command("migrate", verbosity=0)
    user = get_user_model().objects.create_user(username="ollama-smoke-user")
    client = Client(HTTP_HOST="localhost")
    client.force_login(user)
    response = client.post("/chat/send/", {
        "message": "Reply with the word ready.",
        "model": os.environ["SMOKE_OLLAMA_ROUTE"],
    })
    if response.status_code != 200 or not response.streaming:
        raise RuntimeError(f"The application did not start a streamed response ({response.status_code}: {response.content.decode(errors='replace')}).")
    events = [json.loads(chunk) for chunk in response.streaming_content]
    if not any(isinstance(event.get("text"), str) and event["text"] for event in events):
        raise RuntimeError("The application received no assistant reply from the local model.")
    if events[-1] != {"done": True}:
        raise RuntimeError("The application did not save a completed local-model reply.")
    automatic = client.post("/chat/send/", {
        "message": "Reply with the word ready using Auto.",
        "model": "auto",
    })
    if automatic.status_code != 200 or not automatic.streaming:
        raise RuntimeError(f"The application did not start the Auto response ({automatic.status_code}).")
    auto_events = [json.loads(chunk) for chunk in automatic.streaming_content]
    if not any(isinstance(event.get("text"), str) and event["text"] for event in auto_events):
        raise RuntimeError("The Auto combo returned no assistant text through the application.")
    if auto_events[-1] != {"done": True}:
        raise RuntimeError("The Auto combo did not complete through the application.")
    from superharry.models import ConversationEvent
    effective = ConversationEvent.objects.filter(
        conversation_id=automatic["X-Conversation-Id"],
    ).values_list("content", flat=True).last()
    if effective not in {"Auto model is unknown"} and not (effective or "").startswith("Auto used "):
        raise RuntimeError("The Auto response did not record its effective model or uncertainty.")
    print(f"Auto history: {effective}")


if __name__ == "__main__":
    main()
