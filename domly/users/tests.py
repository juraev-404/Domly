from datetime import timedelta
from io import BytesIO, StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from chat.models import Conversation, Message as ChatMessage
from listings.models import City, Favorite, Listing, ListingReport

from .models import EmailCodeAttempt, Notification, RegistrationAttempt, User


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="notification_user", phone="+992900000080", email="notification-user@example.com", password="test-password-123"
        )
        cls.other = User.objects.create_user(
            username="notification_other", phone="+992900000081", email="notification-other@example.com", password="test-password-123"
        )
        cls.listing = Listing.objects.create(
            owner=cls.user,
            city=City.objects.get(slug="dushanbe"),
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Объявление с уведомлением",
            description="Достаточно подробное описание объекта для тестирования уведомлений.",
            price="2500.00",
            address="Улица Айни, 8",
        )

    def test_user_sees_only_own_notifications_and_unread_count(self):
        own = Notification.objects.create(
            user=self.user, listing=self.listing, kind=Notification.Kind.LISTING_APPROVED
        )
        Notification.objects.create(
            user=self.other, listing=self.listing, kind=Notification.Kind.LISTING_REJECTED
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, own.get_kind_display())
        self.assertNotContains(response, Notification.Kind.LISTING_REJECTED.label)
        self.assertEqual(response.context["unread_notification_count"], 1)

    def test_read_action_is_post_only_and_scoped_to_owner(self):
        own = Notification.objects.create(
            user=self.user, listing=self.listing, kind=Notification.Kind.LISTING_APPROVED
        )
        url = reverse("mark_notification_read", args=(own.public_id,))
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url), self.listing.get_absolute_url())
        own.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertIsNotNone(own.read_at)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Domly <no-reply@domly.test>",
)
class AuthenticationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="domly_user",
            phone="+992900001122",
            email="owner@example.com",
            password="SafePassword-934",
            is_phone_verified=True,
            is_email_verified=True,
            terms_accepted_at=timezone.now(),
            terms_version="2026-08-17",
            privacy_consent_at=timezone.now(),
            privacy_policy_version="2026-08-17",
        )

    def test_login_with_username_or_verified_email(self):
        for identifier in ("domly_user", "OWNER@example.com"):
            response = self.client.post(
                reverse("login"),
                {"identifier": identifier, "password": "SafePassword-934"},
            )
            self.assertRedirects(response, "/")
            self.client.logout()

    def test_phone_is_not_an_active_login_identifier(self):
        response = self.client.post(
            reverse("login"),
            {"identifier": self.user.phone, "password": "SafePassword-934"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_existing_user_must_accept_current_legal_documents_after_login(self):
        self.user.terms_accepted_at = None
        self.user.terms_version = ""
        self.user.privacy_consent_at = None
        self.user.privacy_policy_version = ""
        self.user.save(
            update_fields=(
                "terms_accepted_at",
                "terms_version",
                "privacy_consent_at",
                "privacy_policy_version",
            )
        )

        response = self.client.post(
            reverse("login"),
            {"identifier": "domly_user", "password": "SafePassword-934"},
        )
        self.assertRedirects(response, reverse("legal_acceptance"))
        response = self.client.post(
            reverse("legal_acceptance"),
            {"accept_terms": "on", "privacy_consent": "on"},
        )
        self.assertRedirects(response, "/")
        self.user.refresh_from_db()
        self.assertEqual(self.user.terms_version, "2026-08-17")
        self.assertEqual(self.user.privacy_policy_version, "2026-08-17")
        self.assertIsNotNone(self.user.terms_accepted_at)
        self.assertIsNotNone(self.user.privacy_consent_at)

    @patch("users.views.generate_verification_code", return_value="123456")
    def test_registration_requires_email_code(self, generate_code):
        response = self.client.post(
            reverse("register"),
            {
                "username": "new_owner",
                "email": "new@example.com",
                "password1": "AnotherSafePassword-934",
                "password2": "AnotherSafePassword-934",
                "accept_terms": "on",
                "privacy_consent": "on",
            },
        )
        self.assertRedirects(response, reverse("verify"))
        self.assertFalse(User.objects.filter(username="new_owner").exists())
        self.assertEqual(RegistrationAttempt.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])
        self.assertIn("123456", mail.outbox[0].body)

        response = self.client.post(reverse("verify"), {"code": "123456"})
        self.assertRedirects(response, "/")
        user = User.objects.get(username="new_owner")
        self.assertTrue(user.is_email_verified)
        self.assertIsNone(user.phone)
        self.assertTrue(user.check_password("AnotherSafePassword-934"))
        self.assertIsNotNone(user.terms_accepted_at)
        self.assertEqual(user.terms_version, "2026-08-17")
        self.assertIsNotNone(user.privacy_consent_at)
        self.assertEqual(user.privacy_policy_version, "2026-08-17")
        self.assertEqual(RegistrationAttempt.objects.count(), 0)

    def test_registration_requires_legal_acceptance_and_privacy_consent(self):
        data = {
            "username": "consent_owner",
            "email": "consent@example.com",
            "password1": "AnotherSafePassword-934",
            "password2": "AnotherSafePassword-934",
        }
        response = self.client.post(reverse("register"), data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "accept_terms",
            "Для регистрации необходимо принять условия сервиса.",
        )
        self.assertFormError(
            response.context["form"],
            "privacy_consent",
            "Для регистрации необходимо дать согласие на обработку данных.",
        )
        self.assertFalse(RegistrationAttempt.objects.exists())

    @patch("users.views.generate_verification_code", side_effect=("123456", "654321"))
    def test_registration_code_resend_has_cooldown_and_replaces_code(self, generate_code):
        self.client.post(
            reverse("register"),
            {
                "username": "resend_owner",
                "email": "resend@example.com",
                "password1": "AnotherSafePassword-934",
                "password2": "AnotherSafePassword-934",
                "accept_terms": "on",
                "privacy_consent": "on",
            },
        )
        attempt = RegistrationAttempt.objects.get()

        cooldown_response = self.client.post(reverse("resend_registration_code"))
        self.assertRedirects(cooldown_response, reverse("verify"))
        self.assertEqual(len(mail.outbox), 1)

        attempt.last_sent_at = timezone.now() - timedelta(seconds=61)
        attempt.save(update_fields=("last_sent_at",))
        resend_response = self.client.post(reverse("resend_registration_code"))
        self.assertRedirects(resend_response, reverse("verify"))
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("654321", mail.outbox[1].body)
        self.assertEqual(self.client.post(reverse("verify"), {"code": "123456"}).status_code, 200)
        self.assertRedirects(
            self.client.post(reverse("verify"), {"code": "654321"}),
            "/",
        )

    @patch("users.views.generate_verification_code", return_value="123456")
    def test_registration_locks_after_five_wrong_codes(self, generate_code):
        self.client.post(
            reverse("register"),
            {
                "username": "locked_owner",
                "email": "locked@example.com",
                "password1": "AnotherSafePassword-934",
                "password2": "AnotherSafePassword-934",
                "accept_terms": "on",
                "privacy_consent": "on",
            },
        )
        for _ in range(5):
            self.client.post(reverse("verify"), {"code": "000000"})
        response = self.client.post(reverse("verify"), {"code": "123456"})
        self.assertContains(response, "Превышено число попыток")
        self.assertFalse(User.objects.filter(username="locked_owner").exists())

    @patch("users.views.generate_verification_code", return_value="123456")
    def test_password_reset_changes_password_after_email_code(self, generate_code):
        response = self.client.post(
            reverse("password_reset"),
            {"email": "OWNER@example.com"},
        )
        self.assertRedirects(response, reverse("password_reset_verify"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("123456", mail.outbox[0].body)

        self.assertRedirects(
            self.client.post(reverse("password_reset_verify"), {"code": "123456"}),
            reverse("password_reset_new"),
        )
        self.assertRedirects(
            self.client.post(
                reverse("password_reset_new"),
                {
                    "password1": "CompletelyNewPassword-934",
                    "password2": "CompletelyNewPassword-934",
                },
            ),
            reverse("login"),
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("CompletelyNewPassword-934"))
        self.assertFalse(EmailCodeAttempt.objects.exists())

    def test_password_reset_does_not_reveal_unknown_email(self):
        known = self.client.post(reverse("password_reset"), {"email": self.user.email})
        self.client.cookies.clear()
        mail.outbox.clear()
        unknown = self.client.post(reverse("password_reset"), {"email": "missing@example.com"})

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.url, unknown.url)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailCodeAttempt.objects.filter(user__isnull=True).count(), 1)

    def test_unverified_email_cannot_be_used_for_login_or_password_reset(self):
        self.user.is_email_verified = False
        self.user.save(update_fields=("is_email_verified",))

        login_response = self.client.post(
            reverse("login"),
            {"identifier": self.user.email, "password": "SafePassword-934"},
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertFalse(login_response.wsgi_request.user.is_authenticated)

        self.client.post(reverse("password_reset"), {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 0)

    def test_email_rate_limit_is_not_bypassed_by_changing_ip(self):
        for index in range(5):
            self.client.post(
                reverse("password_reset"),
                {"email": "limited@example.com"},
                REMOTE_ADDR=f"192.0.2.{index + 1}",
            )

        response = self.client.post(
            reverse("password_reset"),
            {"email": "limited@example.com"},
            REMOTE_ADDR="198.51.100.1",
        )

        self.assertContains(response, "Слишком много запросов", status_code=200)
        self.assertEqual(
            EmailCodeAttempt.objects.filter(email="limited@example.com").count(),
            5,
        )

    def test_cleanup_command_removes_old_expired_attempts(self):
        attempt = EmailCodeAttempt.objects.create(
            purpose=EmailCodeAttempt.Purpose.PASSWORD_RESET,
            email="expired@example.com",
            code_hash="unused",
            expires_at=timezone.now() - timedelta(days=2),
        )

        call_command("cleanup_email_codes", stdout=StringIO())

        self.assertFalse(EmailCodeAttempt.objects.filter(pk=attempt.pk).exists())

    def test_wrong_password_does_not_login(self):
        response = self.client.post(
            reverse("login"),
            {"identifier": "domly_user", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    @override_settings(LOGIN_RATE_LIMIT_ATTEMPTS=3, LOGIN_RATE_LIMIT_WINDOW=900)
    def test_login_is_limited_by_identifier_even_when_ip_changes(self):
        for index in range(3):
            response = self.client.post(
                reverse("login"),
                {"identifier": "domly_user", "password": "wrong"},
                REMOTE_ADDR=f"192.0.2.{index + 1}",
            )
            self.assertEqual(response.status_code, 200)

        response = self.client.post(
            reverse("login"),
            {"identifier": "domly_user", "password": "SafePassword-934"},
            REMOTE_ADDR="198.51.100.20",
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Слишком много попыток входа", status_code=429)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    @override_settings(TRUST_X_REAL_IP=True)
    def test_real_ip_is_only_trusted_from_local_reverse_proxy(self):
        from users.security import client_ip

        proxied = self.client.get(
            reverse("login"),
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_REAL_IP="203.0.113.15",
        )
        direct = self.client.get(
            reverse("login"),
            REMOTE_ADDR="198.51.100.8",
            HTTP_X_REAL_IP="203.0.113.99",
        )
        unix_socket_proxy = self.client.get(
            reverse("login"),
            REMOTE_ADDR="",
            HTTP_X_REAL_IP="203.0.113.16",
        )

        self.assertEqual(client_ip(proxied.wsgi_request), "203.0.113.15")
        self.assertEqual(client_ip(direct.wsgi_request), "198.51.100.8")
        self.assertEqual(client_ip(unix_socket_proxy.wsgi_request), "203.0.113.16")


class ModeratorRoleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.regular_user = User.objects.create_user(
            username="regular_user",
            phone="+992900001123",
            email="regular@example.com",
            password="SafePassword-934",
        )
        cls.moderator = User.objects.create_user(
            username="moderator_user",
            phone="+992900001124",
            email="moderator@example.com",
            password="SafePassword-934",
            is_moderator=True,
        )
        cls.superuser = User.objects.create_superuser(
            username="admin_user",
            phone="+992900001125",
            email="admin@example.com",
            password="SafePassword-934",
        )

    def test_new_user_is_not_moderator_by_default(self):
        self.assertFalse(self.regular_user.is_moderator)

    def test_admin_can_edit_moderator_flag(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("admin:users_user_change", args=(self.regular_user.pk,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="is_moderator"')

    def test_regular_user_sees_create_links(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse("listing_list"))

        self.assertContains(response, f'href="{reverse("create")}"', count=2)
        self.assertNotContains(response, f'href="{reverse("moderation")}"')

    def test_moderator_sees_moderation_links(self):
        self.client.force_login(self.moderator)

        response = self.client.get(reverse("listing_list"))

        self.assertContains(response, f'href="{reverse("moderation")}"', count=2)
        self.assertNotContains(response, f'href="{reverse("create")}"')

    def test_moderator_can_open_moderation_section(self):
        self.client.force_login(self.moderator)

        response = self.client.get(reverse("moderation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Доступ модератора")

    def test_regular_user_cannot_open_moderation_section(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse("moderation"))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(reverse("moderation"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('moderation')}",
        )


class ProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="profile_user",
            phone="+992900001130",
            email="profile@example.com",
            password="SafePassword-934",
            is_phone_verified=True,
            is_email_verified=True,
        )
        cls.owner = User.objects.create_user(
            username="profile_owner",
            phone="+992900001131",
            password="SafePassword-934",
        )
        city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.user,
            "city": city,
            "deal_type": Listing.DealType.SALE,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Описание для личного кабинета.",
            "price": "200000.00",
            "address": "Улица Айни, 4",
        }
        cls.draft = Listing.objects.create(
            **common, title="Личный черновик", status=Listing.Status.DRAFT
        )
        cls.published = Listing.objects.create(
            **common, title="Личное объявление", status=Listing.Status.PUBLISHED
        )
        cls.saved = Listing.objects.create(
            **{**common, "owner": cls.owner},
            title="Сохранённое объявление",
            status=Listing.Status.PUBLISHED,
        )
        Favorite.objects.create(user=cls.user, listing=cls.saved)

    def setUp(self):
        self.client.force_login(self.user)

    def test_profile_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("profile"))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('profile')}")

    def test_profile_data_can_be_edited_without_changing_verified_contacts(self):
        response = self.client.post(
            reverse("profile"),
            {
                "username": "updated_profile",
                "first_name": "Али",
                "last_name": "Каримов",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "updated_profile")
        self.assertEqual(self.user.email, "profile@example.com")
        self.assertEqual(self.user.phone, "+992900001130")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("users.views.generate_verification_code", return_value="123456")
    def test_email_change_only_applies_after_code(self, generate_code):
        response = self.client.post(
            reverse("email_change_request"),
            {"email": "NEW@example.com", "current_password": "SafePassword-934"},
        )
        self.assertRedirects(response, reverse("email_change_verify"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "profile@example.com")
        self.assertIn("123456", mail.outbox[0].body)

        self.assertRedirects(
            self.client.post(reverse("email_change_verify"), {"code": "123456"}),
            reverse("profile"),
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertTrue(self.user.is_email_verified)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_change_rejects_wrong_current_password(self):
        response = self.client.post(
            reverse("email_change_request"),
            {"email": "new@example.com", "current_password": "wrong"},
        )

        self.assertContains(response, "Неверный текущий пароль", status_code=400)
        self.assertFalse(EmailCodeAttempt.objects.filter(user=self.user).exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_avatar_input_hides_storage_name_and_keeps_clear_action(self):
        self.user.avatar.name = "avatars/private-avatar-name.jpg"
        self.user.save(update_fields=("avatar",))

        response = self.client.get(reverse("profile"))

        self.assertNotContains(response, "На данный момент:")
        self.assertNotContains(response, f'href="{self.user.avatar.url}"')
        self.assertContains(response, "data-avatar-clear")
        self.assertContains(response, 'name="avatar-clear"')
        self.assertContains(response, "data-file-picker")
        self.assertContains(response, "data-file-picker-input")
        self.assertContains(response, "Выбрать фото")
        self.assertContains(response, "Фото не выбрано")
        self.assertContains(response, 'src="/static/file_picker.js?v=20260817"')
        self.assertContains(response, "Удалить")
        self.assertNotContains(response, "Очистить")

        clear_response = self.client.post(
            reverse("profile"),
            {
                "avatar-clear": "on",
                "username": self.user.username,
                "first_name": self.user.first_name,
                "last_name": self.user.last_name,
                "email": self.user.email,
            },
        )

        self.assertRedirects(clear_response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)

    def test_uploaded_avatar_is_optimized_and_metadata_is_removed(self):
        source = BytesIO()
        exif = Image.Exif()
        exif[315] = "private metadata"
        Image.new("RGB", (1200, 800), color="green").save(
            source,
            format="JPEG",
            exif=exif,
        )
        upload = SimpleUploadedFile(
            "avatar-with-metadata.jpg",
            source.getvalue(),
            content_type="image/jpeg",
        )

        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            self.user.avatar = upload
            self.user.save(update_fields=("avatar",))
            self.user.refresh_from_db()

            self.assertTrue(self.user.avatar.name.endswith(".webp"))
            with Image.open(self.user.avatar.path) as avatar:
                self.assertEqual(avatar.format, "WEBP")
                self.assertLessEqual(max(avatar.size), 512)
                self.assertFalse(avatar.getexif())

    def test_listing_tabs_separate_drafts_and_other_statuses(self):
        listings_response = self.client.get(reverse("profile_listings"))
        drafts_response = self.client.get(reverse("profile_drafts"))

        self.assertContains(listings_response, self.published.title)
        self.assertNotContains(listings_response, self.draft.title)
        self.assertContains(drafts_response, self.draft.title)
        self.assertNotContains(drafts_response, self.published.title)

    def test_favorites_tab_shows_saved_listing(self):
        response = self.client.get(reverse("favorites"))

        self.assertContains(response, self.saved.title)
        self.assertNotContains(response, self.published.title)

    def test_logout_icon_is_visible_only_on_profile_page(self):
        profile_response = self.client.get(reverse("profile"))
        catalog_response = self.client.get(reverse("listing_list"))
        listings_response = self.client.get(reverse("profile_listings"))

        self.assertContains(profile_response, 'title="Выход из аккаунта"')
        self.assertContains(profile_response, "data-logout-form", count=2)
        self.assertContains(profile_response, "data-logout-dialog")
        self.assertContains(profile_response, "Выйти из аккаунта?")
        self.assertNotContains(catalog_response, 'title="Выход из аккаунта"')
        self.assertNotContains(listings_response, 'title="Выход из аккаунта"')
        self.assertNotContains(listings_response, "data-logout-dialog")


class AccountDeletionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="delete_me",
            email="delete-me@example.com",
            password="SafePassword-934",
            is_email_verified=True,
        )
        cls.other = User.objects.create_user(
            username="remaining_user",
            email="remaining@example.com",
            password="SafePassword-934",
        )
        city = City.objects.get(slug="dushanbe")
        cls.owned_listing = Listing.objects.create(
            owner=cls.user,
            city=city,
            deal_type=Listing.DealType.SALE,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Объявление удаляемого пользователя",
            description="Описание",
            price="100000.00",
            address="Душанбе",
        )
        cls.other_listing = Listing.objects.create(
            owner=cls.other,
            city=city,
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Объявление другого пользователя",
            description="Описание",
            price="3000.00",
            address="Душанбе",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_delete_account_page_requires_login_and_is_linked_from_profile(self):
        profile = self.client.get(reverse("profile"))
        self.assertContains(profile, reverse("delete_account"))
        self.assertContains(profile, "Удалить аккаунт")

        self.client.logout()
        response = self.client.get(reverse("delete_account"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('delete_account')}",
        )

    def test_delete_account_page_is_translated(self):
        with translation.override("en"):
            english = self.client.get(reverse("delete_account"))
            english_help = self.client.get(reverse("help"))
        self.assertContains(english, "Delete permanently")
        self.assertContains(english, "Enter your username to confirm")
        self.assertContains(english_help, "Open the account deletion section")

        with translation.override("tg"):
            tajik = self.client.get(reverse("delete_account"))
            tajik_help = self.client.get(reverse("help"))
        self.assertContains(tajik, "Тамоман нест кардан")
        self.assertContains(tajik, "Номи корбарии худро барои тасдиқ ворид кунед")
        self.assertContains(tajik_help, "Дар профил бахши нест кардани аккаунтро кушоед")

    def test_delete_account_requires_correct_password_and_exact_username(self):
        wrong_password = self.client.post(
            reverse("delete_account"),
            {"current_password": "wrong", "username": self.user.username},
        )
        self.assertContains(wrong_password, "Неверный текущий пароль")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

        wrong_username = self.client.post(
            reverse("delete_account"),
            {"current_password": "SafePassword-934", "username": "DELETE_ME"},
        )
        self.assertContains(wrong_username, "Ник не совпадает")
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_staff_account_cannot_be_deleted_through_public_page(self):
        self.user.is_staff = True
        self.user.save(update_fields=("is_staff",))

        response = self.client.post(
            reverse("delete_account"),
            {
                "current_password": "SafePassword-934",
                "username": self.user.username,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_confirmed_deletion_removes_owned_data_and_anonymizes_report(self):
        conversation = Conversation.objects.create()
        conversation.participants.add(self.user, self.other)
        ChatMessage.objects.create(
            conversation=conversation,
            sender=self.other,
            body="Сообщение в удаляемом диалоге",
        )
        report = ListingReport.objects.create(
            listing=self.other_listing,
            reporter=self.user,
            reason=ListingReport.Reason.OTHER,
            details="Жалоба должна остаться без автора",
        )
        Favorite.objects.create(user=self.user, listing=self.other_listing)
        RegistrationAttempt.objects.create(
            username=self.user.username,
            email=self.user.email,
            password_hash="unused",
            code_hash="unused",
            expires_at=timezone.now() + timedelta(minutes=10),
            terms_accepted_at=timezone.now(),
            terms_version=settings.LEGAL_TERMS_VERSION,
            privacy_consent_at=timezone.now(),
            privacy_policy_version=settings.LEGAL_PRIVACY_VERSION,
        )
        user_pk = self.user.pk
        listing_pk = self.owned_listing.pk

        response = self.client.post(
            reverse("delete_account"),
            {
                "current_password": "SafePassword-934",
                "username": self.user.username,
            },
        )

        self.assertRedirects(response, reverse("listing_list"))
        self.assertFalse(User.objects.filter(pk=user_pk).exists())
        self.assertFalse(Listing.objects.filter(pk=listing_pk).exists())
        self.assertFalse(Conversation.objects.filter(pk=conversation.pk).exists())
        self.assertFalse(RegistrationAttempt.objects.filter(email="delete-me@example.com").exists())
        report.refresh_from_db()
        self.assertIsNone(report.reporter)
        self.assertNotIn("_auth_user_id", self.client.session)


class PublicProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user(
            username="public_owner",
            first_name="Али",
            last_name="Каримов",
            phone="+992900001140",
            email="private-owner@example.com",
            password="SafePassword-934",
        )
        city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.author,
            "city": city,
            "deal_type": Listing.DealType.RENT,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Описание объявления публичного автора.",
            "price": "3000.00",
            "address": "Улица Рудаки, 10",
        }
        cls.published = Listing.objects.create(
            **common,
            title="Опубликованная квартира автора",
            status=Listing.Status.PUBLISHED,
        )
        cls.draft = Listing.objects.create(
            **common,
            title="Черновик автора",
            status=Listing.Status.DRAFT,
        )

    def test_public_profile_shows_safe_author_data_and_published_listings(self):
        response = self.client.get(
            reverse("public_profile", args=(self.author.username,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Али Каримов")
        self.assertContains(response, f"@{self.author.username}")
        self.assertContains(response, self.published.title)
        self.assertNotContains(response, self.draft.title)
        self.assertNotContains(response, self.author.phone)
        self.assertNotContains(response, self.author.email)

    def test_listing_owner_links_to_public_profile(self):
        response = self.client.get(
            reverse("listing_detail", args=(self.published.public_id,))
        )

        self.assertContains(
            response,
            reverse("public_profile", args=(self.author.username,)),
        )

    def test_inactive_author_profile_is_not_public(self):
        self.author.is_active = False
        self.author.save(update_fields=("is_active",))

        response = self.client.get(
            reverse("public_profile", args=(self.author.username,))
        )

        self.assertEqual(response.status_code, 404)


class LocalizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="localized_user",
            email="localized@example.com",
            password="SafePassword-934",
            is_email_verified=True,
        )

    def setUp(self):
        translation.activate("ru")

    def test_russian_is_the_default_language(self):
        with translation.override("ru"):
            response = self.client.get(reverse("listing_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html lang="ru" data-theme="light">', html=False)
        self.assertContains(response, "Помощь")
        self.assertContains(response, 'data-language-switcher="desktop"')
        self.assertContains(response, 'data-theme="light"', html=False)
        self.assertContains(response, 'data-theme-toggle')
        self.assertContains(response, 'data-enable-dark-label="Включить тёмную тему"')
        self.assertContains(
            response, 'src="/static/theme.js?v=20260817"', html=False
        )

    def test_theme_script_switches_between_moon_and_sun_icons(self):
        theme_script = (settings.BASE_DIR / "static" / "theme.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('darkIcon.toggleAttribute("hidden", isDark)', theme_script)
        self.assertIn('lightIcon.toggleAttribute("hidden", !isDark)', theme_script)

    def test_login_form_labels_and_invalid_credentials_follow_active_language(self):
        cases = {
            "en": {
                "heading": "Sign in",
                "identifier": "Username or email",
                "password": "Password",
                "remember": "Remember me",
                "error": "The sign-in details are incorrect.",
            },
            "tg": {
                "heading": "Воридшавӣ",
                "identifier": "Номи корбарӣ ё почтаи электронӣ",
                "password": "Рамз",
                "remember": "Маро дар хотир нигоҳ дор",
                "error": "Маълумоти воридшавӣ нодуруст аст.",
            },
        }

        for language, expected in cases.items():
            with self.subTest(language=language):
                cache.clear()
                self.client.cookies.clear()
                with translation.override(language):
                    login_url = reverse("login")
                response = self.client.post(
                    login_url,
                    {"identifier": "missing-user", "password": "wrong-password"},
                )

                self.assertEqual(response.status_code, 200)
                for text in expected.values():
                    self.assertContains(response, text)
                self.assertNotContains(response, "Неверные данные для входа.")
                self.assertNotContains(response, "Ник или email")
                self.assertNotContains(response, "Запомнить меня")

    def test_file_pickers_are_translated_in_profile_and_listing_form(self):
        self.client.force_login(self.user)
        with translation.override("en"):
            english_profile_url = reverse("profile")
        with translation.override("tg"):
            tajik_create_url = reverse("create")

        english_profile = self.client.get(english_profile_url)
        self.assertContains(english_profile, "Choose photo")
        self.assertContains(english_profile, "No photo selected")

        tajik_create = self.client.get(tajik_create_url)
        self.assertContains(tajik_create, "Интихоби аксҳо")
        self.assertContains(tajik_create, "Аксҳо интихоб нашудаанд")

    def test_language_endpoint_persists_english_and_translates_navigation(self):
        with translation.override("ru"):
            language_url = reverse("set_language")
            russian_home = reverse("listing_list")
        with translation.override("en"):
            english_home = reverse("listing_list")
        response = self.client.post(
            language_url,
            {"language": "en", "next": russian_home},
        )

        self.assertRedirects(response, english_home)
        self.assertEqual(response.cookies["django_language"].value, "en")
        page = self.client.get(english_home)
        self.assertContains(page, '<html lang="en" data-theme="light">', html=False)
        self.assertContains(page, "Help")
        self.assertContains(page, "Filter")
        self.assertContains(page, "Search")
        self.assertContains(page, "Rent")
        self.assertContains(page, "Apartment")
        self.assertContains(page, "Dushanbe")
        self.assertContains(page, "Khujand")
        self.assertContains(page, "Privacy Policy")
        self.assertContains(page, "Terms of Use")
        self.assertContains(page, "Posting Rules")
        self.assertContains(page, 'data-enable-dark-label="Enable dark theme"')
        self.assertContains(page, 'data-enable-light-label="Enable light theme"')
        self.assertContains(page, 'value="Худжанд"', html=False)
        self.assertNotContains(page, ">Худжанд<", html=False)

    def test_language_switcher_can_change_language_repeatedly(self):
        with translation.override("ru"):
            language_url = reverse("set_language")
            russian_home = reverse("listing_list")
        with translation.override("en"):
            english_home = reverse("listing_list")
        with translation.override("tg"):
            tajik_home = reverse("listing_list")

        page = self.client.get(russian_home)
        options = {
            option["code"]: option["url"]
            for option in page.context["language_switch_options"]
        }
        self.assertEqual(options["en"], english_home)

        response = self.client.post(
            language_url,
            {"language": "en", "next": options["en"]},
        )
        self.assertRedirects(response, english_home)

        page = self.client.get(english_home)
        options = {
            option["code"]: option["url"]
            for option in page.context["language_switch_options"]
        }
        self.assertEqual(options["tg"], tajik_home)

        response = self.client.post(
            language_url,
            {"language": "tg", "next": options["tg"]},
        )
        self.assertRedirects(response, tajik_home)

        page = self.client.get(tajik_home)
        options = {
            option["code"]: option["url"]
            for option in page.context["language_switch_options"]
        }
        self.assertEqual(options["ru"], russian_home)

        response = self.client.post(
            language_url,
            {"language": "ru", "next": options["ru"]},
        )
        self.assertRedirects(response, russian_home)

    def test_tajik_profile_has_triangle_mobile_switcher(self):
        self.client.force_login(self.user)
        with translation.override("ru"):
            language_url = reverse("set_language")
            russian_profile = reverse("profile")
        with translation.override("tg"):
            tajik_profile = reverse("profile")
            tajik_catalog = reverse("listing_list")
        self.client.post(
            language_url,
            {"language": "tg", "next": russian_profile},
        )

        response = self.client.get(tajik_profile)
        self.assertContains(response, '<html lang="tg" data-theme="light">', html=False)
        self.assertContains(response, "Маълумоти шахсӣ")
        self.assertContains(response, "Захира кардани тағйирот")
        self.assertContains(response, 'data-language-switcher="mobile"')
        self.assertContains(response, 'data-enable-dark-label="Фаъол кардани реҷаи торик"')
        self.assertContains(response, 'data-enable-light-label="Фаъол кардани реҷаи равшан"')
        self.assertContains(response, 'data-theme-toggle', count=2)
        self.assertContains(response, "grid-cols-2 grid-rows-2")
        self.assertContains(response, 'fill="#198a3b"')

        catalog = self.client.get(tajik_catalog)
        self.assertContains(catalog, "Хуҷанд")
        self.assertContains(catalog, "Кӯлоб")
        self.assertContains(catalog, "Ҷустуҷӯ")
        self.assertContains(catalog, "Навтарин")
        self.assertContains(catalog, "Арзонтарин")
        self.assertContains(catalog, "Қиматтарин")
        self.assertContains(catalog, "Эълон додан")
        self.assertContains(catalog, "Сиёсати махфият")
        self.assertContains(catalog, "Созишномаи корбар")
        self.assertContains(catalog, "Қоидаҳои нашр")
