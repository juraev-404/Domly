import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from django.db.models import QuerySet

from .models import Listing


PROPERTY_SYNONYMS = {
    Listing.PropertyType.APARTMENT: {
        "квартира", "квартиру", "квартиры", "квартире", "кв", "апартаменты",
        "apartment", "apartments", "apartament", "flat", "kvartira",
    },
    Listing.PropertyType.HOUSE: {
        "дом", "дома", "доме", "коттедж", "house", "home", "dom", "хона", "khona", "ҳавлӣ", "havli",
    },
    Listing.PropertyType.ROOM: {
        "комната", "комнату", "комнаты", "room", "komnata", "ҳуҷра", "hujra",
    },
    Listing.PropertyType.LAND: {
        "участок", "земля", "землю", "land", "plot", "uchastok",
    },
    Listing.PropertyType.COMMERCIAL: {
        "коммерция", "коммерческая", "офис", "склад", "магазин", "commercial", "office", "warehouse",
    },
}

DEAL_SYNONYMS = {
    Listing.DealType.RENT: {
        "аренда", "арендовать", "снять", "сниму", "сдается", "сдаётся", "rent", "rental", "ijora", "иҷора",
    },
    Listing.DealType.SALE: {
        "продажа", "продается", "продаётся", "купить", "покупка", "sale", "buy", "furush", "фурӯш",
    },
}

CURRENCY_SYNONYMS = {
    Listing.Currency.TJS: {"сомони", "tjs", "смн"},
    Listing.Currency.USD: {"доллар", "доллара", "долларов", "usd", "dollar", "dollars"},
}

ROOM_WORDS = {
    "однушка": 1, "однокомнатная": 1, "студия": 1, "studio": 1,
    "двушка": 2, "двухкомнатная": 2, "двухкомнатную": 2,
    "трешка": 3, "трёшка": 3, "трехкомнатная": 3, "трёхкомнатная": 3,
    "четырехкомнатная": 4, "четырёхкомнатная": 4,
}

STOP_WORDS = {
    "в", "на", "и", "или", "с", "для", "по", "из", "у", "около", "рядом",
    "the", "a", "an", "in", "at", "with", "for", "near",
    "дар", "бо", "ва", "барои",
    "до", "от", "не", "дороже", "дешевле", "цена", "стоимость",
    "комната", "комнаты", "комнат", "комнатная", "комнатную",
    "м2", "м²", "квм", "сомони", "доллар", "долларов", "tjs", "usd",
}

CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ғ": "gh", "ӣ": "i", "қ": "q", "ӯ": "u", "ҳ": "h", "ҷ": "j",
})


@dataclass(frozen=True)
class SearchIntent:
    raw_query: str
    text_tokens: tuple[str, ...]
    deal_type: str | None = None
    property_type: str | None = None
    city_name: str | None = None
    rooms: int | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    currency: str | None = None


def normalize_text(value):
    value = unicodedata.normalize("NFKC", value or "").lower().replace("ё", "е")
    return re.sub(r"[^\w\s.,-]", " ", value, flags=re.UNICODE)


def transliterate(value):
    return normalize_text(value).translate(CYRILLIC_TO_LATIN)


def _tokens(value):
    return tuple(re.findall(r"[\w]+", normalize_text(value), flags=re.UNICODE))


def _fuzzy_choice(token, groups, threshold=0.8):
    best = (0.0, None)
    for canonical, synonyms in groups.items():
        for synonym in synonyms:
            normalized_synonym = normalize_text(synonym)
            latin_synonym = transliterate(synonym)
            if token in {normalized_synonym, latin_synonym}:
                return canonical
            if len(token) < 4:
                continue
            ratio = SequenceMatcher(None, token, normalized_synonym).ratio()
            ratio = max(ratio, SequenceMatcher(None, token, latin_synonym).ratio())
            if ratio > best[0]:
                best = (ratio, canonical)
    if token and best[0] >= threshold:
        return best[1]
    return None


