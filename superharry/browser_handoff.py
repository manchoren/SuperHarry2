"""Volatile, task-scoped owner control for an isolated browser session.

This registry deliberately stores owner input and live frames only in process
memory. It is safe only with one Gunicorn worker process; threads share it.
"""
from __future__ import annotations

import hmac
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field


MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_QUEUED_EVENTS = 16
MAX_EVENT_TEXT = 1024
MAX_ACTIVE_HANDOFFS = 64
MAX_OWNER_EVENTS_PER_SESSION = 64


@dataclass
class Handoff:
    job_id: str
    owner_id: int
    token: str
    expires_at: float
    events: queue.Queue = field(default_factory=lambda: queue.Queue(MAX_QUEUED_EVENTS))
    lock: threading.Lock = field(default_factory=threading.Lock)
    frame: bytes | None = None
    owner_control: bool = False
    returned_control: bool = False
    next_sequence: int = 0
    interaction_count: int = 0
    active: bool = True
    action_proposal: dict | None = None
    action_decision_event: threading.Event = field(default_factory=threading.Event)
    action_notice: dict | None = None
    action_plan_started: bool = False


_lock = threading.RLock()
_sessions: dict[str, Handoff] = {}


def register(job_id, owner_id, token, expires_at):
    if type(owner_id) is not int or owner_id <= 0 or not isinstance(token, str) or len(token) < 32:
        raise ValueError("Invalid browser handoff identity")
    key = str(job_id)
    state = Handoff(key, owner_id, token, float(expires_at))
    with _lock:
        for expired_key, old_state in list(_sessions.items()):
            if time.time() >= old_state.expires_at or not old_state.active:
                _sessions.pop(expired_key, None)
                clear_state(old_state)
        old = _sessions.pop(key, None)
        if old is not None:
            clear_state(old)
        if len(_sessions) >= MAX_ACTIVE_HANDOFFS:
            raise RuntimeError("Browser handoff capacity is unavailable")
        _sessions[key] = state
    return state


def _state(job_id, owner_id=None):
    with _lock:
        state = _sessions.get(str(job_id))
    if state is None:
        return None
    if owner_id is not None and state.owner_id != owner_id:
        return None
    if not state.active or time.time() >= state.expires_at:
        clear(job_id)
        return None
    return state


def owner_state(job_id, owner_id):
    return _state(job_id, owner_id)


def authenticate(job_id, owner_id, token):
    state = _state(job_id, owner_id)
    if (state is None or not isinstance(token, str)
            or not hmac.compare_digest(state.token, token)):
        return None
    return state


def take_control(job_id, owner_id):
    state = _state(job_id, owner_id)
    if state is None:
        return False
    with state.lock:
        if state.owner_control:
            return True
        if state.returned_control:
            return False
        state.owner_control = True
        if _enqueue_locked(state, {"type": "take_control"}):
            return True
        state.owner_control = False
        return False


def return_control(job_id, owner_id):
    state = _state(job_id, owner_id)
    if state is None:
        return False
    with state.lock:
        if not state.owner_control:
            return False
        clear_events(state)
        if state.action_proposal is not None and state.action_proposal.get("decision") is None:
            state.action_proposal["decision"] = "deny"
            state.action_decision_event.set()
            state.action_notice = {"id": secrets.token_urlsafe(8), "message": "The pending action was rejected when browser control ended."}
        if not _enqueue_locked(state, {"type": "return_control"}):
            return False
        state.owner_control = False
        state.returned_control = True
        state.frame = None
        return True


def enqueue_owner_event(job_id, owner_id, event):
    state = _state(job_id, owner_id)
    if state is None or not isinstance(event, dict):
        return False
    with state.lock:
        if (not state.owner_control
                or state.interaction_count >= MAX_OWNER_EVENTS_PER_SESSION):
            return False
        if not _enqueue_locked(state, event):
            return False
        state.interaction_count += 1
        return True


def _enqueue_locked(state, event):
    state.next_sequence += 1
    item = {**event, "sequence": state.next_sequence}
    try:
        state.events.put_nowait(item)
        return True
    except queue.Full:
        state.next_sequence -= 1
        return False


