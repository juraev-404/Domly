from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Exists, OuterRef, Value
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from listings.models import Favorite, Listing

from .forms import LoginForm, ProfileForm, RegistrationForm, VerificationCodeForm
from .models import Notification, RegistrationAttempt, User
from .services import generate_verification_code, send_verification_code


REGISTRATION_SESSION_KEY = "registration_attempt_id"
MAX_REGISTRATIONS_PER_HOUR = 5


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or None


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("/")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            identifier=form.cleaned_data["identifier"],
            password=form.cleaned_data["password"],
        )
        if user is None:
            form.add_error(None, "Неверные данные для входа.")
        else:
            login(request, user)
            if not form.cleaned_data["remember_me"]:
                request.session.set_expiry(0)
            next_url = request.GET.get("next")
            if not next_url or not url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                next_url = "/"
            return redirect(next_url)

    return render(request, "users/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("/")


@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect("/")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ip = _client_ip(request)
        since = timezone.now() - timedelta(hours=1)
        if ip and RegistrationAttempt.objects.filter(
            request_ip=ip, created_at__gte=since
        ).count() >= MAX_REGISTRATIONS_PER_HOUR:
            form.add_error(None, "Слишком много запросов. Попробуйте позже.")
        else:
            code = generate_verification_code()
            attempt = RegistrationAttempt.objects.create(
                username=form.cleaned_data["username"],
                phone=form.cleaned_data["phone"],
                email=form.cleaned_data["email"],
                password_hash=make_password(form.cleaned_data["password1"]),
                code_hash=make_password(code),
                expires_at=timezone.now() + RegistrationAttempt.CODE_LIFETIME,
                request_ip=ip,
            )
            try:
                send_verification_code(attempt.phone, code)
            except Exception:
                attempt.delete()
                form.add_error(None, "Не удалось отправить код. Попробуйте позже.")
            else:
                request.session[REGISTRATION_SESSION_KEY] = attempt.pk
                return redirect("verify")

    return render(request, "users/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def verify_view(request):
    attempt_id = request.session.get(REGISTRATION_SESSION_KEY)
    if not attempt_id:
        return redirect("register")

    attempt = RegistrationAttempt.objects.filter(pk=attempt_id).first()
    if attempt is None:
        request.session.pop(REGISTRATION_SESSION_KEY, None)
        return redirect("register")

    form = VerificationCodeForm(request.POST or None)
    created_user = None
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                attempt = RegistrationAttempt.objects.select_for_update().get(pk=attempt_id)
                if attempt.is_expired:
                    form.add_error(None, "Срок действия кода истёк. Зарегистрируйтесь снова.")
                elif attempt.is_locked:
                    form.add_error(None, "Превышено число попыток. Зарегистрируйтесь снова.")
                elif not check_password(form.cleaned_data["code"], attempt.code_hash):
                    attempt.failed_attempts += 1
                    attempt.save(update_fields=["failed_attempts"])
                    form.add_error(None, "Неверный код.")
                else:
                    created_user = User(
                        username=attempt.username,
                        phone=attempt.phone,
                        email=attempt.email,
                        password=attempt.password_hash,
                        is_phone_verified=True,
                    )
                    created_user.full_clean(exclude=["last_login", "date_joined"])
                    created_user.save()
                    attempt.delete()
        except (IntegrityError, ValidationError):
            form.add_error(None, "Ник, телефон или email уже зарегистрирован.")
        else:
            if created_user is not None:
                request.session.pop(REGISTRATION_SESSION_KEY, None)
                login(request, created_user, backend="users.backends.MultiIdentifierBackend")
                return redirect("/")

    return render(request, "users/verify.html", {"form": form, "attempt": attempt})

def _profile_context(user, active_tab, form=None):
    listings = (
        Listing.objects.filter(owner=user).exclude(status=Listing.Status.DELETED)
        .select_related("city", "owner")
        .prefetch_related("images")
    )
    favorites = (
        Favorite.objects.filter(
            user=user,
            listing__status=Listing.Status.PUBLISHED,
            listing__owner__is_active=True,
        )
        .select_related("listing__city", "listing__owner")
        .prefetch_related("listing__images")
    )
    context = {
        "active_tab": active_tab,
        "profile_form": form,
        "listing_count": listings.exclude(status=Listing.Status.DRAFT).count(),
        "draft_count": listings.filter(status=Listing.Status.DRAFT).count(),
        "favorite_count": favorites.count(),
    }
    if active_tab == "listings":
        context["profile_listings"] = listings.exclude(status=Listing.Status.DRAFT)
        context["show_edit_button"] = True
    elif active_tab == "drafts":
        context["profile_listings"] = listings.filter(status=Listing.Status.DRAFT)
        context["show_edit_button"] = True
    elif active_tab == "favorites":
        context["profile_listings"] = [favorite.listing for favorite in favorites]
        context["show_remove_favorite"] = True
    return context


def public_profile_view(request, username):
    profile_user = get_object_or_404(
        User,
        username__iexact=username,
        is_active=True,
    )
    listings = (
        Listing.objects.published()
        .filter(owner=profile_user)
        .select_related("city", "owner")
        .prefetch_related("images")
        .order_by("-published_at", "-created_at")
    )
    if request.user.is_authenticated:
        listings = listings.annotate(
            is_favorite=Exists(
                Favorite.objects.filter(
                    user=request.user,
                    listing_id=OuterRef("pk"),
                )
            )
        )
    else:
        listings = listings.annotate(
            is_favorite=Value(False, output_field=BooleanField())
        )

    paginator = Paginator(listings, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "users/public_profile.html",
        {
            "profile_user": profile_user,
            "page_obj": page_obj,
            "listing_count": paginator.count,
            "show_favorite_button": True,
        },
    )


@login_required
def profile_view(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Данные профиля обновлены.")
        return redirect("profile")
    return render(
        request,
        "users/profile.html",
        _profile_context(request.user, "profile", form),
    )


@login_required
def profile_listings_view(request):
    return render(
        request,
        "users/profile.html",
        _profile_context(request.user, "listings"),
    )


@login_required
def profile_drafts_view(request):
    return render(
        request,
        "users/profile.html",
        _profile_context(request.user, "drafts"),
    )


@login_required
def favorites_view(request):
    return render(
        request,
        "users/profile.html",
        _profile_context(request.user, "favorites"),
    )


@login_required
def notifications_view(request):
    notifications = Notification.objects.filter(user=request.user).select_related("listing")
    paginator = Paginator(notifications, 20)
    return render(
        request,
        "users/notifications.html",
        {"page_obj": paginator.get_page(request.GET.get("page"))},
    )


@login_required
@require_POST
def mark_notification_read(request, public_id):
    notification = get_object_or_404(
        Notification,
        public_id=public_id,
        user=request.user,
    )
    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=("is_read", "read_at"))
    if notification.listing and notification.listing.status != Listing.Status.DELETED:
        return redirect(notification.listing.get_absolute_url())
    return redirect("notifications")


@login_required
@require_POST
def mark_all_notifications_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    return redirect("notifications")
