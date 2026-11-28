"""Process the dedicated skill spool from a host with Docker access."""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from urllib.parse import quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.skill_sandbox import remove_container, run_skill
from scripts.runtime_policy import check_component_versions
from scripts.skill_network import NetworkMediator, mediate_action
from scripts.worker_lock import lock_spool
from superharry.csv_protocol import publish, read_limited
from superharry.browser_tasks import (
    consent_is_current, consent_path, validate_browser_handoff_url, validate_browser_url,
    validate_owner_get_action,
)
from superharry.browser_search import search_mwmbl_urls
from superharry.search_providers import SearchProviderError, SearchProviderUnavailable


MAX_JOB_BYTES = 8 * 1024 * 1024
DEFAULT_PUBLIC_RESOURCES = frozenset({"https://docs.python.org/3/"})
SEARCH_ANSWER_BRIDGE_TIMEOUT_SECONDS = 110


def read_approved_search_query(directory, identifier, owner_id, token, cancelled):
    """Read the approved query from Django without writing it to the host spool."""
    if cancelled.is_set() or not consent_is_current(directory, identifier, owner_id, token):
        raise PermissionError("Browser task approval is no longer active")
    configured_port = os.environ.get("SUPERHARRY_PORT", "8000")
    if not re.fullmatch(r"[0-9]{1,5}", configured_port) or not 1 <= int(configured_port) <= 65535:
        raise SearchProviderUnavailable("Approved search query is unavailable.")
    payload = json.dumps({"operation": "get_search_query", "owner_id": owner_id}, ensure_ascii=True).encode("ascii")
    url = f"http://127.0.0.1:{int(configured_port)}/skill-runs/{identifier}/browser-handoff/bridge/"
    request = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Cache-Control": "no-store",
        "X-Browser-Task-Capability": token,
    })
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            raw = response.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise SearchProviderUnavailable("Approved search query exceeded its limit.")
            value = json.loads(raw)
            query = value.get("search_query") if isinstance(value, dict) else None
            if not isinstance(query, str) or not query or cancelled.is_set():
                raise SearchProviderUnavailable("Approved search query is unavailable.")
            return query
    except urllib.error.HTTPError as error:
        if error.code == 410:
            raise PermissionError("Browser task approval expired") from None
        raise SearchProviderUnavailable("Approved search query is unavailable.") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise SearchProviderUnavailable("Approved search query is unavailable.") from None


def synthesize_approved_search_answer(directory, identifier, owner_id, token, page_content, citations, cancelled):
    """Send bounded, verified browser text to Django for model synthesis."""
    if (cancelled.is_set() or not consent_is_current(directory, identifier, owner_id, token)
            or not isinstance(page_content, str) or not page_content.strip()
            or len(page_content.encode("utf-8")) > 16 * 1024
            or not isinstance(citations, list) or not 1 <= len(citations) <= 5):
        raise PermissionError("Search answer request is no longer valid")
    configured_port = os.environ.get("SUPERHARRY_PORT", "8000")
    if not re.fullmatch(r"[0-9]{1,5}", configured_port) or not 1 <= int(configured_port) <= 65535:
        raise SearchProviderUnavailable("Search answer could not be generated.")
    payload = json.dumps({
        "operation": "synthesize_search_answer", "owner_id": owner_id,
        "page_content": page_content, "citations": citations,
    }, ensure_ascii=False).encode("utf-8")
    if len(payload) > 128 * 1024:
        raise SearchProviderUnavailable("Search answer request exceeded its limit.")
    url = f"http://127.0.0.1:{int(configured_port)}/skill-runs/{identifier}/browser-handoff/bridge/"
    request = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Cache-Control": "no-store",
        "X-Browser-Task-Capability": token,
    })
    try:
        with urllib.request.urlopen(request, timeout=SEARCH_ANSWER_BRIDGE_TIMEOUT_SECONDS) as response:
            raw = response.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise SearchProviderUnavailable("Search answer exceeded its limit.")
            value = json.loads(raw)
            answer = value.get("answer") if isinstance(value, dict) else None
            answer_citations = value.get("citations") if isinstance(value, dict) else None
            if (not isinstance(answer, str) or not answer.strip()
                    or not isinstance(answer_citations, list) or answer_citations != citations
                    or cancelled.is_set() or not consent_is_current(directory, identifier, owner_id, token)):
                raise SearchProviderUnavailable("Search answer could not be generated.")
            return answer
    except urllib.error.HTTPError as error:
        if error.code == 410:
            raise PermissionError("Browser task approval expired") from None
        raise SearchProviderUnavailable("Search answer could not be generated.") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise SearchProviderUnavailable("Search answer could not be generated.") from None


