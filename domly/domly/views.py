import logging

from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.urls import reverse
from django.views.decorators.http import require_GET, require_safe


logger = logging.getLogger(__name__)


@require_safe
def healthcheck(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        cache_key = "healthcheck"
        cache.set(cache_key, "ok", timeout=10)
        if cache.get(cache_key) != "ok":
            raise RuntimeError("Cache health check failed")
    except Exception:
        logger.exception("Domly health check failed")
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def robots_txt(request):
    disallowed_paths = (
        "/admin/",
        "/auth/",
        "/profile/",
        "/favorites/",
        "/notifications/",
        "/messages/",
        "/create/",
        "/moderation/",
        "/reports/",
        "/location/",
    )
    lines = ["User-agent: *", "Allow: /"]
    lines.extend(f"Disallow: {path}" for path in disallowed_paths)
    for language_code, _language_name in settings.LANGUAGES:
        if language_code == settings.LANGUAGE_CODE:
            continue
        lines.extend(
            f"Disallow: /{language_code}{path}" for path in disallowed_paths[1:]
        )
    lines.extend(("", f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}"))
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")
