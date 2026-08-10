from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import City, Favorite, Listing, ListingImage, ModerationDecision


class LocationTests(TestCase):
    def test_map_uses_default_city(self):
        response = self.client.get(reverse("city_map"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Душанбе")

    def test_city_selection_is_saved_in_session(self):
        response = self.client.post(
            reverse("set_city"),
            {"city": "Худжанд", "next": reverse("city_map")},
        )
        self.assertRedirects(response, reverse("city_map"))
        self.assertEqual(self.client.session["selected_city"], "Худжанд")

        response = self.client.get(reverse("city_map"))
        self.assertContains(response, "Худжанд")

    def test_unknown_city_is_rejected(self):
        response = self.client.post(reverse("set_city"), {"city": "Неизвестный"})
        self.assertEqual(response.status_code, 400)


class HelpPageTests(TestCase):
    def test_help_page_is_public_and_contains_current_guidance(self):
        response = self.client.get(reverse("help"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Чем мы можем помочь?")
        self.assertContains(response, "Объявления и модерация")
        self.assertContains(response, "Безопасная сделка")
        self.assertContains(response, "Восстановление пароля по подтверждённому номеру")
        self.assertContains(response, "data-help-search")
        self.assertNotContains(response, "Submit")

    def test_help_page_only_accepts_get(self):
        response = self.client.post(reverse("help"))

        self.assertEqual(response.status_code, 405)


class ListingListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="catalog_owner",
            phone="+992900000030",
            email="catalog-owner@example.com",
            password="test-password-123",
        )
        cls.viewer = get_user_model().objects.create_user(
            username="catalog_viewer",
            phone="+992900000031",
            email="catalog-viewer@example.com",
            password="test-password-123",
        )
        dushanbe = City.objects.get(slug="dushanbe")
        khujand = City.objects.get(slug="khujand")
        common = {
            "owner": cls.user,
            "deal_type": Listing.DealType.RENT,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Квартира рядом с центром города.",
            "address": "Проспект Рудаки",
        }
        cls.published = Listing.objects.create(
            **common,
            city=dushanbe,
            title="Квартира в центре",
            price=Decimal("400000.00"),
            rooms=2,
            area=Decimal("65.00"),
            floor=5,
            total_floors=10,
            status=Listing.Status.PUBLISHED,
        )
        cls.cheaper = Listing.objects.create(
            **common,
            city=dushanbe,
            title="Недорогая квартира",
            price=Decimal("250000.00"),
            rooms=1,
            area=Decimal("40.00"),
            floor=2,
            total_floors=5,
            status=Listing.Status.PUBLISHED,
        )
        cls.sale_listing = Listing.objects.create(
            **{**common, "deal_type": Listing.DealType.SALE},
            city=dushanbe,
            title="Квартира на продажу",
            price=Decimal("500000.00"),
            status=Listing.Status.PUBLISHED,
        )
        cls.draft = Listing.objects.create(
            **common,
            city=dushanbe,
            title="Скрытый черновик",
            price=Decimal("100000.00"),
            status=Listing.Status.DRAFT,
        )
        cls.other_city = Listing.objects.create(
            **common,
            city=khujand,
            title="Квартира в Худжанде",
            price=Decimal("300000.00"),
            status=Listing.Status.PUBLISHED,
        )

    def test_home_shows_only_published_listings_in_selected_city(self):
        response = self.client.get(reverse("listing_list"))

        self.assertContains(response, self.published.title)
        self.assertContains(response, self.cheaper.title)
        self.assertNotContains(response, self.draft.title)
        self.assertNotContains(response, self.other_city.title)
        self.assertNotContains(response, self.sale_listing.title)
        self.assertContains(response, "grid grid-cols-2")
        self.assertContains(response, 'aria-label="Главная"')
        self.assertEqual(response.context["deal_type"], Listing.DealType.RENT)
        self.assertEqual(
            response.context["property_type"], Listing.PropertyType.APARTMENT
        )
        self.assertNotContains(response, "Сделка: любая")
        self.assertContains(response, '<option value="">Любой</option>', html=True)

    def test_any_property_type_can_still_be_selected(self):
        response = self.client.get(
            reverse("listing_list"),
            {"deal_type": Listing.DealType.RENT, "property_type": ""},
        )

        self.assertEqual(response.context["property_type"], "")

    def test_search_and_property_filters_are_applied(self):
        response = self.client.get(
            reverse("listing_list"),
            {"q": "Квартира в центре", "property_type": Listing.PropertyType.APARTMENT},
        )

        self.assertContains(response, self.published.title)
        self.assertNotContains(response, self.cheaper.title)

    def test_price_sorting_is_applied(self):
        response = self.client.get(reverse("listing_list"), {"sort": "price_asc"})

        titles = [listing.title for listing in response.context["page_obj"]]
        self.assertEqual(titles[:2], [self.cheaper.title, self.published.title])

    def test_advanced_listing_filters_are_applied(self):
        response = self.client.get(
            reverse("listing_list"),
            {
                "deal_type": Listing.DealType.RENT,
                "property_type": Listing.PropertyType.APARTMENT,
                "rooms": "2",
                "min_area": "60",
                "max_area": "70",
                "min_floor": "4",
                "max_floor": "6",
            },
        )

        self.assertContains(response, self.published.title)
        self.assertNotContains(response, self.cheaper.title)
        self.assertTrue(response.context["advanced_filters_active"])
        self.assertContains(response, "Дополнительные параметры")

    def test_header_filter_links_target_visible_filter_block(self):
        response = self.client.get(reverse("listing_list"))

        self.assertContains(
            response,
            f'href="{reverse("listing_list")}#filters"',
            count=1,
        )
        self.assertContains(response, 'id="filters"')
        self.assertContains(response, "data-mobile-filter-toggle")
        self.assertContains(response, 'id="mobile-filter-panel"')
        self.assertContains(response, "Дополнительные фильтры")

    def test_authenticated_user_sees_saved_favorite_state(self):
        Favorite.objects.create(user=self.viewer, listing=self.published)
        self.client.force_login(self.viewer)

        response = self.client.get(reverse("listing_list"))

        self.assertContains(response, "Удалить из избранного")


class ListingModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="owner",
            phone="+992900000001",
            password="test-password-123",
        )
        cls.city, _ = City.objects.update_or_create(
            slug="dushanbe",
            defaults={
                "name": "Душанбе",
                "latitude": Decimal("38.559800"),
                "longitude": Decimal("68.787000"),
            },
        )

    def make_listing(self, **overrides):
        values = {
            "owner": self.user,
            "city": self.city,
            "deal_type": Listing.DealType.SALE,
            "property_type": Listing.PropertyType.APARTMENT,
            "title": "2-комнатная квартира",
            "description": "Светлая квартира рядом с центром.",
            "price": Decimal("450000.00"),
            "address": "Улица Рудаки",
        }
        values.update(overrides)
        return Listing(**values)

    def test_listing_defaults_to_draft_and_has_public_id(self):
        listing = self.make_listing()
        listing.full_clean()
        listing.save()

        self.assertEqual(listing.status, Listing.Status.DRAFT)
        self.assertEqual(listing.currency, Listing.Currency.TJS)
        self.assertIsNotNone(listing.public_id)

    def test_published_queryset_excludes_drafts(self):
        self.make_listing().save()
        self.make_listing(
            title="Опубликованная квартира",
            status=Listing.Status.PUBLISHED,
        ).save()

        self.assertEqual(Listing.objects.published().count(), 1)

    def test_invalid_price_is_rejected(self):
        listing = self.make_listing(price=Decimal("-1.00"))

        with self.assertRaises(ValidationError):
            listing.full_clean()

    def test_floor_cannot_exceed_total_floors(self):
        listing = self.make_listing(floor=10, total_floors=9)

        with self.assertRaises(ValidationError):
            listing.full_clean()


class ListingPublicationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="publisher",
            phone="+992900000002",
            password="test-password-123",
        )
        cls.city = City.objects.get(slug="dushanbe")

    def setUp(self):
        self.client.force_login(self.user)

    def valid_data(self, **overrides):
        data = {
            "deal_type": Listing.DealType.SALE,
            "property_type": Listing.PropertyType.APARTMENT,
            "city": self.city.pk,
            "title": "2-комнатная квартира в центре",
            "description": "Светлая квартира с ремонтом, мебелью и парковкой рядом с домом.",
            "price": "450000.00",
            "currency": Listing.Currency.TJS,
            "address": "Улица Рудаки, 10",
            "rooms": "2",
            "area": "68.50",
            "floor": "7",
            "total_floors": "12",
        }
        data.update(overrides)
        return data

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()

        response = self.client.get(reverse("create"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('create')}",
        )

    def test_authenticated_user_can_open_publication_page(self):
        response = self.client.get(reverse("create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Расскажите о недвижимости")

    def test_draft_can_be_saved_without_images(self):
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="draft"),
        )

        self.assertRedirects(response, reverse("create"))
        listing = Listing.objects.get()
        self.assertEqual(listing.owner, self.user)
        self.assertEqual(listing.status, Listing.Status.DRAFT)
        self.assertIsNone(listing.submitted_at)
        self.assertFalse(listing.images.exists())

    def test_publication_requires_an_image(self):
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="publish"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "добавьте хотя бы одну фотографию")
        self.assertFalse(Listing.objects.exists())

    def test_listing_with_image_is_sent_to_moderation(self):
        image = SimpleUploadedFile(
            "apartment.gif",
            (
                b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00"
                b"\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00"
                b"\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
            ),
            content_type="image/gif",
        )
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="publish", images=image),
        )

        self.assertRedirects(response, reverse("create"))
        listing = Listing.objects.get()
        self.assertEqual(listing.status, Listing.Status.PENDING)
        self.assertIsNotNone(listing.submitted_at)
        self.assertEqual(ListingImage.objects.filter(listing=listing).count(), 1)


class ListingDetailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="detail_owner",
            phone="+992900000010",
            email="detail-owner@example.com",
            password="test-password-123",
        )
        cls.outsider = User.objects.create_user(
            username="detail_outsider",
            phone="+992900000011",
            email="detail-outsider@example.com",
            password="test-password-123",
        )
        cls.moderator = User.objects.create_user(
            username="detail_moderator",
            phone="+992900000012",
            email="detail-moderator@example.com",
            password="test-password-123",
            is_moderator=True,
        )
        cls.city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.owner,
            "city": cls.city,
            "deal_type": Listing.DealType.RENT,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Подробное описание квартиры для проверки страницы.",
            "price": Decimal("2000.00"),
            "address": "Улица Рудаки, 15",
            "rooms": 2,
            "area": Decimal("50.00"),
            "floor": 4,
            "total_floors": 11,
        }
        cls.published = Listing.objects.create(
            **common,
            title="Опубликованная квартира",
            status=Listing.Status.PUBLISHED,
        )
        ListingImage.objects.create(
            listing=cls.published,
            image="listings/test/apartment.jpg",
            alt_text=cls.published.title,
        )
        ListingImage.objects.create(
            listing=cls.published,
            image="listings/test/apartment-2.jpg",
            alt_text=f"{cls.published.title}, фотография 2",
            position=1,
        )
        cls.pending = Listing.objects.create(
            **common,
            title="Квартира на модерации",
            status=Listing.Status.PENDING,
        )

    def test_published_listing_is_visible_to_everyone(self):
        response = self.client.get(self.published.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published.title)
        self.assertContains(response, self.owner.phone)
        self.assertContains(response, "/media/listings/test/apartment.jpg")

    def test_gallery_has_mobile_slider_and_desktop_lightbox(self):
        response = self.client.get(self.published.get_absolute_url())

        self.assertContains(response, "data-gallery-track")
        self.assertContains(response, 'data-gallery-open="0"')
        self.assertContains(response, "data-gallery-lightbox")
        self.assertContains(response, "Следующая фотография")

    def test_pending_listing_is_hidden_from_anonymous_users(self):
        response = self.client.get(self.pending.get_absolute_url())

        self.assertEqual(response.status_code, 404)

    def test_pending_listing_is_hidden_from_other_users(self):
        self.client.force_login(self.outsider)

        response = self.client.get(self.pending.get_absolute_url())

        self.assertEqual(response.status_code, 404)

    def test_owner_can_preview_pending_listing(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.pending.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Режим предпросмотра")

    def test_moderator_can_preview_pending_listing(self):
        self.client.force_login(self.moderator)

        response = self.client.get(self.pending.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.pending.title)
        self.assertContains(response, "Отправлено на модерацию")

    def test_unknown_public_id_returns_404(self):
        response = self.client.get(
            reverse("listing_detail", kwargs={"public_id": uuid4()})
        )

        self.assertEqual(response.status_code, 404)


class ModerationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="moderation_owner",
            phone="+992900000060",
            email="moderation-owner@example.com",
            password="test-password-123",
        )
        cls.regular_user = User.objects.create_user(
            username="moderation_regular",
            phone="+992900000061",
            email="moderation-regular@example.com",
            password="test-password-123",
        )
        cls.moderator = User.objects.create_user(
            username="moderation_staff",
            phone="+992900000062",
            email="moderation-staff@example.com",
            password="test-password-123",
            is_moderator=True,
        )
        cls.city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.owner,
            "city": cls.city,
            "deal_type": Listing.DealType.RENT,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Подробное описание объявления для проверки модератором.",
            "price": Decimal("2500.00"),
            "address": "Улица Айни, 20",
        }
        cls.pending = Listing.objects.create(
            **common,
            title="Объявление ожидает модерации",
            status=Listing.Status.PENDING,
        )
        cls.published = Listing.objects.create(
            **common,
            title="Уже опубликованное объявление",
            status=Listing.Status.PUBLISHED,
        )

    def approve_url(self, listing=None):
        return reverse(
            "moderation_approve",
            args=((listing or self.pending).public_id,),
        )

    def reject_url(self, listing=None):
        return reverse(
            "moderation_reject",
            args=((listing or self.pending).public_id,),
        )

    def test_queue_contains_only_pending_listings(self):
        self.client.force_login(self.moderator)

        response = self.client.get(reverse("moderation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.pending.title)
        self.assertNotContains(response, self.published.title)
        self.assertEqual(response.context["pending_count"], 1)
        self.assertNotContains(response, "<img")

    def test_regular_user_cannot_moderate_listing(self):
        self.client.force_login(self.regular_user)

        response = self.client.post(self.approve_url())

        self.assertEqual(response.status_code, 403)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Listing.Status.PENDING)
        self.assertFalse(ModerationDecision.objects.exists())

    def test_moderation_actions_only_accept_post(self):
        self.client.force_login(self.moderator)

        self.assertEqual(self.client.get(self.approve_url()).status_code, 405)
        self.assertEqual(self.client.get(self.reject_url()).status_code, 405)

    def test_moderator_can_approve_pending_listing(self):
        self.client.force_login(self.moderator)

        response = self.client.post(self.approve_url())

        self.assertRedirects(response, reverse("moderation"))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Listing.Status.PUBLISHED)
        self.assertIsNotNone(self.pending.published_at)
        decision = ModerationDecision.objects.get(listing=self.pending)
        self.assertEqual(decision.moderator, self.moderator)
        self.assertEqual(decision.decision, ModerationDecision.Decision.APPROVED)
        self.assertEqual(decision.reason, "")

    def test_rejection_requires_reason(self):
        self.client.force_login(self.moderator)

        response = self.client.post(self.reject_url(), {"reason": "   "})

        self.assertRedirects(response, self.pending.get_absolute_url())
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Listing.Status.PENDING)
        self.assertFalse(ModerationDecision.objects.exists())

    def test_moderator_can_reject_with_reason(self):
        self.client.force_login(self.moderator)
        reason = "Добавьте фотографию фасада и уточните полный адрес."

        response = self.client.post(self.reject_url(), {"reason": reason})

        self.assertRedirects(response, reverse("moderation"))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Listing.Status.REJECTED)
        self.assertIsNone(self.pending.published_at)
        decision = ModerationDecision.objects.get(listing=self.pending)
        self.assertEqual(decision.decision, ModerationDecision.Decision.REJECTED)
        self.assertEqual(decision.reason, reason)

    def test_processed_listing_cannot_be_moderated_again(self):
        self.client.force_login(self.moderator)
        self.client.post(self.approve_url())

        response = self.client.post(
            self.reject_url(),
            {"reason": "Попытка изменить уже принятое решение."},
        )

        self.assertRedirects(response, reverse("moderation"))
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Listing.Status.PUBLISHED)
        self.assertEqual(
            ModerationDecision.objects.filter(listing=self.pending).count(),
            1,
        )

    def test_owner_sees_rejection_reason(self):
        reason = "Укажите точный район и добавьте фотографию кухни."
        ModerationDecision.objects.create(
            listing=self.pending,
            moderator=self.moderator,
            decision=ModerationDecision.Decision.REJECTED,
            reason=reason,
        )
        self.pending.status = Listing.Status.REJECTED
        self.pending.save(update_fields=("status", "updated_at"))
        self.client.force_login(self.owner)

        response = self.client.get(self.pending.get_absolute_url())

        self.assertContains(response, "Причина отклонения")
        self.assertContains(response, reason)

        self.client.force_login(self.regular_user)
        self.assertEqual(self.client.get(self.pending.get_absolute_url()).status_code, 404)


class ListingEditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="edit_owner",
            phone="+992900000040",
            email="edit-owner@example.com",
            password="test-password-123",
        )
        cls.outsider = User.objects.create_user(
            username="edit_outsider",
            phone="+992900000041",
            email="edit-outsider@example.com",
            password="test-password-123",
        )
        cls.city = City.objects.get(slug="dushanbe")
        cls.listing = Listing.objects.create(
            owner=cls.owner,
            city=cls.city,
            deal_type=Listing.DealType.SALE,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Квартира для редактирования",
            description="Подробное описание квартиры, которую владелец будет редактировать.",
            price=Decimal("420000.00"),
            address="Улица Рудаки, 30",
            rooms=2,
            area=Decimal("65.00"),
            floor=5,
            total_floors=10,
        )
        cls.image = ListingImage.objects.create(
            listing=cls.listing,
            image="listings/test/edit-apartment.jpg",
            alt_text=cls.listing.title,
        )

    def edit_url(self):
        return reverse("edit_listing", args=(self.listing.public_id,))

    def valid_data(self, **overrides):
        data = {
            "deal_type": Listing.DealType.SALE,
            "property_type": Listing.PropertyType.APARTMENT,
            "city": self.city.pk,
            "title": "Обновлённая квартира в центре",
            "description": "Обновлённое подробное описание квартиры после редактирования владельцем.",
            "price": "430000.00",
            "currency": Listing.Currency.TJS,
            "address": "Улица Рудаки, 31",
            "rooms": "2",
            "area": "66.00",
            "floor": "5",
            "total_floors": "10",
            "action": "draft",
        }
        data.update(overrides)
        return data

    def test_only_owner_can_open_edit_page(self):
        anonymous = self.client.get(self.edit_url())
        self.assertRedirects(anonymous, f"{reverse('login')}?next={self.edit_url()}")

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.edit_url()).status_code, 404)

        self.client.force_login(self.owner)
        response = self.client.get(self.edit_url())
        self.assertContains(response, "Редактирование объявления")
        self.assertContains(response, self.listing.title)

    def test_owner_can_save_changes_as_draft_without_reuploading_image(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.edit_url(), self.valid_data())

        self.assertRedirects(response, self.listing.get_absolute_url())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.title, "Обновлённая квартира в центре")
        self.assertEqual(self.listing.status, Listing.Status.DRAFT)
        self.assertIsNone(self.listing.submitted_at)
        self.assertEqual(self.listing.images.count(), 1)

    def test_edited_published_listing_returns_to_moderation(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            self.edit_url(), self.valid_data(action="publish")
        )

        self.assertRedirects(response, self.listing.get_absolute_url())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PENDING)
        self.assertIsNotNone(self.listing.submitted_at)

    def test_last_image_cannot_be_removed_when_submitting(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            self.edit_url(),
            self.valid_data(action="publish", remove_images=[self.image.pk]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "добавьте хотя бы одну фотографию")
        self.assertTrue(ListingImage.objects.filter(pk=self.image.pk).exists())

    def test_owner_sees_edit_link_on_listing_page(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertContains(response, self.edit_url())

        self.client.force_login(self.outsider)
        response = self.client.get(self.listing.get_absolute_url())
        self.assertNotContains(response, self.edit_url())


class FavoriteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="favorite_owner",
            phone="+992900000021",
            email="favorite-owner@example.com",
            password="test-password-123",
        )
        cls.user = User.objects.create_user(
            username="favorite_user",
            phone="+992900000022",
            email="favorite-user@example.com",
            password="test-password-123",
        )
        cls.city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.owner,
            "city": cls.city,
            "deal_type": Listing.DealType.SALE,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Описание объявления для проверки избранного.",
            "price": Decimal("350000.00"),
            "address": "Проспект Рудаки, 20",
        }
        cls.published = Listing.objects.create(
            **common,
            title="Квартира для избранного",
            status=Listing.Status.PUBLISHED,
        )
        cls.pending = Listing.objects.create(
            **common,
            title="Скрытая квартира",
            status=Listing.Status.PENDING,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_toggle_adds_and_then_removes_favorite(self):
        url = reverse("toggle_favorite", args=(self.published.public_id,))

        response = self.client.post(url, {"next": reverse("favorites")})
        self.assertRedirects(response, reverse("favorites"))
        self.assertTrue(Favorite.objects.filter(user=self.user, listing=self.published).exists())

        self.client.post(url, {"next": reverse("favorites")})
        self.assertFalse(Favorite.objects.filter(user=self.user, listing=self.published).exists())

    def test_pending_listing_cannot_be_favorited(self):
        response = self.client.post(
            reverse("toggle_favorite", args=(self.pending.public_id,))
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Favorite.objects.exists())

    def test_favorites_page_renders_card_and_remove_action(self):
        Favorite.objects.create(user=self.user, listing=self.published)

        response = self.client.get(reverse("favorites"))

        self.assertContains(response, self.published.title)
        self.assertContains(response, "Удалить из избранного")
