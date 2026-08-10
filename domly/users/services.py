import logging
import secrets

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def send_verification_code(phone, code):
    """Development SMS adapter. Replace this function when an SMS provider is chosen."""
    if not settings.DEBUG:
        raise ImproperlyConfigured("SMS provider is not configured.")
    logger.warning("Development SMS code for %s: %s", phone, code)
