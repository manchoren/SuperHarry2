import json
import math
import socket
import threading
import time
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
    return sorted(available_route_catalog())


def available_route_catalog():
    payload = gateway_json("/v1/models", settings.OMNIROUTE_API_KEY)
    return {
        item["id"]: item for item in gateway_rows(payload, "data")
        if isinstance(item.get("id"), str) and item["id"]
    }


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


def _response_lines(response, deadline):
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Gateway stream exceeded its total time limit")
            try:
                response.fp.raw._sock.settimeout(min(30.0, remaining))
            except AttributeError:
                pass
        try:
            line = response.readline()
        except (HTTPException, OSError, ValueError, AttributeError) as error:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Gateway stream exceeded its total time limit") from error
            raise
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Gateway stream exceeded its total time limit")
        if not line:
            return
        yield line


def stream_reply(messages, *, route_id, max_tokens=None, reasoning_effort="none", on_submit=None, tools=None, gateway_url=None,
                 gateway_api_key=None, total_timeout=None):
    if total_timeout is not None and (
        isinstance(total_timeout, bool) or not isinstance(total_timeout, (int, float))
        or not math.isfinite(total_timeout) or total_timeout <= 0
    ):
        raise ValueError("total_timeout must be a positive finite number")
    payload = {"model": route_id, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if reasoning_effort != "none":
        payload["reasoning_effort"] = reasoning_effort
    gateway_url = settings.OMNIROUTE_URL if gateway_url is None else gateway_url
    gateway_api_key = settings.OMNIROUTE_API_KEY if gateway_api_key is None else gateway_api_key
    request = Request(
        f"{gateway_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {gateway_api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + total_timeout if total_timeout is not None else None
    try:
        if on_submit is not None:
            on_submit()
        open_timeout = 30 if deadline is None else max(0.001, min(30, deadline - time.monotonic()))
        response = opener.open(request, timeout=open_timeout)
    except HTTPError as error:
        error.close()
        if error.code in (402, 429):
            raise GatewayFailure("The free route has exhausted its quota or rate limit.") from None
        raise GatewayFailure("The free route is currently unavailable.") from None
    except (URLError, TimeoutError):
        raise GatewayFailure("The free route is currently unavailable.") from None
    calls = {}
    finished = False
    deadline_timer = None
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            response.close()
            raise TimeoutError("Gateway stream exceeded its total time limit")

        def interrupt_response():
            try:
                response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except (AttributeError, OSError):
                pass
            response.close()

        deadline_timer = threading.Timer(remaining, interrupt_response)
        deadline_timer.daemon = True
        deadline_timer.start()
    with response:
        try:
            if route_id in {"superharry-auto", getattr(settings, "AUTO_CHAT_MODEL", "")}:
                reported_model = response.headers.get("X-OmniRoute-Model", "").strip()
                yield {"effective_model": reported_model[:200] or None}
            for line in _response_lines(response, deadline):
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    for call in calls.values():
                        yield {"tool_call": call}
                    return
                event = json.loads(data)
                error = event.get("error")
                if isinstance(error, dict) and (
                    error.get("type") == "rate_limit_error"
                    or error.get("code") in ("model_cooldown", "rate_limit_exceeded", "insufficient_quota")
                ):
                    raise GatewayFailure("The free route has exhausted its quota or rate limit.")
                if error:
                    break
                choices = event.get("choices")
                if choices:
                    if choices[0].get("finish_reason") is not None:
                        finished = True
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        yield content
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning:
                        yield {"reasoning": reasoning}
                    for part in delta.get("tool_calls", []):
                        index = part.get("index")
                        if not isinstance(index, int) or not 0 <= index < 10:
                            raise ValueError("Invalid tool call index.")
                        call = calls.setdefault(index, {"name": "", "arguments": ""})
                        if "id" in part:
                            if not isinstance(part["id"], str) or len(part["id"]) > 255:
                                raise ValueError("Invalid tool call ID.")
                            call["id"] = part["id"]
                        function = part.get("function") or {}
                        for key in ("name", "arguments"):
                            fragment = function.get(key, "")
                            if not isinstance(fragment, str):
                                raise ValueError("Invalid tool call fragment.")
                            call[key] += fragment
                            if len(call[key]) > 65536:
                                raise ValueError("Tool call too large.")
        finally:
            if deadline_timer is not None:
                deadline_timer.cancel()
    if finished:
        for call in calls.values():
            yield {"tool_call": call}
        return
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