def worker_poll(job_id, owner_id, token, timeout=2):
    state = authenticate(job_id, owner_id, token)
    if state is None:
        return None
    try:
        return state.events.get(timeout=max(0, min(float(timeout), 2.5)))
    except queue.Empty:
        return {"type": "idle"}


def publish_owner_frame(job_id, owner_id, token, image):
    state = authenticate(job_id, owner_id, token)
    if (state is None or not state.owner_control or not isinstance(image, bytes)
            or not 4 <= len(image) <= MAX_FRAME_BYTES or not image.startswith(b"\xff\xd8\xff")):
        return False
    with state.lock:
        if not state.owner_control or time.time() >= state.expires_at:
            return False
        state.frame = image
    return True


def owner_frame(job_id, owner_id):
    state = _state(job_id, owner_id)
    if state is None or not state.owner_control:
        return None
    with state.lock:
        return state.frame


def clear_events(state):
    while True:
        try:
            state.events.get_nowait()
        except queue.Empty:
            return


def clear_state(state):
    with state.lock:
        state.active = False
        state.owner_control = False
        state.frame = None
        if state.action_proposal is not None and state.action_proposal.get("decision") is None:
            state.action_proposal["decision"] = "deny"
        state.action_decision_event.set()
        state.action_proposal = None
        state.action_notice = None
        state.action_plan_started = False
        clear_events(state)
        state.token = ""


def propose_action(job_id, owner_id, token, *, digest, method, url, summary, form_text, fields, expires_at):
    """Hold one owner-reviewed browser action in volatile task state."""
    state = authenticate(job_id, owner_id, token)
    if (state is None or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or method not in {"POST", "PUT", "PATCH", "DELETE"}
            or not isinstance(url, str) or not 1 <= len(url) <= 4096
            or not isinstance(summary, str) or not 1 <= len(summary) <= 500
            or not isinstance(form_text, str) or len(form_text) > 2000
            or not isinstance(fields, list) or len(fields) > 32
            or type(expires_at) not in (int, float) or not time.time() < expires_at <= state.expires_at):
        return None
    safe_fields = []
    total = 0
    for item in fields:
        if (not isinstance(item, dict) or set(item) != {"name", "value"}
                or not isinstance(item["name"], str) or not 1 <= len(item["name"]) <= 100
                or not isinstance(item["value"], str) or len(item["value"]) > 1000):
            return None
        total += len(item["name"]) + len(item["value"])
        if total > 8000:
            return None
        safe_fields.append({"name": item["name"], "value": item["value"]})
    with state.lock:
        if not state.owner_control or state.action_proposal is not None:
            return None
        proposal = {
            "id": secrets.token_urlsafe(18), "digest": digest, "method": method,
            "url": url, "summary": summary, "form_text": form_text, "fields": safe_fields,
            "expires_at": float(expires_at), "decision": None,
        }
        state.action_decision_event.clear()
        state.action_proposal = proposal
        state.action_notice = {"id": secrets.token_urlsafe(8), "message": "A website form is waiting for your review."}
        return {key: value for key, value in proposal.items() if key != "decision"}


def action_proposal(job_id, owner_id):
    state = _state(job_id, owner_id)
    if state is None:
        return None
    with state.lock:
        proposal = state.action_proposal
        if (not state.owner_control or proposal is None or proposal.get("decision") is not None
                or time.time() >= proposal["expires_at"]):
            return None
        return {key: value for key, value in proposal.items() if key != "decision"}


def action_status(job_id, owner_id):
    state = _state(job_id, owner_id)
    if state is None:
        return None
    with state.lock:
        return dict(state.action_notice) if state.owner_control and state.action_notice else None


def report_action_status(job_id, owner_id, message):
    state = _state(job_id, owner_id)
    if state is None or not isinstance(message, str) or not 1 <= len(message) <= 300:
        return False
    with state.lock:
        if not state.owner_control:
            return False
        state.action_notice = {"id": secrets.token_urlsafe(8), "message": message}
        return True


