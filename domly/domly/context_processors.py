from django.conf import settings
from django.urls import translate_url
from django.utils.translation import get_language, gettext as _


INDEXABLE_URL_NAMES = {
    "listing_list",
    "city_listings",
    "listing_detail",
    "help",
    "privacy_policy",
    "terms_of_use",
    "publication_rules",
}

OG_LOCALES = {
    "ru": "ru_RU",
    "tg": "tg_TJ",
    "en": "en_US",
}

LANGUAGE_NATIVE_NAMES = {
    "ru": "Русский",
    "tg": "Тоҷикӣ",
    "en": "English",
}


def seo(request):
    language = (get_language() or "ru").split("-")[0]
    current_url = request.get_full_path()
    language_switch_options = tuple(
        {
            "code": code,
            "label": LANGUAGE_NATIVE_NAMES.get(code, str(label)),
            "url": translate_url(current_url, code),
            "is_current": code == language,
        }
        for code, label in settings.LANGUAGES
    )
    url_name = getattr(request.resolver_match, "url_name", None)
    is_indexable = url_name in INDEXABLE_URL_NAMES
    alternate_language_urls = ()
    x_default_url = ""
    if is_indexable:
        alternate_language_urls = tuple(
            {
                "language": code,
                "url": request.build_absolute_uri(translate_url(request.path, code)),
            }
            for code, _label in settings.LANGUAGES
        )
        x_default_url = request.build_absolute_uri(
            translate_url(request.path, settings.LANGUAGE_CODE)
        )
    return {
        "canonical_url": request.build_absolute_uri(request.path),
        "seo_title": _("Domly — аренда и продажа недвижимости"),
        "seo_description": _(
            "Объявления об аренде и продаже недвижимости в Таджикистане. "
            "Ищите квартиры, дома, комнаты и участки напрямую от владельцев."
        ),
        "seo_robots": "index,follow" if is_indexable else "noindex,nofollow",
        "alternate_language_urls": alternate_language_urls,
        "x_default_url": x_default_url,
        "og_type": "website",
        "og_locale": OG_LOCALES.get(language, OG_LOCALES["ru"]),
        "og_locale_alternates": tuple(
            locale for code, locale in OG_LOCALES.items() if code != language
        ),
        "language_switch_options": language_switch_options,
    }