def synthesize_approved_handoff_answer(directory, identifier, owner_id, token, page_content, citations, cancelled):
    """Send the opted-in authenticated page excerpt to Django for continuation."""
    if (cancelled.is_set() or not consent_is_current(directory, identifier, owner_id, token)
            or not isinstance(page_content, str) or not page_content.strip()
            or len(page_content.encode("utf-8")) > 16 * 1024
            or not isinstance(citations, list) or not 1 <= len(citations) <= 10):
        raise SearchProviderUnavailable("Authenticated page continuation is unavailable.")
    configured_port = os.environ.get("SUPERHARRY_PORT", "8000")
    if not re.fullmatch(r"[0-9]{1,5}", configured_port) or not 1 <= int(configured_port) <= 65535:
        raise SearchProviderUnavailable("Authenticated page continuation is unavailable.")
    payload = json.dumps({
        "operation": "synthesize_handoff_answer", "owner_id": owner_id,
        "page_content": page_content, "citations": citations,
    }, ensure_ascii=False).encode("utf-8")
    if len(payload) > 128 * 1024:
        raise SearchProviderUnavailable("Authenticated page continuation exceeded its limit.")
    url = f"http://127.0.0.1:{int(configured_port)}/skill-runs/{identifier}/browser-handoff/bridge/"
    request = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Cache-Control": "no-store",
        "X-Browser-Task-Capability": token,
    })
    try:
        with urllib.request.urlopen(request, timeout=SEARCH_ANSWER_BRIDGE_TIMEOUT_SECONDS) as response:
            raw = response.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise SearchProviderUnavailable("Authenticated page answer exceeded its limit.")
            value = json.loads(raw)
            answer = value.get("answer") if isinstance(value, dict) else None
            if not isinstance(answer, str) or not answer.strip() or cancelled.is_set():
                raise SearchProviderUnavailable("Authenticated page continuation is unavailable.")
            return answer
    except urllib.error.HTTPError as error:
        if error.code == 410:
            raise PermissionError("Browser task approval expired") from None
        raise SearchProviderUnavailable("Authenticated page continuation is unavailable.") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise SearchProviderUnavailable("Authenticated page continuation is unavailable.") from None


def job_id(path):
    try:
        identifier = UUID(path.name.split(".", 1)[0])
        return identifier if path.name.startswith(str(identifier) + ".") else None
    except ValueError:
        return None


def cleanup_network_requests(spool, identifier):
    for path in spool.glob(f"{identifier}.*.network-*"):
        parts = path.name.split(".")
        try:
            request_id = UUID(parts[1])
        except (IndexError, ValueError):
            continue
        if (len(parts) == 3 and parts[0] == str(identifier) and parts[1] == str(request_id)
                and parts[2] in ("network-request", "network-decision")):
            path.unlink(missing_ok=True)


def cleanup_browser_session_files(spool, identifier):
    for suffix in ("browser-view", "browser-command", "browser-screenshot-complete"):
        (spool / f"{identifier}.{suffix}").unlink(missing_ok=True)


def cleanup_browser_handoff_bridge(identifier, request):
    if not isinstance(request, dict) or request.get("browser_handoff") is not True:
        return
    owner_id = request.get("_browser_owner_id")
    token = request.get("_browser_consent_token")
    if type(owner_id) is not int or owner_id <= 0 or not isinstance(token, str) or not 32 <= len(token) <= 128:
        raise RuntimeError("Owner-control bridge identity is unavailable during cleanup")
    configured_port = os.environ.get("SUPERHARRY_PORT", "8000")
    if not re.fullmatch(r"[0-9]{1,5}", configured_port) or not 1 <= int(configured_port) <= 65535:
        raise RuntimeError("Owner-control bridge port is invalid during cleanup")
    payload = json.dumps({"operation": "cleanup", "owner_id": owner_id}, ensure_ascii=True).encode("ascii")
    url = f"http://127.0.0.1:{int(configured_port)}/skill-runs/{identifier}/browser-handoff/bridge/"
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json", "Cache-Control": "no-store",
        "X-Browser-Task-Capability": token,
    })
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            cleared = json.loads(response.read(2048)).get("cleared") is True
            if not cleared:
                raise RuntimeError("Owner-control bridge cleanup was not confirmed")
    except urllib.error.HTTPError as error:
        if error.code == 410:
            return
        raise RuntimeError("Owner-control bridge cleanup failed") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise RuntimeError("Owner-control bridge cleanup could not be confirmed") from None


def process_queue(spool, runtime_check=None, *, image=None, browser_image="superharry-skill-browser-worker:local", package_handler=None, public_resources=DEFAULT_PUBLIC_RESOURCES, mediator=None):
    while _process_queue_once(spool, runtime_check=runtime_check, image=image, package_handler=package_handler,
                              browser_image=browser_image, public_resources=public_resources, mediator=mediator):
        pass


