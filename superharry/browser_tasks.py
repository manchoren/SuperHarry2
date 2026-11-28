"""Owner- and task-scoped consent for mediated read-only browser runs."""
import hmac
import json
import re
import time
from urllib.parse import parse_qsl, unquote, urlsplit


_SENSITIVE_ROUTE = re.compile(
    r"(?:^|[^a-z0-9])(pay|payment|payments|checkout|billing|purchase|order|card|delete|remove|update|edit|create|submit|confirm|commit|execute|process|save|apply|complete|activate|deactivate|cancel|subscribe|unsubscribe|transfer|refund|redeem|reset)(?:$|[^a-z0-9])",
    re.I,
)
_SENSITIVE_PARAMETER = re.compile(r"(?:payment|checkout|billing|purchase|card|ccnumber|cvv|cvc|delete|remove|update|submit|confirm|activate|cancel|transfer|refund|redeem)", re.I)
_MUTATING_ACTION = re.compile(r"^(delete|remove|update|edit|create|submit|confirm|activate|deactivate|cancel|subscribe|unsubscribe|transfer|refund|redeem|reset|purchase|pay)$", re.I)
_PROHIBITED_MONEY_ACTIVITY = re.compile(
    r"(?:^|[^a-z0-9])(pay(?:ment)?s?|checkout|billing|purchase|orders?|credit|debit|card|ccnumber|cvv|cvc|securitycode|transfer|refund|redeem|donat(?:e|ion)|subscription|wallet|iban|swift|routing|bank|wire|amount|total|currency|price)(?:$|[^a-z0-9])",
    re.I,
)
_SECRET_ACTION_FIELD = re.compile(r"(?:password|passwd|secret|token|session|otp|one.?time|verification.?code|security.?code|ccnumber|cvv|cvc|card|payment|subscription|billing|wallet|bank)", re.I)
_CSRF_FIELD = re.compile(r"^(?:csrf(?:middleware)?token|csrf_[a-z0-9_]+|authenticity_token|_token)$", re.I)
_CARD_NUMBER_CANDIDATE = re.compile(r"(?<!\d)(?:\d[\W_]{0,3}){11,18}\d(?!\d)")
_FINANCIAL_FIELD_MARKERS = (
    "amount", "total", "fee", "price", "invoice", "balance", "due", "subtotal",
    "tax", "quantity", "qty", "order", "method", "charge", "funding", "payout",
    "credit", "debit", "loan", "commission",
)
_LOGIN_ACTIONS = {"login", "signin", "sign-in", "authenticate", "auth"}


