import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GatewayFailure(Exception):
    pass


def stream_reply(messages):
    request = Request(
        f"{settings.OMNIROUTE_URL.rstrip('/')}/v1/chat/completions",
        data=json.dumps({
            "model": settings.CHAT_MODEL,
            "messages": messages,
            "stream": True,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.OMNIROUTE_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        response = opener.open(request, timeout=30)
    except HTTPError as error:
        error.close()
        if error.code in (402, 429):
            raise GatewayFailure("The free route has exhausted its quota or rate limit.") from None
        raise GatewayFailure("The free route is currently unavailable.") from None
    except (URLError, TimeoutError):
        raise GatewayFailure("The free route is currently unavailable.") from None
    with response:
        for line in response:
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                return
            event = json.loads(data)
            if event.get("error"):
                break
            choices = event.get("choices")
            if choices:
                content = choices[0].get("delta", {}).get("content")
                if isinstance(content, str) and content:
                    yield content
    raise OSError("The gateway stream ended before completion.")