def _process_queue_once(spool, runtime_check=None, *, image=None, browser_image="superharry-skill-browser-worker:local", package_handler=None, public_resources=DEFAULT_PUBLIC_RESOURCES, mediator=None):
    rescan = False
    for control in spool.glob("*.skill-control"):
        identifier = job_id(control)
        if identifier is None or (spool / f"{identifier}.skill-running").exists():
            continue
        applying = spool / f"{identifier}.skill-applying"
        try:
            control.replace(applying)
            request = json.loads(read_limited(applying, MAX_JOB_BYTES))
            generation = request.get("generation")
            if (not isinstance(generation, int) or isinstance(generation, bool) or generation < 1
                    or request.get("action") not in ("updated", "deleted", "cancelled")
                    or (request.get("action") == "updated" and not isinstance(request.get("payload"), dict))):
                raise ValueError("Invalid lifecycle control")
            incoming = spool / f"{identifier}.skill-input"
            outcome = spool / f"{identifier}.skill-result"
            try:
                previous = json.loads(read_limited(outcome, 100 * 1024))
            except (OSError, ValueError):
                previous = {}
            if isinstance(previous, dict) and previous.get("generation", 0) >= generation:
                applying.unlink(missing_ok=True)
                continue
            action = request.get("action")
            if action in ("deleted", "cancelled") or (action == "updated" and (
                    (spool / f"{identifier}.skill-external").exists() or request.get("uncertain") or not request.get("payload"))):
                incoming.unlink(missing_ok=True)
                cleanup_network_requests(spool, identifier)
                reason = ("skill_deleted" if action == "deleted" else
                          "account_deactivated" if action == "cancelled" else
                          "skill_changed_external" if (spool / f"{identifier}.skill-external").exists()
                          else "skill_changed_uncertain" if request.get("uncertain")
                          else "skill_changed_unavailable")
                result = {"status": "failed", "reason": reason, "generation": generation}
                if action == "cancelled":
                    result["external_action_uncertain"] = bool(
                        request.get("uncertain") or (spool / f"{identifier}.skill-external").exists()
                    )
                preserve_browser_answer = (
                    action == "cancelled" and isinstance(previous, dict)
                    and previous.get("status") in ("complete", "failed")
                    and previous.get("_browser_session_answer") is True
                )
                if not preserve_browser_answer:
                    publish(outcome, json.dumps(result).encode("ascii"), replace=True)
            elif action == "updated":
                payload = request["payload"]
                payload["_lifecycle_generation"] = generation
                outcome.unlink(missing_ok=True)
                publish(incoming, json.dumps(payload, ensure_ascii=True).encode("ascii"), replace=True)
            applying.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError, TypeError) as error:
            raise RuntimeError("Cannot safely apply skill lifecycle control.") from error
    for incoming in spool.glob("*.skill-input"):
        identifier = job_id(incoming)
        if identifier is None:
            continue
        running = spool / f"{identifier}.skill-running"
        outcome = spool / f"{identifier}.skill-result"
        if outcome.exists():
            incoming.unlink(missing_ok=True)
            continue
        if runtime_check is not None:
            runtime_check()
        try:
            incoming.replace(running)
        except FileNotFoundError:
            # A concurrent lifecycle operation may have claimed the queued job.
            continue
        request = {}
        browser_runtime = False
        browser_handoff = False
        browser_owner_id = None
        browser_token = None
        try:
            request = json.loads(read_limited(running, MAX_JOB_BYTES))
            browser_runtime = isinstance(request, dict) and request.get("runtime") == "playwright-browser"
            browser_session = browser_runtime and request.get("browser_session") is True
            browser_handoff = browser_runtime and request.get("browser_handoff") is True
            continue_with_model = browser_runtime and request.get("continue_with_model") is True
            browser_search = browser_runtime and request.get("search_task") is True
            if browser_runtime:
                base_keys = ({"runtime", "search_task", "_browser_owner_id", "_browser_consent_token", "browser_session", "browser_handoff"}
                             if browser_search else
                             {"runtime", "target_url", "_browser_owner_id", "_browser_consent_token", "browser_session", "browser_handoff"})
                action_keys = {"action_request", "action_route_id"}
                continuation_keys = {"continue_with_model"}
                expected_sets = {frozenset(base_keys)}
                if not browser_search:
                    expected_sets.update((frozenset(base_keys | continuation_keys),
                                          frozenset(base_keys | action_keys),
                                          frozenset(base_keys | action_keys | continuation_keys)))
                if frozenset(request) not in expected_sets:
                    raise ValueError("Invalid browser job")
                if type(request.get("browser_handoff")) is not bool:
                    raise ValueError("Invalid browser handoff mode")
                if ("continue_with_model" in request and type(request["continue_with_model"]) is not bool
                        or continue_with_model and not browser_handoff):
                    raise ValueError("Invalid authenticated page continuation consent")
                if action_keys.issubset(request) and (
                        request.get("browser_handoff") is not True
                        or not isinstance(request.get("action_request"), str)
                        or not 1 <= len(request["action_request"]) <= 1000
                        or not isinstance(request.get("action_route_id"), str)
                        or not 1 <= len(request["action_route_id"]) <= 255):
                    raise ValueError("Invalid agent-prepared browser action")
                if browser_search:
                    if browser_handoff or request.get("search_task") is not True:
                        raise ValueError("Invalid browser search task")
                else:
                    (validate_browser_handoff_url(request["target_url"]) if browser_handoff
                     else validate_browser_url(request["target_url"]))
                browser_owner_id = request.pop("_browser_owner_id")
                browser_token = request.pop("_browser_consent_token")
                if browser_search:
                    request.pop("search_task")
                if not consent_is_current(spool, identifier, browser_owner_id, browser_token):
                    raise PermissionError("Browser task approval expired")
                request.pop("continue_with_model", None)
                if continue_with_model:
                    request["follow_authenticated_links"] = True
            cancelled = threading.Event()
            control = spool / f"{identifier}.skill-control"
            stop_watcher = threading.Event()

            def watch_control():
                while not stop_watcher.wait(0.05):
                    if control.exists():
                        cancelled.set()

            watcher = threading.Thread(target=watch_control, daemon=True)
            watcher.start()
            active_mediator = mediator or NetworkMediator(public_resources=public_resources)
            wheels = dict(request.get("wheels", {}))
            browser_bytes = 0
            owner_control = False
            active_owner_sequence = None
            handoff_returned = False
            handoff_cleaned = False
            owner_sensitive_values = []
            screenshot_command_id = str(uuid5(NAMESPACE_URL, f"superharry-browser-screenshot:{identifier}"))

            def browser_handoff_call(operation, **extra):
                if not browser_handoff or not consent_is_current(spool, identifier, browser_owner_id, browser_token):
                    raise PermissionError("Owner-control session is no longer active")
                configured_port = os.environ.get("SUPERHARRY_PORT", "8000")
                if not re.fullmatch(r"[0-9]{1,5}", configured_port) or not 1 <= int(configured_port) <= 65535:
                    raise PermissionError("Owner-control bridge is unavailable")
                payload = json.dumps({
                    "operation": operation, "owner_id": browser_owner_id, **extra,
                }, ensure_ascii=True).encode("ascii")
                url = f"http://127.0.0.1:{int(configured_port)}/skill-runs/{identifier}/browser-handoff/bridge/"
                req = urllib.request.Request(url, data=payload, method="POST", headers={
                    "Content-Type": "application/json", "Cache-Control": "no-store",
                    "X-Browser-Task-Capability": browser_token,
                })
                try:
                    with urllib.request.urlopen(req, timeout=4) as response:
                        raw = response.read(3 * 1024 * 1024 + 1)
                        if len(raw) > 3 * 1024 * 1024:
                            raise PermissionError("Owner-control response exceeded its limit")
                        if not raw:
                            return {}
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise PermissionError("Owner-control response was invalid")
                        return value
                except urllib.error.HTTPError as error:
                    raise PermissionError(f"Owner-control bridge rejected the request ({error.code})") from None
                except (OSError, ValueError, urllib.error.URLError):
                    raise PermissionError("Owner-control bridge is unavailable") from None

            def internal_browser_response(value, *, status=200):
                return {
                    "status": status,
                    "headers": {"content-type": "application/json; charset=utf-8"},
                    "body": json.dumps(value, ensure_ascii=True).encode("ascii"),
                }

            def fetch_package(action):
                content = (package_handler or active_mediator.retrieve_package)(action)
                name = unquote(urlsplit(action["url"]).path.rsplit("/", 1)[-1])
                if name.endswith(".whl"):
                    if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name):
                        raise ValueError("Invalid wheel filename")
                    encoded = base64.b64encode(content).decode("ascii")
                    if name in wheels and wheels[name] != encoded:
                        raise ValueError("Conflicting wheel contents")
                    wheels[name] = encoded
                    if len(json.dumps({**request, "wheels": wheels}).encode("utf-8")) > MAX_JOB_BYTES:
                        raise ValueError("Prepared dependencies exceed the job size limit")
                return content

            def send_network(action):
                # This marker survives interruption and must precede any external request.
                publish(spool / f"{identifier}.skill-external", b"requested", replace=False)
                if cancelled.is_set():
                    raise PermissionError("Execution is no longer active")
                return mediate_action(action, spool, identifier, active_mediator, cancelled=cancelled)

            def send_browser(action):
                nonlocal browser_bytes, owner_control, active_owner_sequence, handoff_returned, handoff_cleaned
                def consent_active():
                    return (not cancelled.is_set() and not control.exists()
                            and consent_is_current(spool, identifier, browser_owner_id, browser_token))

                def propose_browser_action(proposal):
                    response = browser_handoff_call("propose_action", **proposal)
                    return response.get("proposal")

                def wait_browser_action(proposal_id, digest):
                    response = browser_handoff_call(
                        "poll_action_decision", proposal_id=proposal_id, digest=digest,
                    )
                    return response.get("decision")

                def report_browser_action(message):
                    if browser_handoff:
                        browser_handoff_call("report_action_status", message=message)

                def plan_browser_action(forms):
                    if not browser_handoff or "action_request" not in request or not owner_control:
                        raise PermissionError("Agent action preparation is unavailable")
                    response = browser_handoff_call("prepare_action", forms=forms)
                    plan = response.get("plan")
                    if not isinstance(plan, dict):
                        raise PermissionError("No safe form action could be prepared")
                    return plan

                if not browser_runtime or not consent_active():
                    raise PermissionError("Execution is no longer active")
                if browser_session and isinstance(action, dict):
                    operation = action.get("operation")
                    if operation == "prepare_action" and set(action) == {"operation", "forms"}:
                        if not browser_handoff or not owner_control or "action_request" not in request:
                            raise PermissionError("Agent action preparation is unavailable")
                        return internal_browser_response({"plan": plan_browser_action(action["forms"])})
                    if operation == "report_action_status" and set(action) == {"operation", "message"}:
                        message = action.get("message")
                        if (not browser_handoff or not owner_control or not isinstance(message, str)
                                or not 1 <= len(message) <= 300):
                            raise PermissionError("Owner action status is unavailable")
                        report_browser_action(message)
                        return internal_browser_response({"reported": True})
                    if operation == "poll_owner_control" and set(action) == {"operation"}:
                        if not browser_handoff:
                            raise PermissionError("Owner control is not enabled for this task")
                        event = browser_handoff_call("poll")
                        if event.get("type") == "take_control":
                            owner_control = True
                            active_owner_sequence = None
                        elif event.get("type") == "return_control":
                            owner_control = False
                            active_owner_sequence = None
                            handoff_returned = True
                        elif event.get("type") in ("click", "key") and owner_control:
                            active_owner_sequence = event.get("sequence")
                        elif event.get("type") == "type" and owner_control:
                            active_owner_sequence = None
                            value = event.get("text")
                            if isinstance(value, str) and value:
                                owner_sensitive_values.append(value)
                        return internal_browser_response(event)

                    if operation == "cleanup_owner_control" and set(action) == {"operation"}:
                        if not browser_handoff or not handoff_returned:
                            raise PermissionError("Owner control must be returned before cleanup")
                        cleaned = browser_handoff_call("cleanup")
                        if cleaned.get("cleared") is not True:
                            raise PermissionError("Owner-control cleanup could not be confirmed")
                        handoff_cleaned = True
                        owner_control = False
                        active_owner_sequence = None
                        return internal_browser_response({"cleared": True})

                    if operation == "publish_owner_view" and set(action) == {"operation", "mime", "data"}:
                        if (not browser_handoff or action.get("mime") != "image/jpeg"
                                or not isinstance(action.get("data"), str)
                                or len(action["data"]) > 4 * 1024 * 1024):
                            raise PermissionError("Owner-control frame is invalid")
                        try:
                            image_bytes = base64.b64decode(action["data"], validate=True)
                        except (ValueError, TypeError):
                            raise PermissionError("Owner-control frame is invalid") from None
                        if (not 4 <= len(image_bytes) <= 2 * 1024 * 1024
                                or not image_bytes.startswith(b"\xff\xd8\xff")):
                            raise PermissionError("Owner-control frame exceeded its limit")
                        published = browser_handoff_call("publish_view", data=action["data"])
                        if published.get("published") is not True:
                            raise PermissionError("Owner-control frame could not be published")
                        return internal_browser_response({"published": True})

                    if operation == "publish_result":
                        if (set(action) != {"operation", "message", "citations"}
                                or not isinstance(action.get("message"), str)
                                or len(action["message"].encode("utf-8")) > 16 * 1024
                                or not isinstance(action.get("citations"), list)
                                or len(action["citations"]) > 10
                                or not browser_handoff and not action["citations"]):
                            raise PermissionError("Browser result is invalid")
                        try:
                            citations = list(dict.fromkeys(
                                validate_browser_url(url) for url in action["citations"]
                            ))
                        except (TypeError, ValueError):
                            raise PermissionError("Browser citations are invalid") from None
                        if browser_handoff and any(urlsplit(url).query or urlsplit(url).fragment for url in citations):
                            raise PermissionError("Owner-controlled citations cannot contain query or fragment data")
                        if (not browser_handoff and not citations) or not consent_active():
                            raise PermissionError("Browser result is no longer active")
                        if browser_handoff and (not handoff_returned or not handoff_cleaned):
                            raise PermissionError("Owner-controlled results are withheld until return and cleanup")
                        safe_message = action["message"]
                        if browser_search:
                            try:
                                safe_message = synthesize_approved_search_answer(
                                    spool, identifier, browser_owner_id, browser_token,
                                    safe_message, citations, cancelled,
                                )
                            except SearchProviderError:
                                failed_answer = {
                                    "status": "failed", "stage": "synthesis",
                                    "reason": "search_answer_unavailable",
                                    "message": "The pages were opened, but an answer could not be generated.",
                                    "generation": request.get("_lifecycle_generation", 0),
                                    "_browser_session_answer": True,
                                }
                                publish(outcome, json.dumps(failed_answer, ensure_ascii=True).encode("ascii"), replace=True)
                                owner_sensitive_values.clear()
                                return internal_browser_response({"published": True})
                        if browser_handoff:
                            secrets_to_redact = list(owner_sensitive_values)
                            if len(owner_sensitive_values) > 1:
                                secrets_to_redact.append("".join(owner_sensitive_values))
                            for secret in sorted(secrets_to_redact, key=len, reverse=True):
                                if not secret:
                                    continue
                                for variant in {secret, quote(secret, safe=""), quote(secret, safe="/")}:
                                    if variant:
                                        safe_message = re.sub(re.escape(variant), "[redacted]", safe_message, flags=re.IGNORECASE)
                            if continue_with_model:
                                try:
                                    safe_message = synthesize_approved_handoff_answer(
                                        spool, identifier, browser_owner_id, browser_token,
                                        safe_message, citations, cancelled,
                                    )
                                except SearchProviderError:
                                    failed_answer = {
                                        "status": "failed", "stage": "continuation",
                                        "reason": "authenticated_page_continuation_unavailable",
                                        "message": "The authenticated page was opened, but the selected model could not continue the task.",
                                        "generation": request.get("_lifecycle_generation", 0),
                                        "_browser_session_answer": True,
                                    }
                                    publish(outcome, json.dumps(failed_answer, ensure_ascii=True).encode("ascii"), replace=True)
                                    owner_sensitive_values.clear()
                                    return internal_browser_response({"published": True})
                        early = {
                            "status": "complete", "output": safe_message,
                            "citations": citations,
                            "generation": request.get("_lifecycle_generation", 0),
                            "_browser_session_answer": True,
                        }
                        publish(outcome, json.dumps(early, ensure_ascii=True).encode("ascii"), replace=True)
                        owner_sensitive_values.clear()
                        return internal_browser_response({"published": True})

                    if operation == "poll_command" and set(action) == {"operation"}:
                        command_path = spool / f"{identifier}.browser-command"
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            if not consent_active():
                                raise PermissionError("Execution is no longer active")
                            try:
                                pending = json.loads(read_limited(command_path, 2048))
                            except FileNotFoundError:
                                pending = None
                            except (OSError, ValueError):
                                raise PermissionError("Browser command is invalid") from None
                            if pending is not None:
                                if (set(pending) != {"job_id", "owner_id", "command_id", "command", "expires_at"}
                                        or pending["job_id"] != str(identifier)
                                        or pending["owner_id"] != browser_owner_id
                                        or pending["command_id"] != screenshot_command_id
                                        or pending["command"] != "capture_screenshot"
                                        or type(pending["expires_at"]) not in (int, float)
                                        or time.time() >= pending["expires_at"]):
                                    command_path.unlink(missing_ok=True)
                                    return internal_browser_response({"command": None})
                                return internal_browser_response({
                                    "command": "capture_screenshot", "command_id": screenshot_command_id,
                                })
                            time.sleep(0.1)
                        return internal_browser_response({"command": None})

                    if operation in ("publish_view", "screenshot_result"):
                        if browser_handoff:
                            raise PermissionError("Owner-controlled images cannot be saved to artifacts or spool")
                        expected_action_keys = (
                            {"operation", "mime", "data"} if operation == "publish_view"
                            else {"operation", "command_id", "mime", "data"}
                        )
                        if (set(action) != expected_action_keys or action.get("mime") != "image/jpeg"
                                or not isinstance(action.get("data"), str)
                                or len(action["data"]) > 4 * 1024 * 1024):
                            raise PermissionError("Browser image message is invalid")
                        try:
                            image_bytes = base64.b64decode(action["data"], validate=True)
                        except (ValueError, TypeError):
                            raise PermissionError("Browser image data is invalid") from None
                        if (not 4 <= len(image_bytes) <= 2 * 1024 * 1024
                                or not image_bytes.startswith(b"\xff\xd8\xff")):
                            raise PermissionError("Browser image exceeded its limit or had an invalid format")
                        if operation == "publish_view":
                            if not consent_active():
                                raise PermissionError("Execution is no longer active")
                            publish(spool / f"{identifier}.browser-view", image_bytes, replace=True)
                            return internal_browser_response({"saved": True})

                        command_path = spool / f"{identifier}.browser-command"
                        try:
                            pending = json.loads(read_limited(command_path, 2048))
                        except (OSError, ValueError):
                            raise PermissionError("Screenshot command is no longer active") from None
                        if (action["command_id"] != screenshot_command_id
                                or pending.get("command_id") != screenshot_command_id
                                or pending.get("owner_id") != browser_owner_id
                                or pending.get("job_id") != str(identifier)
                                or pending.get("command") != "capture_screenshot"
                                or time.time() >= pending.get("expires_at", 0)):
                            raise PermissionError("Screenshot command does not match this task")
                        save_artifacts([{"name": "browser-screenshot.jpg", "data": action["data"]}])
                        publish(spool / f"{identifier}.browser-screenshot-complete", b"complete", replace=True)
                        command_path.unlink(missing_ok=True)
                        return internal_browser_response({"saved": True})

                if browser_handoff and isinstance(action, dict) and set(action) == {
                    "method", "url", "headers", "body", "owner_sequence", "main_navigation", "action_context",
                    "action_origin",
                }:
                    if not owner_control:
                        raise PermissionError("Owner-controlled browser requests are paused")
                    method = action.get("method")
                    sequence = action.get("owner_sequence")
                    origin = action.get("action_origin")
                    continuation_read = origin == "continuation"
                    if continuation_read:
                        if (owner_control or not handoff_returned or not continue_with_model
                                or method != "GET" or sequence is not None):
                            raise PermissionError("Authenticated continuation permits only consented GET requests after return")
                    elif not owner_control:
                        raise PermissionError("Owner-controlled browser requests are paused")
                    elif origin == "owner" and method == "POST":
                        if (type(sequence) is not int or sequence != active_owner_sequence
                                or action.get("main_navigation") is not True):
                            raise PermissionError("Form submission was not directly initiated by the owner")
                        active_owner_sequence = None
                    elif origin == "owner" and method == "GET":
                        if action.get("main_navigation") and type(sequence) is int and sequence == active_owner_sequence:
                            active_owner_sequence = None
                    elif origin == "agent" and method == "POST":
                        if ("action_request" not in request or sequence is not None
                                or action.get("main_navigation") is not True):
                            raise PermissionError("Agent form submission does not match an approved task")
                    else:
                        raise PermissionError("Unsupported owner-controlled navigation")
                    try:
                        request_body = base64.b64decode(action["body"], validate=True)
                    except (ValueError, TypeError):
                        raise PermissionError("Owner-controlled request body is invalid") from None
                    normalized = {"method": method, "url": action["url"],
                                  "headers": action["headers"], "body": request_body}
                    if method == "GET" and action.get("main_navigation") is True:
                        try:
                            validate_owner_get_action(action["url"], action["action_context"])
                        except (PermissionError, ValueError):
                            report_browser_action(
                                "This GET form, button, or unclear action was stopped before it reached the website."
                            )
                            return {"status": 204, "headers": {}, "body": b""}
                    response = active_mediator.retrieve_browser_owner(
                        normalized, job_id=identifier, owner_id=browser_owner_id, token=browser_token,
                        action_context=action["action_context"], authorized=consent_active,
                        cancelled=cancelled,
                        propose_action=propose_browser_action,
                        wait_action=wait_browser_action,
                        report_action=report_browser_action if owner_control else None,
                        before_send=lambda: publish(spool / f"{identifier}.skill-external", b"requested", replace=False),
                    )
                    browser_bytes += len(response["body"])
                    if browser_bytes > 8 * 1024 * 1024:
                        raise ValueError("Browser task resource limit exceeded")
                    return response
                validate_browser_url(action.get("url") if isinstance(action, dict) else None)
                # A read-only GET may still disclose request metadata, so record it before dispatch.
                publish(spool / f"{identifier}.skill-external", b"requested", replace=False)
                response = active_mediator.retrieve_browser(action, authorized=consent_active, cancelled=cancelled)
                browser_bytes += len(response["body"])
                if browser_bytes > 8 * 1024 * 1024:
                    raise ValueError("Browser task resource limit exceeded")
                return response

            def save_artifacts(files):
                if browser_handoff:
                    raise PermissionError("Artifacts are disabled during owner-controlled sign-in")
                if cancelled.is_set() or control.exists():
                    raise ValueError("Execution is no longer active")
                value = {"generation": request.get("_lifecycle_generation", 0), "files": files}
                publish(spool / f"{identifier}.skill-artifacts", json.dumps(value, ensure_ascii=True).encode("ascii"), replace=True)

            selected_image = browser_image if browser_runtime else image
            image_options = {"image": selected_image} if selected_image is not None else {}
            browser_options = {"browser_handler": send_browser} if browser_runtime else {}
            browser_search_failed = False
            if browser_search:
                try:
                    query = read_approved_search_query(spool, identifier, browser_owner_id, browser_token, cancelled)
                    if cancelled.is_set() or not consent_is_current(spool, identifier, browser_owner_id, browser_token):
                        raise PermissionError("Browser task approval expired")
                    publish(spool / f"{identifier}.skill-external", b"requested", replace=False)
                    urls = search_mwmbl_urls(
                        query, mediator=active_mediator,
                        authorized=lambda: (not cancelled.is_set() and not control.exists()
                                            and consent_is_current(spool, identifier, browser_owner_id, browser_token)),
                        cancelled=cancelled,
                    )
                    safe_urls = []
                    for url in urls:
                        try:
                            checked = validate_browser_url(url)
                        except (TypeError, ValueError):
                            continue
                        if checked not in safe_urls:
                            safe_urls.append(checked)
                    if not safe_urls:
                        raise SearchProviderUnavailable("Mwmbl returned no usable public pages.")
                    request["target_urls"] = safe_urls[:5]
                    request["browser_search"] = True
                except SearchProviderError as error:
                    browser_search_failed = True
                    result = {"status": "failed", "stage": "search", "reason": "search_provider_unavailable",
                              "message": str(error)[:1000]}
            try:
                if not browser_search_failed and request.get("_lifecycle_generation") and request.get("dependencies") and not wheels:
                    preparation = {**request, "operation": "prepare", "input": ""}
                    result = run_skill(preparation, job_id=identifier, timeout=180, cancelled=cancelled,
                                       package_handler=fetch_package, **image_options)
                    if result.get("status") == "complete" and not control.exists():
                        request["wheels"] = wheels
                        cancelled.clear()
                    else:
                        preparation = None
                elif not browser_search_failed:
                    preparation = True
                else:
                    preparation = None
                if preparation is not None:
                    browser_timeout = 240 if browser_search else 180
                    if (browser_runtime and not browser_search and not browser_handoff
                            and "action_request" not in request):
                        request["follow_public_links"] = True
                    result = run_skill(
                        request, job_id=identifier, timeout=browser_timeout, cancelled=cancelled,
                        network_handler=send_network,
                        **browser_options,
                        package_handler=fetch_package if request.get("operation") == "prepare" else None,
                        artifact_handler=save_artifacts if request.get("operation") == "run" or browser_session else None,
                        **image_options,
                    )
            finally:
                stop_watcher.set()
                watcher.join()
            if request.get("operation") == "prepare" and result.get("status") == "complete":
                publish(spool / f"{identifier}.skill-wheels", json.dumps(wheels).encode("ascii"), replace=True)
                result["wheel_cache"] = True
        except PermissionError:
            result = {"status": "failed", "stage": "execution", "reason": "browser_consent_expired"}
        except (OSError, ValueError):
            result = {"status": "failed", "stage": "preparation", "reason": "invalid_request"}
        handoff_cancelled = bool(browser_handoff and "cancelled" in locals() and "control" in locals()
                                 and (cancelled.is_set() or control.exists()))
        if handoff_cancelled:
            handoff_cleaned = True  # Cancellation/lifecycle owner already cleared the Django RAM registry.
            if "owner_sensitive_values" in locals():
                owner_sensitive_values.clear()
        if browser_handoff and "browser_handoff_call" in locals() and not locals().get("handoff_cleaned", False):
            try:
                cleaned = browser_handoff_call("cleanup")
                if cleaned.get("cleared") is not True:
                    raise PermissionError("Owner-control cleanup was not confirmed")
            except (OSError, PermissionError, ValueError):
                result = {"status": "failed", "stage": "execution", "reason": "cleanup_unverified"}
        if browser_handoff and "owner_sensitive_values" in locals():
            owner_sensitive_values.clear()
        if result.get("reason") == "cleanup_unverified":
            publish(outcome, b'{"status":"failed","reason":"cleanup_unverified"}', replace=True)
            raise RuntimeError("Cannot verify cleanup of skill container.")
        cleanup_network_requests(spool, identifier)
        if browser_runtime:
            consent_path(spool, identifier).unlink(missing_ok=True)
            cleanup_browser_session_files(spool, identifier)
        result["generation"] = request.get("_lifecycle_generation", 0)
        # Once the page answer is available, ending the owner-scoped live session
        # must not turn that successful answer into a timeout or cancellation.
        try:
            existing_result = json.loads(read_limited(outcome, 100 * 1024))
        except (OSError, ValueError):
            existing_result = None
        if not (browser_session and isinstance(existing_result, dict)
                and existing_result.get("status") in ("complete", "failed")
                and existing_result.get("_browser_session_answer") is True):
            publish(outcome, json.dumps(result, ensure_ascii=True).encode("ascii"), replace=True)
        running.unlink(missing_ok=True)
        if (spool / f"{identifier}.skill-control").exists():
            rescan = True
    return rescan