def semantic_action_text(value):
    """Split camelCase names before applying word-boundary safety rules."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)


def has_card_number_candidate(value):
    return bool(_CARD_NUMBER_CANDIDATE.search(value))


def has_sensitive_payment_field(value):
    compact = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(marker in compact for marker in (
        "pay", "billing", "checkout", "purchase", "subscription", "wallet", "bank",
        "card", "ccnumber", "cvv", "cvc", "storedmethod", "methodid",
    ))


def has_financial_field(value):
    """Reject financial field names even when sites concatenate their words."""
    compact = re.sub(r"[^a-z0-9]", "", semantic_action_text(value).casefold())
    return any(marker in compact for marker in _FINANCIAL_FIELD_MARKERS)


def validate_owner_get_action(url, context):
    """Allow owner GET navigation, but fail closed on visible form/button actions."""
    validate_browser_handoff_url(url)
    parsed_url = urlsplit(url)
    try:
        destination_text = " ".join((
            _decode_url_component(parsed_url.path),
            *(_decode_url_component(part) for part in parsed_url.query.split("&")),
        ))
    except ValueError:
        raise PermissionError("The owner-controlled destination could not be checked safely.") from None
    if (_PROHIBITED_MONEY_ACTIVITY.search(semantic_action_text(destination_text))
            or has_sensitive_payment_field(destination_text)
            or has_card_number_candidate(destination_text)):
        raise PermissionError("Payment, card, and account-action destinations are blocked.")
    if context is None:
        return True
    get_context_keys = {"kind", "summary", "form_text", "visible_fields"}
    if (not isinstance(context, dict)
            or not {"kind", "summary", "form_text"}.issubset(context)
            or not set(context).issubset(get_context_keys)
            or context["kind"] not in {"none", "link", "form", "button", "other"}
            or not isinstance(context["summary"], str) or len(context["summary"]) > 300
            or not isinstance(context["form_text"], str) or len(context["form_text"]) > 2000
            or "visible_fields" in context and (not isinstance(context["visible_fields"], list)
                or len(context["visible_fields"]) > 32
                or any(not isinstance(name, str) or len(name) > 100 for name in context["visible_fields"]))):
        raise PermissionError("The owner-controlled GET action could not be checked safely.")
    if context["kind"] in {"form", "button"}:
        raise PermissionError("GET form and button actions are stopped because their effects cannot be reviewed safely.")
    description = semantic_action_text(" ".join((context["summary"], context["form_text"])))
    if (_PROHIBITED_MONEY_ACTIVITY.search(description) or has_sensitive_payment_field(description)
            or has_card_number_candidate(description)):
        raise PermissionError("Payment, card, and unclear account actions are blocked.")
    return True


def _decode_url_component(component):
    try:
        for _ in range(3):
            decoded = unquote(component, errors="strict")
            if decoded == component:
                break
            component = decoded
        if re.search(r"%[0-9a-fA-F]{2}", component):
            raise ValueError
        return component
    except (UnicodeDecodeError, ValueError):
        raise ValueError("Enter one valid public HTTP(S) URL.") from None


def validate_browser_url(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Enter one valid public HTTP(S) URL.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Enter one valid public HTTP(S) URL.") from None
    expected_port = 443 if parsed.scheme == "https" else 80
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or port == 0 or port is not None and port != expected_port):
        raise ValueError("Enter one valid public HTTP(S) URL.")
    if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path + parsed.query):
        raise ValueError("Enter one valid public HTTP(S) URL.") from None
    decoded_path = _decode_url_component(parsed.path)
    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    parameters = [(_decode_url_component(key), _decode_url_component(item)) for key, item in parameters]
    if (_SENSITIVE_ROUTE.search(decoded_path)
            or has_financial_field(decoded_path)
            or any(_SENSITIVE_PARAMETER.search(key) or key.casefold() == "action" and _MUTATING_ACTION.fullmatch(value)
                   or has_financial_field(key) or has_financial_field(value)
                   or has_card_number_candidate(value)
                   for key, value in parameters)):
        raise ValueError("Payment, card, and account-action URLs are not available in read-only browsing.")
    return value


def validate_browser_handoff_url(value):
    """Require encrypted transport for any task that accepts owner-entered credentials."""
    validated = validate_browser_url(value)
    if urlsplit(validated).scheme != "https":
        raise ValueError("Owner-controlled sign-in requires an HTTPS URL.")
    return validated


def validate_owner_login_submission(url, body, content_type):
    """Accept only a bounded native URL-encoded login form submission.

    The caller must also prove that this main-frame POST directly follows a
    current owner-control click/key event. This validator is a second fail-
    closed guard, not a classifier for arbitrary external actions.
    """
    validate_browser_handoff_url(url)
    if (not isinstance(body, bytes) or len(body) > 64 * 1024
            or not isinstance(content_type, str)
            or content_type.split(";", 1)[0].strip().casefold() != "application/x-www-form-urlencoded"):
        raise ValueError("Only bounded native form login requests are available during browser control.")
    try:
        fields = parse_qsl(body.decode("utf-8", errors="strict"), keep_blank_values=True,
                           max_num_fields=128, strict_parsing=False)
    except (UnicodeDecodeError, ValueError):
        raise ValueError("The login form could not be checked safely.") from None
    if not fields:
        raise ValueError("The login form could not be checked safely.")
    names = set()
    for key, value in fields:
        decoded_key = _decode_url_component(key).casefold()
        decoded_value = _decode_url_component(value)
        semantic_value = semantic_action_text(decoded_value)
        if (_SENSITIVE_PARAMETER.search(decoded_key)
                or decoded_key == "action" and decoded_value.casefold() not in _LOGIN_ACTIONS
                or _PROHIBITED_MONEY_ACTIVITY.search(semantic_value)
                or has_sensitive_payment_field(decoded_value)
                or has_card_number_candidate(decoded_value)):
            raise PermissionError("Payment, card, and account-action requests are blocked.")
        names.add(decoded_key)
    identity = {"username", "user", "email", "login", "userid", "user_id"}
    password = {"password", "passwd", "pass", "pwd"}
    auxiliary = {"csrf", "csrfmiddlewaretoken", "authenticity_token", "_token", "token", "action"}
    if (not names.intersection(identity) or not names.intersection(password)
            or any(name not in identity | password | auxiliary and not name.startswith("csrf_") for name in names)):
        raise PermissionError("Only a recognizable username and password sign-in form is available.")
    return True


def validate_browser_action(action, context):
    """Describe one bounded, safe-to-review native form action.

    This does not approve the request. The caller must bind this description
    to the exact request digest and wait for a one-use owner decision.
    """
    if (not isinstance(action, dict) or set(action) != {"method", "url", "headers", "body"}
            or action.get("method") != "POST" or not isinstance(action.get("url"), str)
            or not isinstance(action.get("headers"), dict) or not isinstance(action.get("body"), bytes)
            or len(action["body"]) > 64 * 1024):
        raise ValueError("Only a bounded native form submission can be reviewed.")
    try:
        parsed = urlsplit(action["url"])
        port = parsed.port
    except ValueError:
        raise ValueError("The action destination is invalid.") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or port == 0
            or port is not None and port != 443
            or re.search(r"%(?![0-9a-fA-F]{2})", parsed.path + parsed.query)):
        raise ValueError("Actions require a public HTTPS destination on the standard port.")
    try:
        decoded_destination = " ".join((
            _decode_url_component(parsed.path),
            *(_decode_url_component(item) for item in parsed.query.split("&")),
        ))
    except ValueError:
        raise ValueError("The action destination could not be checked safely.") from None
    if (_PROHIBITED_MONEY_ACTIVITY.search(semantic_action_text(decoded_destination))
            or has_financial_field(decoded_destination)
            or has_card_number_candidate(decoded_destination)):
        raise PermissionError("Payments, purchases, transfers, and card-related actions are blocked.")

    headers = {name.casefold(): value for name, value in action["headers"].items()
               if isinstance(name, str) and isinstance(value, str)}
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().casefold()
    if content_type != "application/x-www-form-urlencoded":
        raise PermissionError("Only native URL-encoded form actions can be reviewed.")
    try:
        pairs = parse_qsl(action["body"].decode("utf-8", errors="strict"), keep_blank_values=True,
                          max_num_fields=32, strict_parsing=True)
    except (UnicodeDecodeError, ValueError):
        raise PermissionError("The form data could not be checked safely.") from None
    if not pairs:
        raise PermissionError("The form action is unclear and was stopped.")

    context_keys = {"summary", "form_text", "kind", "visible_fields", "expected_url"}
    if (not isinstance(context, dict)
            or not {"summary", "form_text", "visible_fields"}.issubset(context)
            or not set(context).issubset(context_keys)
            or context.get("kind", "form") != "form"
            or not isinstance(context["summary"], str) or not context["summary"].strip()
            or len(context["summary"]) > 300 or not isinstance(context["form_text"], str)
            or len(context["form_text"]) > 2000
            or not isinstance(context["visible_fields"], list) or not 1 <= len(context["visible_fields"]) <= 32
            or any(not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_.:\[\]-]{1,100}", name) is None
                   for name in context["visible_fields"])
            or len({name.casefold() for name in context["visible_fields"]}) != len(context["visible_fields"])
            or ("expected_url" in context
                and (not isinstance(context["expected_url"], str) or context["expected_url"] != action["url"]))):
        raise PermissionError("The form effect could not be described safely.")
    summary = context["summary"].strip()
    context_text = semantic_action_text(" ".join((summary, context["form_text"])))
    if _PROHIBITED_MONEY_ACTIVITY.search(context_text) or has_card_number_candidate(context_text):
        raise PermissionError("Payments, purchases, transfers, and card-related actions are blocked.")

    display_fields = []
    visible_fields = {name.casefold() for name in context["visible_fields"]}
    submitted_fields = set()
    for key, value in pairs:
        decoded_key = _decode_url_component(key)
        normalized_key = decoded_key.casefold()
        semantic_key = semantic_action_text(decoded_key)
        normalized_value = _decode_url_component(value)
        csrf_field = bool(_CSRF_FIELD.fullmatch(normalized_key))
        if not csrf_field and (normalized_key not in visible_fields or normalized_key in submitted_fields):
            raise PermissionError("The form submitted a field that was not a single visible control.")
        if not csrf_field:
            submitted_fields.add(normalized_key)
        if not csrf_field and (_SECRET_ACTION_FIELD.search(semantic_key)
                               or has_sensitive_payment_field(semantic_key)
                               or has_financial_field(semantic_key)
                               or _PROHIBITED_MONEY_ACTIVITY.search(semantic_key)):
            raise PermissionError("Credential, code, payment, and card fields cannot be submitted as actions.")
        if (_PROHIBITED_MONEY_ACTIVITY.search(semantic_key)
                or _PROHIBITED_MONEY_ACTIVITY.search(normalized_value)
                or has_card_number_candidate(normalized_value)):
            raise PermissionError("Payments, purchases, transfers, and card-related actions are blocked.")
        if not csrf_field:
            display_fields.append({"name": normalized_key, "value": normalized_value[:1000]})
    if not display_fields or sum(len(item["name"]) + len(item["value"]) for item in display_fields) > 8000:
        raise PermissionError("The action details are not clear enough to review.")
    return {
        "method": "POST", "url": action["url"], "summary": summary,
        "form_text": context["form_text"], "fields": display_fields,
    }


def consent_path(directory, job_id):
    return directory / f"{job_id}.browser-consent"


def consent_is_current(directory, job_id, owner_id, token, *, now=None):
    if (type(owner_id) is not int or owner_id <= 0 or not isinstance(token, str)
            or not 32 <= len(token) <= 128):
        return False
    path = consent_path(directory, job_id)
    try:
        raw = path.read_bytes()
        if len(raw) > 2048:
            return False
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeError):
        return False
    if not isinstance(value, dict) or set(value) != {"job_id", "owner_id", "token", "expires_at"}:
        return False
    current_time = time.time() if now is None else now
    return bool(
        value["job_id"] == str(job_id)
        and type(value["owner_id"]) is int and value["owner_id"] == owner_id
        and isinstance(value["token"], str) and hmac.compare_digest(value["token"], token)
        and type(value["expires_at"]) in (int, float)
        and current_time < value["expires_at"] <= current_time + 300
    )