def _parse_decimal(value):
    try:
        return Decimal(value.replace(" ", "").replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None


def parse_search_query(query, city_names=()):
    normalized = normalize_text(query)
    tokens = list(_tokens(normalized))
    consumed = set()
    deal_type = property_type = currency = city_name = None
    rooms = None

    for token in tokens:
        if token in ROOM_WORDS:
            rooms = ROOM_WORDS[token]
            property_type = Listing.PropertyType.APARTMENT
            consumed.add(token)
            continue
        property_match = _fuzzy_choice(token, PROPERTY_SYNONYMS)
        if property_match:
            property_type = property_match
            consumed.add(token)
        deal_match = _fuzzy_choice(token, DEAL_SYNONYMS)
        if deal_match:
            deal_type = deal_match
            consumed.add(token)
        currency_match = _fuzzy_choice(token, CURRENCY_SYNONYMS, threshold=0.84)
        if currency_match:
            currency = currency_match
            consumed.add(token)

    room_match = re.search(r"\b([1-9])\s*[- ]?(?:комнат\w*|room\w*|ҳуҷра\w*|hujra\w*)", normalized)
    if room_match:
        rooms = int(room_match.group(1))
        property_type = property_type or Listing.PropertyType.APARTMENT
        consumed.add(room_match.group(1))

    for candidate in city_names:
        city_variants = (normalize_text(candidate), transliterate(candidate))
        for token in tokens:
            if any(
                token == variant
                or (len(token) >= 4 and SequenceMatcher(None, token, variant).ratio() >= 0.76)
                for variant in city_variants
            ):
                city_name = candidate
                consumed.add(token)
                break
        if city_name:
            break

    min_price = max_price = None
    between = re.search(r"\b(\d[\d ]{2,})\s*[-–]\s*(\d[\d ]{2,})\b", normalized)
    if between:
        min_price, max_price = _parse_decimal(between.group(1)), _parse_decimal(between.group(2))
        consumed.update(_tokens(between.group(0)))
    upper = re.search(r"(?:до|не\s+дороже|under|max)\s*(\d[\d ]*(?:[.,]\d+)?)", normalized)
    lower = re.search(r"(?:от|не\s+дешевле|from|min)\s*(\d[\d ]*(?:[.,]\d+)?)", normalized)
    if upper:
        max_price = _parse_decimal(upper.group(1))
        consumed.update(_tokens(upper.group(0)))
    if lower:
        min_price = _parse_decimal(lower.group(1))
        consumed.update(_tokens(lower.group(0)))

    text_tokens = tuple(
        token for token in tokens
        if token not in consumed and token not in STOP_WORDS and not token.isdigit() and len(token) > 1
    )
    return SearchIntent(
        raw_query=query,
        text_tokens=text_tokens,
        deal_type=deal_type,
        property_type=property_type,
        city_name=city_name,
        rooms=rooms,
        min_price=min_price,
        max_price=max_price,
        currency=currency,
    )


def apply_intent_filters(queryset: QuerySet, intent: SearchIntent):
    if intent.deal_type:
        queryset = queryset.filter(deal_type=intent.deal_type)
    if intent.property_type:
        queryset = queryset.filter(property_type=intent.property_type)
    if intent.rooms:
        queryset = queryset.filter(rooms=intent.rooms)
    if intent.min_price is not None:
        queryset = queryset.filter(price__gte=intent.min_price)
    if intent.max_price is not None:
        queryset = queryset.filter(price__lte=intent.max_price)
    if intent.currency:
        queryset = queryset.filter(currency=intent.currency)
    return queryset


def _field_score(token, value, weight):
    normalized = normalize_text(value)
    latin = transliterate(value)
    if token in normalized or token in latin:
        return weight
    words = _tokens(normalized) + _tokens(latin)
    best = max((SequenceMatcher(None, token, word).ratio() for word in words), default=0)
    threshold = 0.72 if len(token) >= 5 else 0.82
    return weight * best if best >= threshold else 0


def rank_search_results(listings, intent, sort="relevance"):
    matched = []
    raw_phrase = normalize_text(intent.raw_query)
    for listing in listings:
        score = 0.0
        matched_tokens = 0
        for token in intent.text_tokens:
            token_score = max(
                _field_score(token, listing.title, 8),
                _field_score(token, listing.address, 5),
                _field_score(token, listing.description, 2.5),
                _field_score(token, listing.city.name, 4),
            )
            if token_score:
                matched_tokens += 1
                score += token_score
        if raw_phrase and raw_phrase in normalize_text(listing.title):
            score += 12
        if intent.text_tokens and matched_tokens < max(1, (len(intent.text_tokens) + 1) // 2):
            continue
        listing.search_score = round(score, 3)
        matched.append(listing)

    if sort == "price_asc":
        return sorted(matched, key=lambda item: (item.price, -item.search_score, -item.pk))
    if sort == "price_desc":
        return sorted(matched, key=lambda item: (-item.price, -item.search_score, -item.pk))
    if sort == "newest":
        return sorted(matched, key=lambda item: (item.published_at or item.created_at, item.pk), reverse=True)
    return sorted(matched, key=lambda item: (item.search_score, item.published_at or item.created_at, item.pk), reverse=True)


def interpretation_labels(intent):
    labels = []
    if intent.deal_type:
        labels.append(Listing.DealType(intent.deal_type).label)
    if intent.property_type:
        labels.append(Listing.PropertyType(intent.property_type).label)
    if intent.rooms:
        labels.append(f"{intent.rooms} комн.")
    if intent.min_price is not None:
        labels.append(f"от {intent.min_price:g}")
    if intent.max_price is not None:
        labels.append(f"до {intent.max_price:g}")
    if intent.currency:
        labels.append(Listing.Currency(intent.currency).label)
    if intent.city_name:
        labels.append(intent.city_name)
    return labels
