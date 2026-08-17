from datetime import timedelta

from django.contrib import messages
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Exists, OuterRef, Value
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_POST

from listings.models import Favorite, Listing

from .forms import (
    EmailChangeForm,
    EmailForm,
    LegalAcceptanceForm,
    LoginForm,
    NewPasswordForm,
    ProfileForm,
    RegistrationForm,
    VerificationCodeForm,
)
from .models import EmailCodeAttempt, Notification, RegistrationAttempt, User
from .security import (
    clear_login_failures,
    client_ip,
    login_is_rate_limited,
    record_login_failure,
)
from .services import generate_verification_code, send_email_code


REGISTRATION_SESSION_KEY = "registration_attempt_id"
PASSWORD_RESET_SESSION_KEY = "password_reset_attempt_id"
EMAIL_CHANGE_SESSION_KEY = "email_change_attempt_id"
MAX_EMAIL_CODES_PER_HOUR = 5


def _client_ip(request):
    return client_ip(request)


def _code_rate_limited(model, *, ip, email):
    since = timezone.now() - timedelta(hours=1)
    recent = model.objects.filter(created_at__gte=since)
    return (
        (ip and recent.filter(request_ip=ip).count() >= MAX_EMAIL_CODES_PER_HOUR)
        or recent.filter(email__iexact=email).count() >= MAX_EMAIL_CODES_PER_HOUR
    )


def _send_attempt_code(attempt, *, purpose):
    code = generate_verification_code()
    send_email_code(email=attempt.email, code=code, purpose=purpose)
    attempt.code_hash = make_password(code)
    attempt.expires_at = timezone.now() + attempt.CODE_LIFETIME
    attempt.last_sent_at = timezone.now()
    attempt.failed_attempts = 0
    attempt.save(
        update_fields=(
            "code_hash",
            "expires_at",
            "last_sent_at",
            "failed_attempts",
        )
    )


def _resend_code(request, *, model, session_key, purpose, redirect_name):
    attempt_id = request.session.get(session_key)
    attempt = model.objects.filter(pk=attempt_id).first()
    if attempt is None:
        request.session.pop(session_key, None)
        return redirect(redirect_name)
    if attempt.send_count >= attempt.MAX_SENDS:
        messages.error(request, "Достигнут лимит отправок. Начните процесс заново позже.")
        return redirect(redirect_name)
    if not attempt.can_resend:
        messages.error(request, "Новый код можно запросить через минуту.")
        return redirect(redirect_name)

    try:
        if not isinstance(attempt, EmailCodeAttempt) or attempt.user_id:
            _send_attempt_code(attempt, purpose=purpose)
        else:
            attempt.last_sent_at = timezone.now()
            attempt.expires_at = timezone.now() + attempt.CODE_LIFETIME
            attempt.failed_attempts = 0
            attempt.save(update_fields=("last_sent_at", "expires_at", "failed_attempts"))
    except Exception:
        messages.error(request, "Не удалось отправить письмо. Попробуйте позже.")
    else:
        attempt.send_count += 1
        attempt.save(update_fields=("send_count",))
        messages.success(request, "Новый код отправлен на email.")
    return redirect(redirect_name)


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("listing_list")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier = form.cleaned_data["identifier"]
        if login_is_rate_limited(request=request, identifier=identifier):
            form.add_error(
                None,
                _("Слишком много попыток входа. Повторите попытку через 15 минут."),
            )
            return render(request, "users/login.html", {"form": form}, status=429)
        user = authenticate(
            request,
            identifier=identifier,
            password=form.cleaned_data["password"],
        )
        if user is None:
            record_login_failure(request=request, identifier=identifier)
            form.add_error(None, "Неверные данные для входа.")
        else:
            clear_login_failures(request=request, identifier=identifier)
            login(request, user)
            if not form.cleaned_data["remember_me"]:
                request.session.set_expiry(0)
            next_url = request.GET.get("next")
            if not next_url or not url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                next_url = reverse("listing_list")
            if (
                user.terms_version != settings.LEGAL_TERMS_VERSION
                or user.privacy_policy_version != settings.LEGAL_PRIVACY_VERSION
                or user.terms_accepted_at is None
                or user.privacy_consent_at is None
            ):
                request.session["legal_acceptance_next"] = next_url
                return redirect("legal_acceptance")
            return redirect(next_url)

    return render(request, "users/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("listing_list")


@login_required
@require_http_methods(["GET", "POST"])
def legal_acceptance_view(request):
    form = LegalAcceptanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        accepted_at = timezone.now()
        request.user.terms_accepted_at = accepted_at
        request.user.terms_version = settings.LEGAL_TERMS_VERSION
        request.user.privacy_consent_at = accepted_at
        request.user.privacy_policy_version = settings.LEGAL_PRIVACY_VERSION
        request.user.save(
            update_fields=(
                "terms_accepted_at",
                "terms_version",
                "privacy_consent_at",
                "privacy_policy_version",
            )
        )
        next_url = request.session.pop("legal_acceptance_next", "/")
        if not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = "/"
        return redirect(next_url)
    return render(request, "users/legal_acceptance.html", {"form": form})


@require_http_methods(["GET", "POST"])
def register_view(request):
    if request.user.is_authenticated:
        return redirect("/")

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ip = _client_ip(request)
        email = form.cleaned_data["email"]
        if _code_rate_limited(RegistrationAttempt, ip=ip, email=email):
            form.add_error(None, "Слишком много запросов. Попробуйте позже.")
        else:
            code = generate_verification_code()
            accepted_at = timezone.now()
            attempt = RegistrationAttempt.objects.create(
                username=form.cleaned_data["username"],
                email=form.cleaned_data["email"],
                password_hash=make_password(form.cleaned_data["password1"]),
                code_hash=make_password(code),
                expires_at=timezone.now() + RegistrationAttempt.CODE_LIFETIME,
                request_ip=ip,
                terms_accepted_at=accepted_at,
                terms_version=settings.LEGAL_TERMS_VERSION,
                privacy_consent_at=accepted_at,
                privacy_policy_version=settings.LEGAL_PRIVACY_VERSION,
            )
            try:
                send_email_code(
                    email=attempt.email,
                    code=code,
                    purpose="registration",
                )
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
                        email=attempt.email,
                        password=attempt.password_hash,
                        is_email_verified=True,
                        terms_accepted_at=attempt.terms_accepted_at,
                        terms_version=attempt.terms_version,
                        privacy_consent_at=attempt.privacy_consent_at,
                        privacy_policy_version=attempt.privacy_policy_version,
                    )
                    created_user.full_clean(exclude=["last_login", "date_joined"])
                    created_user.save()
                    attempt.delete()
        except (IntegrityError, ValidationError):
            form.add_error(None, "Ник или email уже зарегистрирован.")
        else:
            if created_user is not None:
                request.session.pop(REGISTRATION_SESSION_KEY, None)
                login(request, created_user, backend="users.backends.MultiIdentifierBackend")
                return redirect("/")

    return render(request, "users/verify.html", {"form": form, "attempt": attempt})


