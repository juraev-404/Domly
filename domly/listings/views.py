import json
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Exists, OuterRef, Q, Value
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import Truncator
from django.utils.translation import get_language, gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    ListingCreateForm,
    ListingReportForm,
    ListingReportReviewForm,
    ModerationBlockForm,
    ModerationRejectForm,
    ModerationUnblockForm,
)
from .locations import CITIES, CITY_SESSION_KEY, get_selected_city, localize_city_name
from .geocoding import GeocodingRateLimited, GeocodingUnavailable, geocode_address
from .models import (
    City,
    Favorite,
    Listing,
    ListingBlock,
    ListingImage,
    ListingReport,
    ModerationDecision,
)
from .moderation_blocks import block_expiry, release_listing_block
from .search import interpretation_labels, parse_search_query, rank_search_results
from users.models import Notification, User, UserBlock
from users.moderation_blocks import release_user_block


def _can_moderate(user):
    return user.is_authenticated and (user.is_moderator or user.is_superuser)


def _require_moderator(user):
    if not _can_moderate(user):
        raise PermissionDenied


def _city_picker_configs():
    return {
        str(city.pk): {
            "name": city.name,
            "latitude": float(city.latitude),
            "longitude": float(city.longitude),
            "zoom": city.map_zoom,
        }
        for city in City.objects.filter(is_active=True).order_by("name")
    }


def _display_decimal(value):
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _transition_owned_listing(
    request,
    public_id,
    *,
    allowed_statuses,
    target_status,
):
    with transaction.atomic():
        listing = get_object_or_404(
            Listing.objects.select_for_update().exclude(status=Listing.Status.DELETED),
            public_id=public_id,
            owner=request.user,
        )
        if listing.status not in allowed_statuses:
            messages.error(request, _("Это действие недоступно для текущего статуса объявления."))
            return listing, False

        listing.status = target_status
        update_fields = ["status", "updated_at"]
        if target_status == Listing.Status.PUBLISHED and listing.published_at is None:
            listing.published_at = timezone.now()
            update_fields.append("published_at")
        if target_status == Listing.Status.DELETED:
            listing.deleted_at = timezone.now()
            update_fields.append("deleted_at")
        listing.save(update_fields=update_fields)

        if target_status == Listing.Status.DELETED:
            Favorite.objects.filter(listing=listing).delete()

    return listing, True


