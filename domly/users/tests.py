from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from listings.models import City, Favorite, Listing

from .models import RegistrationAttempt, User


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="domly_user",
            phone="+992900001122",
            email="owner@example.com",
            password="SafePassword-934",
            is_phone_verified=True,
        )

    def test_login_with_username_phone_or_email(self):
        for identifier in ("domly_user", "+992900001122", "OWNER@example.com"):
            response = self.client.post(
                reverse("login"),
                {"identifier": identifier, "password": "SafePassword-934"},
            )
            self.assertRedirects(response, "/")
            self.client.logout()

    @patch("users.views.send_verification_code")
    def test_registration_requires_sms_code(self, send_code):
        response = self.client.post(
            reverse("register"),
            {
                "username": "new_owner",
                "phone": "+992900009999",
                "email": "new@example.com",
                "password1": "AnotherSafePassword-934",
                "password2": "AnotherSafePassword-934",
            },
        )
        self.assertRedirects(response, reverse("verify"))
        self.assertFalse(User.objects.filter(username="new_owner").exists())
        self.assertEqual(RegistrationAttempt.objects.count(), 1)

        code = send_code.call_args.args[1]
        response = self.client.post(reverse("verify"), {"code": code})
        self.assertRedirects(response, "/")
        user = User.objects.get(username="new_owner")
        self.assertTrue(user.is_phone_verified)
        self.assertTrue(user.check_password("AnotherSafePassword-934"))
        self.assertEqual(RegistrationAttempt.objects.count(), 0)

    def test_wrong_password_does_not_login(self):
        response = self.client.post(
            reverse("login"),
            {"identifier": "domly_user", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)


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

    def test_profile_data_can_be_edited_without_changing_phone(self):
        response = self.client.post(
            reverse("profile"),
            {
                "username": "updated_profile",
                "first_name": "Али",
                "last_name": "Каримов",
                "email": "UPDATED@example.com",
            },
        )

        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "updated_profile")
        self.assertEqual(self.user.email, "updated@example.com")
        self.assertEqual(self.user.phone, "+992900001130")

    def test_avatar_input_hides_storage_name_and_keeps_clear_action(self):
        self.user.avatar.name = "avatars/private-avatar-name.jpg"
        self.user.save(update_fields=("avatar",))

        response = self.client.get(reverse("profile"))

        self.assertNotContains(response, "На данный момент:")
        self.assertNotContains(response, f'href="{self.user.avatar.url}"')
        self.assertContains(response, "data-avatar-clear")
        self.assertContains(response, 'name="avatar-clear"')
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
        self.assertNotContains(catalog_response, 'title="Выход из аккаунта"')
        self.assertNotContains(listings_response, 'title="Выход из аккаунта"')


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