def validate_runtime():
    try:
        components = json.loads(subprocess.check_output(
            ["docker", "version", "--format", "{{json .Server.Components}}"],
            text=True, timeout=15,
        ))
        versions = {entry["Name"]: entry["Version"] for entry in components}
        check_component_versions(versions)
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Skill worker runtime check failed: {error}") from error


def recover_interrupted(spool):
    for running in spool.glob("*.skill-running"):
        identifier = job_id(running)
        if identifier is not None:
            if not remove_container(identifier):
                raise RuntimeError("Cannot verify cleanup of interrupted skill container.")
            cleanup_network_requests(spool, identifier)
            try:
                request = json.loads(read_limited(running, MAX_JOB_BYTES))
                generation = request.get("_lifecycle_generation", 0)
            except (OSError, ValueError, AttributeError):
                request = {}
                generation = 0
            cleanup_browser_handoff_bridge(identifier, request)
            consent_path(spool, identifier).unlink(missing_ok=True)
            cleanup_browser_session_files(spool, identifier)
            interrupted = {"status": "failed", "reason": "interrupted", "generation": generation}
            outcome = spool / f"{identifier}.skill-result"
            try:
                previous = json.loads(read_limited(outcome, 100 * 1024))
            except (OSError, ValueError):
                previous = {}
            cleanup_pending = isinstance(previous, dict) and previous.get("reason") == "cleanup_unverified"
            publish(outcome, json.dumps(interrupted).encode("ascii"), replace=cleanup_pending)
            running.unlink(missing_ok=True)
    for applying in spool.glob("*.skill-applying"):
        identifier = job_id(applying)
        if identifier is None:
            continue
        control = spool / f"{identifier}.skill-control"
        if control.exists():
            applying.unlink(missing_ok=True)
        else:
            applying.replace(control)


def supervise(spool, once, public_resources=DEFAULT_PUBLIC_RESOURCES, *, image=None, browser_image="superharry-skill-browser-worker:local"):
    validate_runtime()
    with lock_spool(spool):
        recover_interrupted(spool)
        while True:
            process_queue(spool, runtime_check=validate_runtime, public_resources=public_resources, image=image,
                          browser_image=browser_image)
            if once:
                return
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--browser-image", default="superharry-skill-browser-worker:local")
    parser.add_argument("--public-resource", action="append", default=[],
                        help="An exact, independently verified public HTTPS URL eligible for retrieval without approval; may be repeated.")
    args = parser.parse_args()
    resources = set(DEFAULT_PUBLIC_RESOURCES)
    for resource in args.public_resource:
        url = urlsplit(resource)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.fragment):
            parser.error("Public resources must be exact HTTPS URLs without credentials or fragments.")
        resources.add(resource)
    supervise(args.spool.resolve(), args.once, public_resources=resources, browser_image=args.browser_image)