@login_required
def create_listing(request):
    submit_action = request.POST.get("action", "draft")
    if submit_action not in {"draft", "publish"}:
        submit_action = "draft"

    selected_city = City.objects.filter(
        name=get_selected_city(request),
        is_active=True,
    ).first()
    form = ListingCreateForm(
        request.POST or None,
        request.FILES or None,
        initial={"city": selected_city},
        submit_action=submit_action,
    )

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            listing = form.save(commit=False)
            listing.owner = request.user
            listing.status = (
                Listing.Status.PENDING
                if submit_action == "publish"
                else Listing.Status.DRAFT
            )
            listing.submitted_at = timezone.now() if submit_action == "publish" else None
            listing.save()

            for position, image in enumerate(form.cleaned_data["images"]):
                ListingImage.objects.create(
                    listing=listing,
                    image=image,
                    alt_text=listing.title,
                    position=position,
                )

        if submit_action == "publish":
            messages.success(
                request,
                "Объявление отправлено на модерацию.",
            )
        else:
            messages.success(request, "Черновик объявления сохранён.")
        return redirect("create")

    return render(
        request,
        "listings/create.html",
        {
            "form": form,
            "submit_action": submit_action,
            "city_picker_configs": _city_picker_configs(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_listing(request, public_id):
    listing = get_object_or_404(
        Listing.objects.exclude(status=Listing.Status.DELETED),
        public_id=public_id,
        owner=request.user,
    )
    submit_action = request.POST.get("action", "draft")
    if submit_action not in {"draft", "publish"}:
        submit_action = "draft"

    form = ListingCreateForm(
        request.POST or None,
        request.FILES or None,
        instance=listing,
        submit_action=submit_action,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            listing = form.save(commit=False)
            listing.status = (
                Listing.Status.PENDING
                if submit_action == "publish"
                else Listing.Status.DRAFT
            )
            listing.submitted_at = timezone.now() if submit_action == "publish" else None
            listing.published_at = None
            listing.save()

            form.cleaned_data["remove_images"].delete()
            remaining_images = list(listing.images.order_by("position", "id"))
            for position, image in enumerate(remaining_images):
                image.position = position
            if remaining_images:
                ListingImage.objects.bulk_update(remaining_images, ["position"])

            start_position = len(remaining_images)
            for offset, image in enumerate(form.cleaned_data["images"]):
                ListingImage.objects.create(
                    listing=listing,
                    image=image,
                    alt_text=listing.title,
                    position=start_position + offset,
                )

        if submit_action == "publish":
            messages.success(request, "Изменения сохранены, объявление отправлено на модерацию.")
        else:
            messages.success(request, "Изменения сохранены в черновике.")
        return redirect(listing.get_absolute_url())

    return render(
        request,
        "listings/create.html",
        {
            "form": form,
            "listing": listing,
            "is_edit": True,
            "submit_action": submit_action,
            "city_picker_configs": _city_picker_configs(),
        },
    )

def listing_list(request, city_slug=None):
    selected_city = get_selected_city(request)
    city_page = None
    if city_slug is not None:
        city_page = get_object_or_404(City, slug=city_slug, is_active=True)
    query = request.GET.get("q", "").strip()
    city_names = list(
        City.objects.filter(is_active=True).order_by("name").values_list("name", flat=True)
    )
    search_intent = parse_search_query(query, city_names) if query else None
    requested_city = request.GET.get("city", "").strip()
    if city_page is None and requested_city in city_names:
        target_city = City.objects.get(name=requested_city, is_active=True)
        target = reverse("city_listings", kwargs={"city_slug": target_city.slug})
        params = request.GET.copy()
        params.pop("city", None)
        if params:
            target = f"{target}?{params.urlencode()}"
        return redirect(target)
    if city_page and requested_city in city_names and requested_city != city_page.name:
        target = reverse("city_listings", kwargs={"city_slug": City.objects.get(name=requested_city).slug})
        params = request.GET.copy()
        params.pop("city", None)
        if params:
            target = f"{target}?{params.urlencode()}"
        return redirect(target)
    result_city = (
        city_page.name
        if city_page
        else requested_city
        if requested_city in city_names
        else search_intent.city_name
        if search_intent and search_intent.city_name
        else selected_city
    )
    listings = (
        Listing.objects.published()
        .filter(city__name=result_city, city__is_active=True)
        .select_related("city", "owner")
        .prefetch_related("images")
    )

    deal_type = request.GET.get("deal_type", "")
    if not deal_type:
        deal_type = (
            search_intent.deal_type
            if search_intent and search_intent.deal_type
            else Listing.DealType.RENT
        )
    property_type = request.GET.get("property_type", "")
    if "property_type" not in request.GET:
        property_type = (
            search_intent.property_type
            if search_intent and search_intent.property_type
            else Listing.PropertyType.APARTMENT
        )
    sort = request.GET.get("sort", "relevance" if query else "newest")
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()
    rooms = request.GET.get("rooms", "").strip()
    min_area = request.GET.get("min_area", "").strip()
    max_area = request.GET.get("max_area", "").strip()
    min_floor = request.GET.get("min_floor", "").strip()
    max_floor = request.GET.get("max_floor", "").strip()

    if search_intent:
        if not min_price and search_intent.min_price is not None:
            min_price = str(search_intent.min_price)
        if not max_price and search_intent.max_price is not None:
            max_price = str(search_intent.max_price)
        if not rooms and search_intent.rooms:
            rooms = str(search_intent.rooms)
        if search_intent.currency:
            listings = listings.filter(currency=search_intent.currency)
    if deal_type not in Listing.DealType.values:
        deal_type = Listing.DealType.RENT
    listings = listings.filter(deal_type=deal_type)

    if property_type in Listing.PropertyType.values:
        listings = listings.filter(property_type=property_type)
    elif property_type:
        property_type = Listing.PropertyType.APARTMENT
        listings = listings.filter(property_type=property_type)
    else:
        property_type = ""

    for value, lookup in ((min_price, "price__gte"), (max_price, "price__lte")):
        if value:
            try:
                parsed_price = Decimal(value)
                if parsed_price >= 0:
                    listings = listings.filter(**{lookup: parsed_price})
            except InvalidOperation:
                pass

    if rooms == "5_plus":
        listings = listings.filter(rooms__gte=5)
    elif rooms in {"1", "2", "3", "4"}:
        listings = listings.filter(rooms=int(rooms))
    else:
        rooms = ""

    for value, lookup in ((min_area, "area__gte"), (max_area, "area__lte")):
        if value:
            try:
                parsed_area = Decimal(value)
                if parsed_area >= 0:
                    listings = listings.filter(**{lookup: parsed_area})
            except InvalidOperation:
                pass

    for value, lookup in ((min_floor, "floor__gte"), (max_floor, "floor__lte")):
        if value:
            try:
                parsed_floor = int(value)
                if parsed_floor >= 0:
                    listings = listings.filter(**{lookup: parsed_floor})
            except (TypeError, ValueError):
                pass

    ordering = {
        "newest": ("-published_at", "-created_at"),
        "price_asc": ("price", "-created_at"),
        "price_desc": ("-price", "-created_at"),
    }
    if sort not in {*ordering, "relevance"}:
        sort = "relevance" if query else "newest"

    if request.user.is_authenticated:
        listings = listings.annotate(
            is_favorite=Exists(
                Favorite.objects.filter(user=request.user, listing_id=OuterRef("pk"))
            )
        )
    else:
        listings = listings.annotate(
            is_favorite=Value(False, output_field=BooleanField())
        )

    if query:
        listings = rank_search_results(list(listings), search_intent, sort=sort)
    else:
        if sort == "relevance":
            sort = "newest"
        listings = listings.order_by(*ordering[sort])

    paginator = Paginator(listings, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    next_page_query = ""
    if page_obj.has_next():
        next_params = request.GET.copy()
        next_params["page"] = page_obj.next_page_number()
        next_page_query = next_params.urlencode()

    return render(
        request,
        "listings/list.html",
        {
            "page_obj": page_obj,
            "listing_count": paginator.count,
            "query": query,
            "result_city": result_city,
            "selected_city": result_city,
            "city_page": city_page,
            "catalog_url": (
                reverse("city_listings", kwargs={"city_slug": city_page.slug})
                if city_page
                else reverse("listing_list")
            ),
            "search_cities": city_names,
            "search_interpretation": interpretation_labels(search_intent) if search_intent else [],
            "deal_type": deal_type,
            "property_type": property_type,
            "sort": sort,
            "min_price": min_price,
            "max_price": max_price,
            "rooms": rooms,
            "min_area": min_area,
            "max_area": max_area,
            "min_floor": min_floor,
            "max_floor": max_floor,
            "advanced_filters_active": any(
                (rooms, min_area, max_area, min_floor, max_floor)
            ),
            "deal_types": Listing.DealType.choices,
            "property_types": Listing.PropertyType.choices,
            "show_favorite_button": True,
            "next_page_query": next_page_query,
            "seo_title": _("Недвижимость в %(city)s — Domly")
            % {"city": localize_city_name(result_city)},
            "seo_description": _(
                "Актуальные объявления об аренде и продаже недвижимости в %(city)s. "
                "Квартиры, дома, комнаты и участки на Domly."
            ) % {"city": localize_city_name(result_city)},
            "seo_robots": "noindex,follow" if request.GET else "index,follow",
        },
    )


def _listing_structured_data(request, listing, listing_images):
    listing_url = request.build_absolute_uri(listing.get_absolute_url())
    property_types = {
        Listing.PropertyType.APARTMENT: "Apartment",
        Listing.PropertyType.HOUSE: "House",
        Listing.PropertyType.ROOM: "Room",
        Listing.PropertyType.LAND: "Place",
        Listing.PropertyType.COMMERCIAL: "Place",
    }
    property_data = {
        "@type": property_types.get(listing.property_type, "Place"),
        "name": listing.title,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": listing.address,
            "addressLocality": localize_city_name(listing.city.name),
            "addressCountry": "TJ",
        },
    }
    if listing.rooms is not None:
        property_data["numberOfRooms"] = listing.rooms
    if listing.area is not None:
        property_data["floorSize"] = {
            "@type": "QuantitativeValue",
            "value": listing.area,
            "unitCode": "MTK",
        }
    if listing.latitude is not None and listing.longitude is not None:
        property_data["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": listing.latitude,
            "longitude": listing.longitude,
        }

    data = {
        "@context": "https://schema.org",
        "@type": "RealEstateListing",
        "@id": f"{listing_url}#listing",
        "url": listing_url,
        "name": listing.title,
        "description": listing.description,
        "datePosted": (listing.published_at or listing.created_at).isoformat(),
        "dateModified": listing.updated_at.isoformat(),
        "inLanguage": (get_language() or settings.LANGUAGE_CODE).split("-")[0],
        "offers": {
            "@type": "Offer",
            "url": listing_url,
            "price": listing.price,
            "priceCurrency": listing.currency,
            "availability": "https://schema.org/InStock",
        },
        "about": property_data,
        "seller": {
            "@type": "Person",
            "name": listing.owner.username,
        },
    }
    images = [request.build_absolute_uri(image.image.url) for image in listing_images]
    if images:
        data["image"] = images
    return json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False).translate(
        str.maketrans({"&": "\\u0026", "<": "\\u003c", ">": "\\u003e"})
    )


def listing_detail(request, public_id):
    listing = get_object_or_404(
        Listing.objects.select_related("owner", "city").prefetch_related("images"),
        public_id=public_id,
    )
    if listing.status == Listing.Status.DELETED:
        raise Http404
    can_preview = request.user.is_authenticated and (
        listing.owner_id == request.user.id
        or request.user.is_moderator
        or request.user.is_superuser
    )
    if not listing.owner.is_active and not (
        request.user.is_authenticated
        and (request.user.is_moderator or request.user.is_superuser)
    ):
        raise Http404
    if listing.status != Listing.Status.PUBLISHED and not can_preview:
        raise Http404

    can_moderate = _can_moderate(request.user)
    latest_decision = None
    active_listing_block = listing.moderation_blocks.filter(
        unblocked_at__isnull=True
    ).select_related("moderator").first()
    if listing.status == Listing.Status.REJECTED and (
        listing.owner_id == getattr(request.user, "id", None) or can_moderate
    ):
        latest_decision = (
            listing.moderation_decisions.select_related("moderator")
            .filter(decision=ModerationDecision.Decision.REJECTED)
            .first()
        )

    is_owner = listing.owner_id == getattr(request.user, "id", None)
    has_pending_report = (
        request.user.is_authenticated
        and not is_owner
        and ListingReport.objects.filter(
            listing=listing,
            reporter=request.user,
            status=ListingReport.Status.PENDING,
        ).exists()
    )
    listing_images = list(listing.images.all())
    cover_image = listing_images[0] if listing_images else None
    is_published = listing.status == Listing.Status.PUBLISHED

    return render(
        request,
        "listings/detail.html",
        {
            "listing": listing,
            "listing_images": listing_images,
            "is_favorite": (
                request.user.is_authenticated
                and Favorite.objects.filter(user=request.user, listing=listing).exists()
            ),
            "can_moderate": can_moderate and listing.status == Listing.Status.PENDING,
            "moderation_reject_form": ModerationRejectForm(),
            "latest_moderation_decision": latest_decision,
            "active_listing_block": active_listing_block,
            "can_manage_blocks": can_moderate and listing.status != Listing.Status.DELETED,
            "can_block_listing": can_moderate and active_listing_block is None,
            "can_block_owner": (
                can_moderate
                and request.user.pk != listing.owner_id
                and listing.owner.is_active
                and not listing.owner.is_superuser
                and (request.user.is_superuser or not listing.owner.is_moderator)
            ),
            "moderation_block_form": ModerationBlockForm(),
            "is_owner": is_owner,
            "can_report": (
                request.user.is_authenticated
                and not is_owner
                and listing.status == Listing.Status.PUBLISHED
            ),
            "has_pending_report": has_pending_report,
            "listing_report_form": ListingReportForm(),
            "can_archive": listing.status == Listing.Status.PUBLISHED,
            "can_restore": listing.status in {
                Listing.Status.ARCHIVED,
                Listing.Status.COMPLETED,
            },
            "can_complete": listing.status == Listing.Status.PUBLISHED,
            "completion_action_label": (
                _("Отметить проданным")
                if listing.deal_type == Listing.DealType.SALE
                else _("Отметить сданным")
            ),
            "seo_title": f"{listing.title} — {localize_city_name(listing.city.name)} | Domly",
            "seo_description": Truncator(listing.description).chars(155),
            "og_type": "article",
            "og_image_url": (
                request.build_absolute_uri(cover_image.image.url) if cover_image else ""
            ),
            "listing_structured_data": (
                _listing_structured_data(request, listing, listing_images)
                if is_published
                else ""
            ),
            "seo_robots": "index,follow" if is_published else "noindex,nofollow",
        },
    )


@login_required
@require_POST
def archive_listing(request, public_id):
    listing, changed = _transition_owned_listing(
        request,
        public_id,
        allowed_statuses={Listing.Status.PUBLISHED},
        target_status=Listing.Status.ARCHIVED,
    )
    if changed:
        messages.success(request, _("Объявление снято с публикации."))
    return redirect(listing.get_absolute_url())


@login_required
@require_POST
def restore_listing(request, public_id):
    listing, changed = _transition_owned_listing(
        request,
        public_id,
        allowed_statuses={Listing.Status.ARCHIVED, Listing.Status.COMPLETED},
        target_status=Listing.Status.PUBLISHED,
    )
    if changed:
        messages.success(request, _("Объявление снова опубликовано."))
    return redirect(listing.get_absolute_url())


@login_required
@require_POST
def complete_listing(request, public_id):
    listing, changed = _transition_owned_listing(
        request,
        public_id,
        allowed_statuses={Listing.Status.PUBLISHED},
        target_status=Listing.Status.COMPLETED,
    )
    if changed:
        if listing.deal_type == Listing.DealType.SALE:
            messages.success(request, _("Объявление отмечено как проданное."))
        else:
            messages.success(request, _("Объявление отмечено как сданное."))
    return redirect(listing.get_absolute_url())


@login_required
@require_POST
def delete_listing(request, public_id):
    deleted_listing, changed = _transition_owned_listing(
        request,
        public_id,
        allowed_statuses={
            Listing.Status.DRAFT,
            Listing.Status.PENDING,
            Listing.Status.PUBLISHED,
            Listing.Status.REJECTED,
            Listing.Status.ARCHIVED,
            Listing.Status.COMPLETED,
        },
        target_status=Listing.Status.DELETED,
    )
    if changed:
        messages.success(request, _("Объявление удалено."))
    return redirect("profile_listings")


@login_required
@require_POST
def toggle_favorite(request, public_id):
    listing = get_object_or_404(
        Listing.objects.published(),
        public_id=public_id,
    )
    favorite, created = Favorite.objects.get_or_create(
        user=request.user,
        listing=listing,
    )
    if created:
        messages.success(request, "Объявление добавлено в избранное.")
    else:
        favorite.delete()
        messages.success(request, "Объявление удалено из избранного.")

    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = listing.get_absolute_url()
    return redirect(next_url)


@login_required
@require_POST
def report_listing(request, public_id):
    listing = get_object_or_404(
        Listing.objects.published(),
        public_id=public_id,
    )
    if listing.owner_id == request.user.id:
        raise PermissionDenied

    form = ListingReportForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Проверьте причину и описание жалобы."))
        return redirect(f"{listing.get_absolute_url()}#report-listing")

    try:
        report, created = ListingReport.objects.get_or_create(
            listing=listing,
            reporter=request.user,
            status=ListingReport.Status.PENDING,
            defaults={
                "reason": form.cleaned_data["reason"],
                "details": form.cleaned_data["details"],
            },
        )
    except IntegrityError:
        created = False

    if created:
        messages.success(request, _("Жалоба отправлена модераторам."))
    else:
        messages.info(request, _("Ваша жалоба на это объявление уже рассматривается."))
    return redirect(listing.get_absolute_url())

@require_http_methods(["GET"])
def help(request):
    return render(
        request,
        "listings/help.html",
        {
            "seo_title": _("Помощь и безопасность — Domly"),
            "seo_description": _(
                "Ответы о регистрации, публикации объявлений, модерации, "
                "поиске недвижимости и безопасном использовании Domly."
            ),
        },
    )


def _legal_page(request, template_name, title, description):
    return render(
        request,
        template_name,
        {
            "legal_contact_email": settings.LEGAL_CONTACT_EMAIL,
            "legal_operator_name": settings.LEGAL_OPERATOR_NAME,
            "legal_operator_address": settings.LEGAL_OPERATOR_ADDRESS,
            "legal_operator_registration_id": settings.LEGAL_OPERATOR_REGISTRATION_ID,
            "legal_operator_tax_id": settings.LEGAL_OPERATOR_TAX_ID,
            "legal_data_protection_certificate": settings.LEGAL_DATA_PROTECTION_CERTIFICATE,
            "legal_documents_draft": settings.LEGAL_DOCUMENTS_DRAFT,
            "legal_updated_at": date(2026, 8, 17),
            "seo_title": title,
            "seo_description": description,
        },
    )


@require_http_methods(["GET"])
def privacy_policy(request):
    return _legal_page(
        request,
        "listings/legal/privacy.html",
        _("Политика конфиденциальности — Domly"),
        _("Как Domly собирает, использует, хранит и защищает данные пользователей."),
    )


@require_http_methods(["GET"])
def terms_of_use(request):
    return _legal_page(
        request,
        "listings/legal/terms.html",
        _("Пользовательское соглашение — Domly"),
        _("Условия регистрации, публикации объявлений и использования сервиса Domly."),
    )


@require_http_methods(["GET"])
def publication_rules(request):
    return _legal_page(
        request,
        "listings/legal/publication_rules.html",
        _("Правила публикации и модерации — Domly"),
        _("Требования к объявлениям и порядок их проверки модераторами Domly."),
    )


@login_required
def moderation(request):
    _require_moderator(request.user)
    listings = (
        Listing.objects.filter(status=Listing.Status.PENDING)
        .select_related("owner", "city")
        .order_by("submitted_at", "created_at", "pk")
    )
    paginator = Paginator(listings, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "listings/moderation.html",
        {"page_obj": page_obj, "pending_count": paginator.count},
    )


@login_required
def listing_reports(request):
    _require_moderator(request.user)
    selected_status = request.GET.get("status", ListingReport.Status.PENDING)
    if selected_status not in ListingReport.Status.values:
        selected_status = ListingReport.Status.PENDING

    reports = (
        ListingReport.objects.filter(status=selected_status)
        .select_related("listing__owner", "listing__city", "reporter", "moderator")
        .order_by("created_at", "pk")
    )
    paginator = Paginator(reports, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "listings/reports.html",
        {
            "page_obj": page_obj,
            "selected_status": selected_status,
            "report_statuses": ListingReport.Status,
            "pending_report_count": ListingReport.objects.filter(
                status=ListingReport.Status.PENDING
            ).count(),
            "review_form": ListingReportReviewForm(),
        },
    )


@login_required
def moderation_blocks(request):
    _require_moderator(request.user)
    return render(
        request,
        "listings/blocks.html",
        {
            "active_listing_blocks": ListingBlock.objects.filter(
                unblocked_at__isnull=True
            ).select_related("listing__owner", "moderator"),
            "active_user_blocks": UserBlock.objects.filter(
                unblocked_at__isnull=True
            ).select_related("user", "moderator"),
            "history_listing_blocks": ListingBlock.objects.filter(
                unblocked_at__isnull=False
            ).select_related("listing__owner", "moderator", "unblocked_by")[:50],
            "history_user_blocks": UserBlock.objects.filter(
                unblocked_at__isnull=False
            ).select_related("user", "moderator", "unblocked_by")[:50],
        },
    )


@login_required
@require_POST
def block_listing(request, public_id):
    _require_moderator(request.user)
    form = ModerationBlockForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Проверьте причину и срок блокировки."))
        return redirect("listing_detail", public_id=public_id)
    with transaction.atomic():
        listing = get_object_or_404(
            Listing.objects.select_for_update().exclude(status=Listing.Status.DELETED),
            public_id=public_id,
        )
        if listing.status == Listing.Status.BLOCKED or ListingBlock.objects.filter(
            listing=listing, unblocked_at__isnull=True
        ).exists():
            messages.error(request, _("Объявление уже заблокировано."))
            return redirect(listing.get_absolute_url())
        ListingBlock.objects.create(
            listing=listing,
            moderator=request.user,
            reason=form.cleaned_data["reason"],
            previous_status=listing.status,
            expires_at=block_expiry(form.cleaned_data["duration"]),
        )
        listing.status = Listing.Status.BLOCKED
        listing.save(update_fields=("status", "updated_at"))
        Notification.objects.create(
            user=listing.owner,
            listing=listing,
            kind=Notification.Kind.LISTING_BLOCKED,
            message=form.cleaned_data["reason"],
        )
    messages.success(request, _("Объявление заблокировано."))
    return redirect(listing.get_absolute_url())


@login_required
@require_POST
def unblock_listing(request, public_id):
    _require_moderator(request.user)
    form = ModerationUnblockForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Укажите причину разблокировки."))
        return redirect("moderation_blocks")
    block = get_object_or_404(ListingBlock, public_id=public_id)
    released_block, changed = release_listing_block(block, actor=request.user, note=form.cleaned_data["note"])
    messages.success(request, _("Объявление разблокировано.") if changed else _("Блокировка уже завершена."))
    return redirect("moderation_blocks")


@login_required
@require_POST
def block_user(request, username):
    _require_moderator(request.user)
    target = get_object_or_404(User, username__iexact=username)
    if target.pk == request.user.pk or target.is_superuser or (
        target.is_moderator and not request.user.is_superuser
    ):
        raise PermissionDenied
    form = ModerationBlockForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Проверьте причину и срок блокировки."))
        listing_public_id = request.POST.get("listing_public_id")
        if listing_public_id:
            return redirect("listing_detail", public_id=listing_public_id)
        return redirect("moderation_blocks")
    with transaction.atomic():
        target = User.objects.select_for_update().get(pk=target.pk)
        if not target.is_active or UserBlock.objects.filter(
            user=target, unblocked_at__isnull=True
        ).exists():
            messages.error(request, _("Аккаунт уже заблокирован или отключён."))
            return redirect("moderation_blocks")
        UserBlock.objects.create(
            user=target,
            moderator=request.user,
            reason=form.cleaned_data["reason"],
            was_active=target.is_active,
            expires_at=block_expiry(form.cleaned_data["duration"]),
        )
        Notification.objects.create(
            user=target,
            kind=Notification.Kind.ACCOUNT_BLOCKED,
            message=form.cleaned_data["reason"],
        )
        target.is_active = False
        target.save(update_fields=("is_active",))
    messages.success(request, _("Аккаунт заблокирован."))
    return redirect("moderation_blocks")


@login_required
@require_POST
def unblock_user(request, public_id):
    _require_moderator(request.user)
    form = ModerationUnblockForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Укажите причину разблокировки."))
        return redirect("moderation_blocks")
    block = get_object_or_404(UserBlock, public_id=public_id)
    released_block, changed = release_user_block(block, actor=request.user, note=form.cleaned_data["note"])
    messages.success(request, _("Аккаунт разблокирован.") if changed else _("Блокировка уже завершена."))
    return redirect("moderation_blocks")


@login_required
@require_POST
def review_listing_report(request, public_id):
    _require_moderator(request.user)
    decision = request.POST.get("decision")
    if decision not in {ListingReport.Status.CONFIRMED, ListingReport.Status.DISMISSED}:
        return HttpResponseBadRequest(_("Неизвестное решение."))

    form = ListingReportReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Добавьте комментарий к решению."))
        return redirect("listing_reports")

    with transaction.atomic():
        report = get_object_or_404(
            ListingReport.objects.select_for_update(),
            public_id=public_id,
        )
        if report.status != ListingReport.Status.PENDING:
            messages.error(request, _("Эта жалоба уже обработана."))
            return redirect("listing_reports")
        report.status = decision
        report.moderator = request.user
        report.resolution_note = form.cleaned_data["resolution_note"]
        report.reviewed_at = timezone.now()
        report.save(
            update_fields=("status", "moderator", "resolution_note", "reviewed_at")
        )

    messages.success(request, _("Решение по жалобе сохранено."))
    return redirect("listing_reports")


@login_required
@require_POST
def moderation_approve(request, public_id):
    _require_moderator(request.user)
    with transaction.atomic():
        listing = get_object_or_404(
            Listing.objects.select_for_update(),
            public_id=public_id,
        )
        if listing.status != Listing.Status.PENDING:
            messages.error(request, _("Это объявление уже обработано."))
            return redirect("moderation")

        listing.status = Listing.Status.PUBLISHED
        listing.published_at = timezone.now()
        listing.save(update_fields=("status", "published_at", "updated_at"))
        ModerationDecision.objects.create(
            listing=listing,
            moderator=request.user,
            decision=ModerationDecision.Decision.APPROVED,
        )
        Notification.objects.create(
            user=listing.owner,
            listing=listing,
            kind=Notification.Kind.LISTING_APPROVED,
        )

    messages.success(request, _("Объявление опубликовано."))
    return redirect("moderation")


@login_required
@require_POST
def moderation_reject(request, public_id):
    _require_moderator(request.user)
    form = ModerationRejectForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Укажите причину отклонения."))
        return redirect("listing_detail", public_id=public_id)

    with transaction.atomic():
        listing = get_object_or_404(
            Listing.objects.select_for_update(),
            public_id=public_id,
        )
        if listing.status != Listing.Status.PENDING:
            messages.error(request, _("Это объявление уже обработано."))
            return redirect("moderation")

        listing.status = Listing.Status.REJECTED
        listing.published_at = None
        listing.save(update_fields=("status", "published_at", "updated_at"))
        ModerationDecision.objects.create(
            listing=listing,
            moderator=request.user,
            decision=ModerationDecision.Decision.REJECTED,
            reason=form.cleaned_data["reason"],
        )
        Notification.objects.create(
            user=listing.owner,
            listing=listing,
            kind=Notification.Kind.LISTING_REJECTED,
            message=form.cleaned_data["reason"],
        )

    messages.success(request, _("Объявление отклонено, причина сохранена."))
    return redirect("moderation")


@require_POST
def set_city(request):
    city = request.POST.get("city", "")
    if city not in CITIES:
        return HttpResponseBadRequest("Неизвестный город")

    request.session[CITY_SESSION_KEY] = city
    next_url = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("listing_list")
    else:
        try:
            next_match = resolve(urlsplit(next_url).path)
        except Resolver404:
            next_match = None
        if next_match and next_match.url_name in {"listing_list", "city_listings"}:
            city_object = City.objects.get(name=city, is_active=True)
            next_url = reverse(
                "city_listings", kwargs={"city_slug": city_object.slug}
            )
    return redirect(next_url)


@login_required
@require_http_methods(["GET"])
def geocode_location(request):
    address = request.GET.get("address", "").strip()
    city_name = request.GET.get("city", "").strip()
    if len(address) < 5:
        return JsonResponse(
            {"error": "Введите более точный адрес."},
            status=400,
        )
    city = City.objects.filter(name=city_name, is_active=True).first()
    if city is None:
        return JsonResponse({"error": "Выберите город из списка."}, status=400)

    try:
        result = geocode_address(city=city, address=address)
    except GeocodingRateLimited:
        return JsonResponse(
            {"error": "Повторите поиск адреса через секунду."},
            status=429,
        )
    except GeocodingUnavailable:
        return JsonResponse(
            {"error": "Сервис поиска адресов временно недоступен."},
            status=503,
        )

    if result is None:
        return JsonResponse(
            {"error": "Адрес не найден. Уточните его или поставьте точку вручную."},
            status=404,
        )
    return JsonResponse(result)


def city_map(request):
    requested_city = request.GET.get("city", "").strip()
    city = City.objects.filter(name=requested_city, is_active=True).first()
    if city is None:
        city = get_object_or_404(
            City,
            name=get_selected_city(request),
            is_active=True,
        )

    city_listings_query = (
        Listing.objects.published()
        .filter(city=city)
        .select_related("city", "owner")
        .prefetch_related("images")
        .order_by("-published_at", "-created_at")
    )
    unmapped_count = city_listings_query.filter(
        Q(latitude__isnull=True) | Q(longitude__isnull=True)
    ).count()
    city_listings = list(
        city_listings_query.filter(
            latitude__isnull=False,
            longitude__isnull=False,
        )
    )
    map_listings = []
    for listing in city_listings:
        images = list(listing.images.all())
        cover = images[0] if images else None
        price = _display_decimal(listing.price)
        area = (
            _display_decimal(listing.area)
            if listing.area is not None
            else ""
        )
        map_listings.append(
            {
                "id": str(listing.public_id),
                "title": listing.title,
                "url": listing.get_absolute_url(),
                "latitude": float(listing.latitude),
                "longitude": float(listing.longitude),
                "price": price,
                "currency": listing.currency,
                "is_negotiable": listing.is_negotiable,
                "address": listing.address,
                "rooms": listing.rooms,
                "area": area,
                "floor": listing.floor,
                "total_floors": listing.total_floors,
                "image_url": cover.image.url if cover else "",
            }
        )

    requested_listing = request.GET.get("listing", "").strip()
    focused_listing_id = next(
        (
            str(listing.public_id)
            for listing in city_listings
            if str(listing.public_id) == requested_listing
        ),
        "",
    )

    map_config = {
        "name": city.name,
        "latitude": float(city.latitude),
        "longitude": float(city.longitude),
        "zoom": city.map_zoom,
    }
    return render(
        request,
        "listings/city_map.html",
        {
            "map_config": map_config,
            "map_city_name": city.name,
            "city_listings": city_listings,
            "map_listings": map_listings,
            "focused_listing_id": focused_listing_id,
            "unmapped_count": unmapped_count,
        },
    )
