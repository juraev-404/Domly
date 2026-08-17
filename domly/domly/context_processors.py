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


def seo(request):
    language = (get_language() or "ru").split("-")[0]
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
    }