@require_POST
def resend_registration_code(request):
    return _resend_code(
        request,
        model=RegistrationAttempt,
        session_key=REGISTRATION_SESSION_KEY,
        purpose="registration",
        redirect_name="verify",
    )


@require_http_methods(["GET", "POST"])
def password_reset_request(request):
    if request.user.is_authenticated:
        return redirect("profile")
    form = EmailForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ip = _client_ip(request)
        email = form.cleaned_data["email"]
        if _code_rate_limited(EmailCodeAttempt, ip=ip, email=email):
            form.add_error(None, "Слишком много запросов. Попробуйте позже.")
        else:
            user = User.objects.filter(
                email__iexact=email,
                is_email_verified=True,
                is_active=True,
            ).first()
            code = generate_verification_code()
            attempt = EmailCodeAttempt.objects.create(
                purpose=EmailCodeAttempt.Purpose.PASSWORD_RESET,
                email=email,
                user=user,
                code_hash=make_password(code),
                expires_at=timezone.now() + EmailCodeAttempt.CODE_LIFETIME,
                request_ip=ip,
            )
            try:
                if user is not None:
                    send_email_code(
                        email=email,
                        code=code,
                        purpose="password_reset",
                    )
            except Exception:
                attempt.delete()
                form.add_error(None, "Не удалось отправить письмо. Попробуйте позже.")
            else:
                request.session[PASSWORD_RESET_SESSION_KEY] = attempt.pk
                return redirect("password_reset_verify")
    return render(request, "users/password_reset_request.html", {"form": form})


@require_http_methods(["GET", "POST"])
def password_reset_verify(request):
    attempt_id = request.session.get(PASSWORD_RESET_SESSION_KEY)
    attempt = EmailCodeAttempt.objects.filter(
        pk=attempt_id,
        purpose=EmailCodeAttempt.Purpose.PASSWORD_RESET,
    ).first()
    if attempt is None:
        return redirect("password_reset")

    form = VerificationCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            attempt = EmailCodeAttempt.objects.select_for_update().get(pk=attempt.pk)
            if attempt.is_expired:
                form.add_error(None, "Срок действия кода истёк. Запросите новый код.")
            elif attempt.is_locked:
                form.add_error(None, "Превышено число попыток. Начните восстановление заново.")
            elif (
                not check_password(form.cleaned_data["code"], attempt.code_hash)
                or attempt.user_id is None
            ):
                attempt.failed_attempts += 1
                attempt.save(update_fields=("failed_attempts",))
                form.add_error(None, "Неверный код.")
            else:
                attempt.verified_at = timezone.now()
                attempt.save(update_fields=("verified_at",))
                return redirect("password_reset_new")
    return render(
        request,
        "users/password_reset_verify.html",
        {"form": form, "attempt": attempt},
    )


@require_POST
def resend_password_reset_code(request):
    return _resend_code(
        request,
        model=EmailCodeAttempt,
        session_key=PASSWORD_RESET_SESSION_KEY,
        purpose="password_reset",
        redirect_name="password_reset_verify",
    )


