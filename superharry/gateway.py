import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GatewayFailure(Exception):
    pass


def gateway_json(path, key, *, method="GET", payload=None):
    if not settings.OMNIROUTE_URL or not key:
        raise GatewayFailure("The gateway credential is not configured.")
    try:
        request = Request(
            f"{settings.OMNIROUTE_URL.rstrip('/')}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method=method,
        )
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as error:
        error.close()
        raise GatewayFailure(f"The gateway rejected the operation (HTTP {error.code}).") from None
    except (OSError, ValueError, HTTPException):
        raise GatewayFailure("The gateway did not return a usable response.") from None
    if not isinstance(result, dict):
        raise GatewayFailure("The gateway did not return a usable response.")
    return result


def available_routes():
    payload = gateway_json("/v1/models", settings.OMNIROUTE_API_KEY)
    return sorted({
        item["id"] for item in gateway_rows(payload, "data")
        if isinstance(item.get("id"), str) and item["id"]
    })


def gateway_rows(payload, field):
    rows = payload.get(field)
    if not isinstance(rows, list):
        raise GatewayFailure("The gateway did not return the requested catalog.")
    return (row for row in rows if isinstance(row, dict))


def manage_gateway(path, **kwargs):
    token = settings.OMNIROUTE_MANAGEMENT_TOKEN
    if token and token == settings.OMNIROUTE_API_KEY:
        raise GatewayFailure("Configure a separate gateway management credential.")
    return gateway_json(path, token, **kwargs)


def provider_connections():
    payload = manage_gateway("/api/providers")
    return [
        {"id": item["id"], "provider": item["provider"], "name": item.get("name") or item["provider"]}
        for item in gateway_rows(payload, "connections")
        if isinstance(item.get("id"), str) and isinstance(item.get("provider"), str)
    ]


def stream_reply(messages, *, route_id, max_tokens=None):
    payload = {"model": route_id, "messages": messages, "stream": True}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    request = Request(
        f"{settings.OMNIROUTE_URL.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
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


def probe_route(route_id):
    received_text = False
    try:
        for _ in stream_reply(
            [{"role": "user", "content": "Reply with OK."}], route_id=route_id, max_tokens=8
        ):
            received_text = True
    except (OSError, ValueError, HTTPException):
        raise GatewayFailure("The route test did not complete.") from None
    if not received_text:
        raise GatewayFailure("The route test returned no text.")
