import hashlib
import ipaddress

from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    remote_address = (request.META.get("REMOTE_ADDR") or "").strip()
    candidate = remote_address
    if (
        getattr(settings, "TRUST_X_REAL_IP", False)
        and remote_address in {"", "127.0.0.1", "::1"}
    ):
        candidate = (request.META.get("HTTP_X_REAL_IP") or remote_address).strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _login_keys(*, request, identifier):
    ip = client_ip(request) or "unknown"
    normalized_identifier = (identifier or "").strip().casefold()
    identifier_hash = hashlib.sha256(normalized_identifier.encode("utf-8")).hexdigest()
    ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    return (
        f"login-failures:ip:{ip_hash}",
        f"login-failures:identifier:{identifier_hash}",
    )


def login_is_rate_limited(*, request, identifier):
    limit = settings.LOGIN_RATE_LIMIT_ATTEMPTS
    keys = _login_keys(request=request, identifier=identifier)
    return any((cache.get(key) or 0) >= limit for key in keys)


def record_login_failure(*, request, identifier):
    timeout = settings.LOGIN_RATE_LIMIT_WINDOW
    for key in _login_keys(request=request, identifier=identifier):
        if cache.add(key, 1, timeout=timeout):
            continue
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=timeout)


def clear_login_failures(*, request, identifier):
    cache.delete_many(_login_keys(request=request, identifier=identifier))