@require_http_methods(["GET", "POST"])
def password_reset_new(request):
    attempt_id = request.session.get(PASSWORD_RESET_SESSION_KEY)
    attempt = EmailCodeAttempt.objects.select_related("user").filter(
        pk=attempt_id,
        purpose=EmailCodeAttempt.Purpose.PASSWORD_RESET,
        verified_at__isnull=False,
    ).first()
    if attempt is None or attempt.is_expired or attempt.user_id is None:
        request.session.pop(PASSWORD_RESET_SESSION_KEY, None)
        return redirect("password_reset")

    form = NewPasswordForm(request.POST or None, user=attempt.user)
    if request.method == "POST" and form.is_valid():
        user = attempt.user
        user.set_password(form.cleaned_data["password1"])
        user.save(update_fields=("password",))
        EmailCodeAttempt.objects.filter(
            user=user,
            purpose=EmailCodeAttempt.Purpose.PASSWORD_RESET,
        ).delete()
        request.session.pop(PASSWORD_RESET_SESSION_KEY, None)
        messages.success(request, "Пароль изменён. Теперь можно войти.")
        return redirect("login")
    return render(request, "users/password_reset_new.html", {"form": form})

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
        {
            **_profile_context(request.user, "profile", form),
            "email_change_form": EmailChangeForm(user=request.user),
        },
    )


@login_required
@require_POST
def email_change_request(request):
    form = EmailChangeForm(request.POST, user=request.user)
    if not form.is_valid():
        profile_form = ProfileForm(instance=request.user)
        return render(
            request,
            "users/profile.html",
            {
                **_profile_context(request.user, "profile", profile_form),
                "email_change_form": form,
            },
            status=400,
        )

    ip = _client_ip(request)
    email = form.cleaned_data["email"]
    if _code_rate_limited(EmailCodeAttempt, ip=ip, email=email):
        form.add_error(None, "Слишком много запросов. Попробуйте позже.")
        profile_form = ProfileForm(instance=request.user)
        return render(
            request,
            "users/profile.html",
            {
                **_profile_context(request.user, "profile", profile_form),
                "email_change_form": form,
            },
            status=429,
        )

    code = generate_verification_code()
    attempt = EmailCodeAttempt.objects.create(
        purpose=EmailCodeAttempt.Purpose.EMAIL_CHANGE,
        email=email,
        user=request.user,
        code_hash=make_password(code),
        expires_at=timezone.now() + EmailCodeAttempt.CODE_LIFETIME,
        request_ip=ip,
    )
    try:
        send_email_code(email=email, code=code, purpose="email_change")
    except Exception:
        attempt.delete()
        form.add_error(None, "Не удалось отправить письмо. Попробуйте позже.")
        profile_form = ProfileForm(instance=request.user)
        return render(
            request,
            "users/profile.html",
            {
                **_profile_context(request.user, "profile", profile_form),
                "email_change_form": form,
            },
            status=503,
        )

    request.session[EMAIL_CHANGE_SESSION_KEY] = attempt.pk
    return redirect("email_change_verify")


@login_required
@require_http_methods(["GET", "POST"])
def email_change_verify(request):
    attempt_id = request.session.get(EMAIL_CHANGE_SESSION_KEY)
    attempt = EmailCodeAttempt.objects.filter(
        pk=attempt_id,
        purpose=EmailCodeAttempt.Purpose.EMAIL_CHANGE,
        user=request.user,
    ).first()
    if attempt is None:
        return redirect("profile")

    form = VerificationCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                attempt = EmailCodeAttempt.objects.select_for_update().get(pk=attempt.pk)
                if attempt.is_expired:
                    form.add_error(None, "Срок действия кода истёк. Запросите новый код.")
                elif attempt.is_locked:
                    form.add_error(None, "Превышено число попыток. Начните смену email заново.")
                elif not check_password(form.cleaned_data["code"], attempt.code_hash):
                    attempt.failed_attempts += 1
                    attempt.save(update_fields=("failed_attempts",))
                    form.add_error(None, "Неверный код.")
                elif User.objects.exclude(pk=request.user.pk).filter(email__iexact=attempt.email).exists():
                    form.add_error(None, "Этот email уже используется.")
                else:
                    request.user.email = attempt.email
                    request.user.is_email_verified = True
                    request.user.save(update_fields=("email", "is_email_verified"))
                    EmailCodeAttempt.objects.filter(
                        user=request.user,
                        purpose=EmailCodeAttempt.Purpose.EMAIL_CHANGE,
                    ).delete()
        except IntegrityError:
            form.add_error(None, "Этот email уже используется.")
        else:
            if not form.errors:
                request.session.pop(EMAIL_CHANGE_SESSION_KEY, None)
                messages.success(request, "Новый email подтверждён.")
                return redirect("profile")
    return render(
        request,
        "users/email_change_verify.html",
        {"form": form, "attempt": attempt},
    )


@login_required
@require_POST
def resend_email_change_code(request):
    return _resend_code(
        request,
        model=EmailCodeAttempt,
        session_key=EMAIL_CHANGE_SESSION_KEY,
        purpose="email_change",
        redirect_name="email_change_verify",
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