def claim_action_plan(job_id, owner_id):
    """Allow one agent planning request per owner handoff session."""
    state = _state(job_id, owner_id)
    if state is None:
        return False
    with state.lock:
        if not state.owner_control or state.action_plan_started:
            return False
        state.action_plan_started = True
        return True


def decide_action(job_id, owner_id, proposal_id, digest, decision):
    state = _state(job_id, owner_id)
    if state is None or decision not in {"approve", "deny"}:
        return False
    with state.lock:
        proposal = state.action_proposal
        if (not state.owner_control or proposal is None or proposal.get("decision") is not None
                or time.time() >= proposal["expires_at"] or not isinstance(proposal_id, str)
                or not isinstance(digest, str) or not hmac.compare_digest(proposal["id"], proposal_id)
                or not hmac.compare_digest(proposal["digest"], digest)):
            return False
        proposal["decision"] = decision
        state.action_notice = {"id": secrets.token_urlsafe(8), "message": (
            "This exact request was approved and will be sent once. Check the page for the result; this app cannot undo a site change."
            if decision == "approve" else "This request was rejected and will not be sent."
        )}
        state.action_decision_event.set()
        return True


def take_action_decision(job_id, owner_id, proposal_id, digest):
    """Consume a decision once when the host worker polls the Django bridge."""
    state = _state(job_id, owner_id)
    if state is None:
        return None
    with state.lock:
        proposal = state.action_proposal
        if (not state.owner_control or proposal is None or proposal.get("decision") is None
                or time.time() >= proposal["expires_at"] or not isinstance(proposal_id, str)
                or not isinstance(digest, str) or not hmac.compare_digest(proposal["id"], proposal_id)
                or not hmac.compare_digest(proposal["digest"], digest)):
            return None
        decision = proposal["decision"]
        state.action_proposal = None
        state.action_decision_event.clear()
        return decision


def wait_action_decision(job_id, owner_id, token, proposal_id, digest, *, timeout=60, authorized=None, cancelled=None):
    """Return one exact owner decision; fail closed on timeout, expiry, or task end."""
    state = authenticate(job_id, owner_id, token)
    if state is None or type(timeout) not in (int, float) or timeout <= 0:
        return None
    deadline = min(time.monotonic() + min(float(timeout), 120), state.expires_at)
    while time.monotonic() < deadline:
        if (cancelled is not None and cancelled.is_set()
                or authorized is not None and not authorized()):
            return None
        with state.lock:
            proposal = state.action_proposal
            if (not state.active or not state.owner_control or proposal is None
                    or time.time() >= proposal["expires_at"]
                    or not hmac.compare_digest(proposal["id"], str(proposal_id))
                    or not hmac.compare_digest(proposal["digest"], str(digest))):
                return None
            decision = proposal.get("decision")
            if decision is not None:
                state.action_proposal = None
                state.action_decision_event.clear()
                return decision
        state.action_decision_event.wait(min(0.2, max(0, deadline - time.monotonic())))
    with state.lock:
        proposal = state.action_proposal
        if proposal is not None and proposal.get("id") == proposal_id:
            state.action_proposal = None
            state.action_decision_event.clear()
    return None


def clear(job_id):
    with _lock:
        state = _sessions.pop(str(job_id), None)
    if state is not None:
        clear_state(state)


def validate_owner_event(event):
    if not isinstance(event, dict) or "type" not in event:
        return None
    kind = event.get("type")
    if kind == "click" and set(event) == {"type", "x", "y"}:
        x, y = event["x"], event["y"]
        if (type(x) is int and type(y) is int and 0 <= x < 1280 and 0 <= y < 800):
            return {"type": kind, "x": x, "y": y}
    if kind == "type" and set(event) == {"type", "text"}:
        text = event["text"]
        if isinstance(text, str) and 0 < len(text) <= MAX_EVENT_TEXT and not any(
            ord(char) < 32 and char not in "\t\n\r" or ord(char) == 127 for char in text
        ):
            return {"type": kind, "text": text}
    if kind == "key" and set(event) == {"type", "key"} and event["key"] in {
        "Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown",
        "ArrowLeft", "ArrowRight", "Home", "End",
    }:
        return {"type": kind, "key": event["key"]}
    return None
