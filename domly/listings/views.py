from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import BooleanField, Exists, OuterRef, Q, Value
from django.http import Http404, HttpResponseBadRequest
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from .forms import ListingCreateForm, ModerationRejectForm
from .locations import CITIES, CITY_SESSION_KEY, get_selected_city
from .models import City, Favorite, Listing, ListingImage, ModerationDecision


def _can_moderate(user):
    return user.is_authenticated and (user.is_moderator or user.is_superuser)


def _require_moderator(user):
    if not _can_moderate(user):
        raise PermissionDenied


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
        {"form": form, "submit_action": submit_action},
    )


@login_required
@require_http_methods(["GET", "POST"])
def edit_listing(request, public_id):
    listing = get_object_or_404(Listing, public_id=public_id, owner=request.user)
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
        },
    )

def listing_list(request):
    selected_city = get_selected_city(request)
    listings = (
        Listing.objects.published()
        .filter(city__name=selected_city, city__is_active=True)
        .select_related("city", "owner")
        .prefetch_related("images")
    )

    query = request.GET.get("q", "").strip()
    deal_type = request.GET.get("deal_type", Listing.DealType.RENT)
    property_type = request.GET.get(
        "property_type", Listing.PropertyType.APARTMENT
    )
    sort = request.GET.get("sort", "newest")
    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()
    rooms = request.GET.get("rooms", "").strip()
    min_area = request.GET.get("min_area", "").strip()
    max_area = request.GET.get("max_area", "").strip()
    min_floor = request.GET.get("min_floor", "").strip()
    max_floor = request.GET.get("max_floor", "").strip()

    if query:
        listings = listings.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(address__icontains=query)
        )
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
    if sort not in ordering:
        sort = "newest"
    listings = listings.order_by(*ordering[sort])

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
        },
    )


def listing_detail(request, public_id):
    listing = get_object_or_404(
        Listing.objects.select_related("owner", "city").prefetch_related("images"),
        public_id=public_id,
    )
    can_preview = request.user.is_authenticated and (
        listing.owner_id == request.user.id
        or request.user.is_moderator
        or request.user.is_superuser
    )
    if listing.status != Listing.Status.PUBLISHED and not can_preview:
        raise Http404

    can_moderate = _can_moderate(request.user)
    latest_decision = None
    if listing.status == Listing.Status.REJECTED and (
        listing.owner_id == getattr(request.user, "id", None) or can_moderate
    ):
        latest_decision = (
            listing.moderation_decisions.select_related("moderator")
            .filter(decision=ModerationDecision.Decision.REJECTED)
            .first()
        )

    return render(
        request,
        "listings/detail.html",
        {
            "listing": listing,
            "listing_images": list(listing.images.all()),
            "is_favorite": (
                request.user.is_authenticated
                and Favorite.objects.filter(user=request.user, listing=listing).exists()
            ),
            "can_moderate": can_moderate and listing.status == Listing.Status.PENDING,
            "moderation_reject_form": ModerationRejectForm(),
            "latest_moderation_decision": latest_decision,
        },
    )


@login_required
@require_POST
def toggle_favorite(request, public_id):
    listing = get_object_or_404(
        Listing,
        public_id=public_id,
        status=Listing.Status.PUBLISHED,
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

@require_http_methods(["GET"])
def help(request):
    return render(request, "listings/help.html")


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
    return redirect(next_url)


def city_map(request):
    city_name = get_selected_city(request)
    city = CITIES[city_name]
    map_config = {
        "name": city_name,
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "zoom": city["zoom"],
    }
    return render(
        request,
        "listings/city_map.html",
        {
            "map_config": map_config,
            "city_listings": [],
        },
    )
