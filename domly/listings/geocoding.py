import hashlib
import json
import math
import re
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache


class GeocodingRateLimited(Exception):
    pass


class GeocodingUnavailable(Exception):
    pass


_PROVIDER_RATE_LIMIT_KEY = "geocode:nominatim:request"
_POSITIVE_CACHE_TIMEOUT = 60 * 60 * 24 * 30
_NEGATIVE_CACHE_TIMEOUT = 60 * 5
_MAX_CITY_DISTANCE_KM = 35
_STREET_PREFIX_RE = re.compile(
    r"^\s*(?:(?:улица|ул\.?|проспект|пр[-\s]?т\.?|street|st\.?)|"
    r"(?:к[ӯу]ча(?:и)?|хи[её]бон(?:и)?))\s+",
    re.IGNORECASE,
)
_TRAILING_HOUSE_RE = re.compile(
    r"(?:\s*,\s*|\s+)(?:(?:дом|д\.?)\s*)?\d+[\w/-]*\s*$",
    re.IGNORECASE,
)
_FOLD_TRANSLATION = str.maketrans(
    {
        "ҷ": "дж",
        "ӣ": "и",
        "ӯ": "у",
        "ҳ": "х",
        "қ": "к",
        "ғ": "г",
        "ъ": "",
    }
)


def _fold_text(value):
    return re.sub(
        r"[^a-zа-я0-9]+",
        " ",
        value.casefold().translate(_FOLD_TRANSLATION),
    ).strip()


def _search_queries(*, city, address):
    raw_address = " ".join(address.strip().split())
    street_name = _STREET_PREFIX_RE.sub("", raw_address)
    street_name = _TRAILING_HOUSE_RE.sub("", street_name).strip(" ,")

    candidates = [
        f"{raw_address}, {city.name}, Таджикистан",
        f"{street_name}, {city.name}",
    ]
    words = street_name.split()
    if len(words) > 1:
        candidates.append(f"{words[-1]}, {city.name}")

    unique = []
    seen = set()
    for candidate in candidates:
        folded = candidate.casefold()
        if candidate and folded not in seen:
            unique.append(candidate)
            seen.add(folded)
    return unique


def _distance_km(latitude_a, longitude_a, latitude_b, longitude_b):
    latitude_a, longitude_a, latitude_b, longitude_b = map(
        math.radians,
        (latitude_a, longitude_a, latitude_b, longitude_b),
    )
    latitude_delta = latitude_b - latitude_a
    longitude_delta = longitude_b - longitude_a
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_a)
        * math.cos(latitude_b)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(haversine))


def _reserve_provider_request(*, wait):
    if cache.add(_PROVIDER_RATE_LIMIT_KEY, True, timeout=1):
        return
    if not wait:
        raise GeocodingRateLimited

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if cache.add(_PROVIDER_RATE_LIMIT_KEY, True, timeout=1):
            return
    raise GeocodingRateLimited


def _provider_search(*, city, query, wait):
    _reserve_provider_request(wait=wait)
    latitude = float(city.latitude)
    longitude = float(city.longitude)
    viewbox_radius = 0.25
    params = urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "addressdetails": 1,
            "limit": 5,
            "countrycodes": "tj",
            "accept-language": "ru,tg,en",
            "layer": "address",
            "viewbox": (
                f"{longitude - viewbox_radius},{latitude + viewbox_radius},"
                f"{longitude + viewbox_radius},{latitude - viewbox_radius}"
            ),
        }
    )
    request = Request(
        f"{settings.GEOCODER_URL}?{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": settings.GEOCODER_USER_AGENT,
        },
    )
    try:
        with urlopen(request, timeout=settings.GEOCODER_TIMEOUT) as response:
            payload = json.loads(response.read(262_144).decode("utf-8"))
    except (OSError, TimeoutError, URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GeocodingUnavailable from error
    if not isinstance(payload, list):
        raise GeocodingUnavailable
    return payload


def _select_result(*, city, payload, search_term):
    city_latitude = float(city.latitude)
    city_longitude = float(city.longitude)
    city_name = _fold_text(city.name)
    search_tokens = {
        token
        for token in _fold_text(search_term).split()
        if len(token) > 2 and not token.isdigit()
    }
    ranked = []

    for item in payload:
        try:
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue

        distance = _distance_km(
            city_latitude,
            city_longitude,
            latitude,
            longitude,
        )
        if distance > _MAX_CITY_DISTANCE_KM:
            continue

        display_name = str(item.get("display_name", search_term))
        folded_display_name = _fold_text(display_name)
        address_details = item.get("address")
        if not isinstance(address_details, dict):
            address_details = {}
        locality = " ".join(
            str(address_details.get(key, ""))
            for key in (
                "city",
                "town",
                "municipality",
                "county",
                "village",
                "city_district",
            )
        )
        folded_locality = _fold_text(locality)

        score = -distance
        score += sum(8 for token in search_tokens if token in folded_display_name)
        if city_name and (city_name in folded_locality or city_name in folded_display_name):
            score += 50
        if item.get("class") == "highway" or item.get("addresstype") in {
            "road",
            "residential",
            "street",
        }:
            score += 20
        ranked.append((score, latitude, longitude, display_name))

    if not ranked:
        return None
    _, latitude, longitude, display_name = max(
        ranked,
        key=lambda candidate: candidate[0],
    )
    return {
        "latitude": latitude,
        "longitude": longitude,
        "display_name": display_name,
    }


def geocode_address(*, city, address):
    cache_source = f"v2:{city.pk}:{address.strip()}"
    cache_key = "geocode:" + hashlib.sha256(
        cache_source.casefold().encode("utf-8")
    ).hexdigest()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached.get("result")

    result = None
    for index, query in enumerate(_search_queries(city=city, address=address)):
        payload = _provider_search(city=city, query=query, wait=index > 0)
        result = _select_result(city=city, payload=payload, search_term=query)
        if result is not None:
            break

    cache.set(
        cache_key,
        {"result": result},
        timeout=(
            _POSITIVE_CACHE_TIMEOUT
            if result is not None
            else _NEGATIVE_CACHE_TIMEOUT
        ),
    )
    return result
