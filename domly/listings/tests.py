import json
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from PIL import Image

from users.models import Notification, UserBlock

from .locations import get_selected_city
from .geocoding import GeocodingRateLimited, geocode_address
from .models import City, Favorite, Listing, ListingBlock, ListingImage, ListingReport, ModerationDecision
from .search import parse_search_query


class LocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(
            username="map_owner",
            phone="+992900000090",
            password="test-password-123",
        )
        cls.dushanbe = City.objects.get(slug="dushanbe")
        cls.khujand = City.objects.get(slug="khujand")

    def test_map_uses_default_city(self):
        response = self.client.get(reverse("city_map"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Душанбе")
        self.assertContains(response, 'id="city-map" class="relative z-0')
        self.assertContains(response, "map.attributionControl.setPrefix(false)")
        self.assertContains(response, ".leaflet-control-attribution")

    def test_city_selection_is_saved_in_session(self):
        response = self.client.post(
            reverse("set_city"),
            {"city": "Худжанд", "next": reverse("city_map")},
        )
        self.assertRedirects(response, reverse("city_map"))
        self.assertEqual(self.client.session["selected_city"], "Худжанд")

        response = self.client.get(reverse("city_map"))
        self.assertContains(response, "Худжанд")

    def test_catalog_city_selection_uses_stable_localized_city_url(self):
        with translation.override("en"):
            catalog_url = reverse("listing_list")
            set_city_url = reverse("set_city")
            khujand_url = reverse(
                "city_listings", kwargs={"city_slug": self.khujand.slug}
            )

        response = self.client.post(
            set_city_url,
            {"city": self.khujand.name, "next": catalog_url},
        )

        self.assertRedirects(response, khujand_url)
        self.assertEqual(self.client.session["selected_city"], self.khujand.name)

    def test_unknown_city_is_rejected(self):
        response = self.client.post(reverse("set_city"), {"city": "Неизвестный"})
        self.assertEqual(response.status_code, 400)

    def test_geocode_endpoint_requires_login(self):
        response = self.client.get(
            reverse("geocode_location"),
            {"city": "Душанбе", "address": "Улица Рудаки, 20"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{reverse('login')}?next="))

    @patch("listings.views.geocode_address")
    def test_geocode_endpoint_returns_address_coordinates(self, geocode):
        geocode.return_value = {
            "latitude": 38.5701,
            "longitude": 68.7899,
            "display_name": "20, улица Рудаки, Душанбе, Таджикистан",
        }
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("geocode_location"),
            {"city": "Душанбе", "address": "Улица Рудаки, 20"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latitude"], 38.5701)
        geocode.assert_called_once_with(
            city=self.dushanbe,
            address="Улица Рудаки, 20",
        )

    def test_map_shows_only_published_listings_with_coordinates_in_city(self):
        mapped = Listing.objects.create(
            owner=self.owner,
            city=self.dushanbe,
            deal_type=Listing.DealType.SALE,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Квартира с точкой на карте",
            description="Подробное описание квартиры с точным местом на карте.",
            price=Decimal("480000.00"),
            address="Улица Рудаки, 20",
            latitude=Decimal("38.570000"),
            longitude=Decimal("68.790000"),
        )
        Listing.objects.create(
            owner=self.owner,
            city=self.dushanbe,
            deal_type=Listing.DealType.SALE,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Квартира без точки",
            description="Опубликованное объявление без сохранённых координат объекта.",
            price=Decimal("390000.00"),
            address="Улица Айни, 5",
        )
        Listing.objects.create(
            owner=self.owner,
            city=self.dushanbe,
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.DRAFT,
            title="Черновик с координатами",
            description="Черновик не должен быть доступен на публичной карте.",
            price=Decimal("3500.00"),
            address="Улица Сомони, 7",
            latitude=Decimal("38.580000"),
            longitude=Decimal("68.800000"),
        )

        response = self.client.get(reverse("city_map"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["city_listings"]), [mapped])
        self.assertEqual(response.context["unmapped_count"], 1)
        self.assertEqual(response.context["map_listings"][0]["id"], str(mapped.public_id))
        self.assertEqual(response.context["map_listings"][0]["price"], "480000")
        self.assertContains(response, mapped.title)
        self.assertNotContains(response, "Черновик с координатами")
        self.assertContains(response, "Объявлений без точного места на карте: 1")

        focused_response = self.client.get(
            reverse("city_map"),
            {"city": "Душанбе", "listing": mapped.public_id},
        )
        self.assertEqual(
            focused_response.context["focused_listing_id"],
            str(mapped.public_id),
        )

    def test_map_city_query_does_not_change_session_city(self):
        listing = Listing.objects.create(
            owner=self.owner,
            city=self.khujand,
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.HOUSE,
            status=Listing.Status.PUBLISHED,
            title="Дом на карте Худжанда",
            description="Просторный дом с точными координатами в Худжанде.",
            price=Decimal("6000.00"),
            address="Проспект Исмоили Сомони",
            latitude=Decimal("40.285000"),
            longitude=Decimal("69.625000"),
        )

        response = self.client.get(reverse("city_map"), {"city": "Худжанд"})

        self.assertContains(response, listing.title)
        self.assertEqual(response.context["map_city_name"], "Худжанд")
        self.assertEqual(get_selected_city(response.wsgi_request), "Душанбе")


@override_settings(
    GEOCODER_URL="https://nominatim.example/search",
    GEOCODER_USER_AGENT="Domly tests",
    GEOCODER_TIMEOUT=3,
)
class GeocodingServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.city = City.objects.get(slug="dushanbe")

    @staticmethod
    def provider_response(payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(payload).encode("utf-8")
        return response

    @patch("listings.geocoding.urlopen")
    def test_address_result_is_normalized_and_cached(self, urlopen):
        response = self.provider_response(
            [
                {
                    "lat": "38.570100",
                    "lon": "68.789900",
                    "display_name": "20, улица Рудаки, Душанбе, Таджикистан",
                }
            ]
        )
        urlopen.return_value = response

        first = geocode_address(city=self.city, address="Улица Рудаки, 20")
        second = geocode_address(city=self.city, address="Улица Рудаки, 20")

        self.assertEqual(first, second)
        self.assertEqual(first["latitude"], 38.5701)
        self.assertEqual(first["longitude"], 68.7899)
        request = urlopen.call_args.args[0]
        self.assertIn("countrycodes=tj", request.full_url)
        self.assertIn("limit=5", request.full_url)
        self.assertIn("viewbox=", request.full_url)
        self.assertEqual(request.get_header("User-agent"), "Domly tests")
        urlopen.assert_called_once()

    @patch("listings.geocoding._reserve_provider_request")
    @patch("listings.geocoding.urlopen")
    def test_address_falls_back_to_shorter_street_name_in_selected_city(
        self,
        urlopen,
        reserve_provider_request,
    ):
        khujand = City.objects.get(slug="khujand")
        urlopen.side_effect = [
            self.provider_response([]),
            self.provider_response([]),
            self.provider_response(
                [
                    {
                        "lat": "39.150000",
                        "lon": "68.550000",
                        "display_name": "Трасса Душанбе — Худжанд, Айни",
                        "class": "highway",
                        "addresstype": "road",
                        "address": {"county": "Ноҳияи Айнӣ"},
                    },
                    {
                        "lat": "40.291000",
                        "lon": "69.631000",
                        "display_name": "кӯчаи Айнӣ, Шаҳри Хуҷанд, Тоҷикистон",
                        "class": "highway",
                        "addresstype": "residential",
                        "address": {"city": "Шаҳри Хуҷанд"},
                    },
                ]
            ),
        ]

        result = geocode_address(
            city=khujand,
            address="Улица Садриддина Айни, 12",
        )

        self.assertEqual(result["latitude"], 40.291)
        self.assertEqual(result["longitude"], 69.631)
        queries = [
            parse_qs(urlparse(call.args[0].full_url).query)["q"][0]
            for call in urlopen.call_args_list
        ]
        self.assertEqual(
            queries,
            [
                "Улица Садриддина Айни, 12, Худжанд, Таджикистан",
                "Садриддина Айни, Худжанд",
                "Айни, Худжанд",
            ],
        )
        self.assertEqual(reserve_provider_request.call_count, 3)

    @patch("listings.geocoding.cache.set")
    @patch("listings.geocoding._reserve_provider_request")
    @patch("listings.geocoding.urlopen")
    def test_unsuccessful_address_is_only_cached_briefly(
        self,
        urlopen,
        reserve_provider_request,
        cache_set,
    ):
        urlopen.side_effect = [self.provider_response([]) for _ in range(3)]

        result = geocode_address(
            city=self.city,
            address="Улица Неизвестная, 99",
        )

        self.assertIsNone(result)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(reserve_provider_request.call_count, 2)
        self.assertEqual(cache_set.call_args.kwargs["timeout"], 60 * 5)

    def test_public_provider_is_limited_to_one_uncached_request_per_second(self):
        cache.set("geocode:nominatim:request", True, timeout=1)

        with self.assertRaises(GeocodingRateLimited):
            geocode_address(city=self.city, address="Проспект Айни, 15")


class HelpPageTests(TestCase):
    def setUp(self):
        translation.activate("ru")
        self.addCleanup(translation.deactivate)

    def test_help_page_is_public_and_contains_current_guidance(self):
        response = self.client.get(reverse("help"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Чем мы можем помочь?")
        self.assertContains(response, "Объявления и модерация")
        self.assertContains(response, "Безопасная сделка")
        self.assertContains(response, "получите шестизначный код на подтверждённый email")
        self.assertContains(response, "Снято с публикации")
        self.assertContains(response, "Продано или сдано")
        self.assertContains(response, "Запомнить меня")
        self.assertContains(response, "Язык и тема")
        self.assertContains(response, "Адрес и карта")
        self.assertContains(response, "Жалобы и блокировки")
        self.assertContains(response, "Умный поиск")
        self.assertContains(response, "новое сообщение снова покажет диалог")
        self.assertContains(response, "data-help-search")
        self.assertNotContains(response, "Submit")

    def test_help_page_only_accepts_get(self):
        response = self.client.post(reverse("help"))

        self.assertEqual(response.status_code, 405)

    def test_home_links_to_help_and_footer_clears_mobile_navigation(self):
        response = self.client.get(reverse("listing_list"))

        self.assertContains(response, "data-site-footer")
        self.assertContains(response, "pb-16")
        self.assertContains(response, "Как пользоваться Domly?")
        self.assertContains(response, "data-home-help-link")
        self.assertContains(response, "border-green-200")
        self.assertNotContains(response, "Аренда и продажа недвижимости напрямую от владельцев.")


class SmartSearchParserTests(TestCase):
    def test_natural_query_extracts_property_rooms_price_and_city(self):
        intent = parse_search_query(
            "двушка до 5000 в Душанбе",
            ("Душанбе", "Худжанд"),
        )

        self.assertEqual(intent.property_type, Listing.PropertyType.APARTMENT)
        self.assertEqual(intent.rooms, 2)
        self.assertEqual(intent.max_price, Decimal("5000"))
        self.assertEqual(intent.city_name, "Душанбе")
        self.assertEqual(intent.text_tokens, ())

    def test_query_understands_deal_synonyms_typos_and_transliteration(self):
        sale = parse_search_query("купить дом в Худжанд", ("Душанбе", "Худжанд"))
        typo = parse_search_query("квартираа в цэнтре", ("Душанбе",))
        latin = parse_search_query("kvartira v Dushanbe", ("Душанбе",))

        self.assertEqual(sale.deal_type, Listing.DealType.SALE)
        self.assertEqual(sale.property_type, Listing.PropertyType.HOUSE)
        self.assertEqual(sale.city_name, "Худжанд")
        self.assertEqual(typo.property_type, Listing.PropertyType.APARTMENT)
        self.assertIn("цэнтре", typo.text_tokens)
        self.assertEqual(latin.property_type, Listing.PropertyType.APARTMENT)
        self.assertEqual(latin.city_name, "Душанбе")


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

    def test_quick_categories_preserve_the_other_selected_filter(self):
        response = self.client.get(
            reverse("listing_list"),
            {
                "deal_type": Listing.DealType.SALE,
                "property_type": Listing.PropertyType.HOUSE,
            },
        )
        catalog_url = reverse("listing_list")

        self.assertContains(
            response,
            f'href="{catalog_url}?deal_type=rent&amp;property_type=house"',
            html=False,
        )
        self.assertContains(
            response,
            f'href="{catalog_url}?deal_type=sale&amp;property_type=room"',
            html=False,
        )

    def test_search_and_property_filters_are_applied(self):
        response = self.client.get(
            reverse("listing_list"),
            {"q": "Квартира в центре", "property_type": Listing.PropertyType.APARTMENT},
        )

        self.assertContains(response, self.published.title)
        titles = [listing.title for listing in response.context["page_obj"]]
        self.assertEqual(titles[0], self.published.title)
        self.assertIn(self.cheaper.title, titles)

    def test_smart_search_applies_rooms_price_and_city_from_query(self):
        matching = Listing.objects.create(
            owner=self.user,
            city=City.objects.get(slug="dushanbe"),
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Уютная двушка у парка",
            description="Светлая квартира с мебелью и хорошим ремонтом.",
            price=Decimal("4500.00"),
            address="Улица Бухоро",
            rooms=2,
        )
        too_expensive = Listing.objects.create(
            owner=self.user,
            city=matching.city,
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Дорогая двушка у парка",
            description="Просторная квартира с двумя комнатами.",
            price=Decimal("6500.00"),
            address="Улица Бухоро",
            rooms=2,
        )

        response = self.client.get(
            reverse("listing_list"),
            {"q": "двушка до 5000 в Душанбе"},
        )

        self.assertContains(response, matching.title)
        self.assertNotContains(response, too_expensive.title)
        self.assertEqual(response.context["result_city"], "Душанбе")
        self.assertEqual(response.context["sort"], "relevance")
        self.assertContains(response, "Поняли запрос")

    def test_smart_search_finds_typo_and_transliterated_query(self):
        typo_response = self.client.get(
            reverse("listing_list"),
            {"q": "квартираа в цэнтре"},
        )
        latin_response = self.client.get(
            reverse("listing_list"),
            {"q": "kvartira v tsentre"},
        )

        self.assertContains(typo_response, self.published.title)
        self.assertNotContains(typo_response, self.cheaper.title)
        self.assertContains(latin_response, self.published.title)

    def test_query_can_override_city_and_default_property_type(self):
        house = Listing.objects.create(
            owner=self.user,
            city=City.objects.get(slug="khujand"),
            deal_type=Listing.DealType.SALE,
            property_type=Listing.PropertyType.HOUSE,
            status=Listing.Status.PUBLISHED,
            title="Дом в центре Худжанда",
            description="Просторный дом для семьи в удобном районе города.",
            price=Decimal("700000.00"),
            address="Проспект Исмоили Сомони",
        )

        response = self.client.get(
            reverse("listing_list"),
            {"q": "купить дом в Худжанд"},
        )

        self.assertContains(response, house.title)
        self.assertEqual(response.context["deal_type"], Listing.DealType.SALE)
        self.assertEqual(response.context["property_type"], Listing.PropertyType.HOUSE)
        self.assertEqual(response.context["result_city"], "Худжанд")

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
        self.assertContains(
            response,
            '<select name="city" aria-label="Город"',
            count=2,
        )
        self.assertContains(response, ">Новые</option>")

    def test_mobile_city_filter_changes_results_without_changing_session_city(self):
        response = self.client.get(
            reverse("listing_list"),
            {"city": "Худжанд"},
        )
        expected_url = reverse("city_listings", kwargs={"city_slug": "khujand"})
        self.assertRedirects(response, expected_url)
        response = self.client.get(expected_url)

        self.assertContains(response, self.other_city.title)
        self.assertNotContains(response, self.published.title)
        self.assertEqual(response.context["result_city"], "Худжанд")
        self.assertEqual(get_selected_city(response.wsgi_request), "Душанбе")
        self.assertContains(response, '<option value="Худжанд" selected>Худжанд</option>', html=True)

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


class ListingImageProcessingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = get_user_model().objects.create_user(
            username="image_owner",
            email="image-owner@example.com",
            password="test-password-123",
        )
        cls.listing = Listing.objects.create(
            owner=owner,
            city=City.objects.get(slug="dushanbe"),
            deal_type=Listing.DealType.SALE,
            property_type=Listing.PropertyType.APARTMENT,
            title="Объявление с оптимизированной фотографией",
            description="Описание объявления для проверки обработки фотографии.",
            price="300000.00",
            address="Улица Рудаки, 20",
        )

    def make_jpeg_with_metadata(self):
        output = BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "private metadata"
        Image.new("RGB", (3000, 2000), color="blue").save(
            output,
            format="JPEG",
            quality=90,
            exif=exif,
        )
        return SimpleUploadedFile(
            "large-private-photo.jpg",
            output.getvalue(),
            content_type="image/jpeg",
        )

    def test_listing_image_is_normalized_optimized_and_has_thumbnail(self):
        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            listing_image = ListingImage.objects.create(
                listing=self.listing,
                image=self.make_jpeg_with_metadata(),
            )

            self.assertTrue(listing_image.image.name.endswith(".webp"))
            self.assertIn("/thumbnails/", listing_image.thumbnail.name)
            self.assertTrue(listing_image.thumbnail.name.endswith(".webp"))
            with Image.open(listing_image.image.path) as main:
                self.assertEqual(main.format, "WEBP")
                self.assertLessEqual(max(main.size), 2400)
                self.assertFalse(main.getexif())
            with Image.open(listing_image.thumbnail.path) as thumbnail:
                self.assertEqual(thumbnail.format, "WEBP")
                self.assertLessEqual(thumbnail.width, 720)
                self.assertLessEqual(thumbnail.height, 540)

            stored_paths = (
                Path(listing_image.image.path),
                Path(listing_image.thumbnail.path),
            )
            with self.captureOnCommitCallbacks(execute=True):
                listing_image.delete()
            self.assertTrue(all(not path.exists() for path in stored_paths))


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
        translation.activate("ru")
        self.addCleanup(translation.deactivate)
        self.media_root = TemporaryDirectory()
        self.addCleanup(self.media_root.cleanup)
        self.media_settings = override_settings(MEDIA_ROOT=self.media_root.name)
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.client.force_login(self.user)

    def make_image(self, name="apartment.png"):
        output = BytesIO()
        Image.new("RGB", (32, 24), color="white").save(output, format="PNG")
        return SimpleUploadedFile(
            name,
            output.getvalue(),
            content_type="image/png",
        )

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
            "latitude": "38.570000",
            "longitude": "68.790000",
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
        self.assertContains(
            response,
            'id="listing-location-picker" class="relative z-0',
        )
        self.assertContains(response, "Найти по адресу")
        self.assertContains(response, reverse("geocode_location"))
        self.assertContains(response, "map.attributionControl.setPrefix(false)")
        self.assertContains(response, "data-file-picker")
        self.assertContains(response, "data-file-picker-input")
        self.assertContains(response, "multiple")
        self.assertContains(response, "Выбрать фотографии")
        self.assertContains(response, "Фотографии не выбраны")
        self.assertContains(response, 'src="/static/file_picker.js?v=20260817"')

    def test_draft_can_be_saved_without_coordinates(self):
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="draft", latitude="", longitude=""),
        )

        self.assertRedirects(response, reverse("create"))
        listing = Listing.objects.get()
        self.assertIsNone(listing.latitude)
        self.assertIsNone(listing.longitude)

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
        self.assertEqual(listing.contact_phone, "")

    def test_contact_phone_is_optional_and_normalized(self):
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="draft", contact_phone="90 123 45 67"),
        )

        self.assertRedirects(response, reverse("create"))
        self.assertEqual(Listing.objects.get().contact_phone, "+992901234567")

    def test_invalid_contact_phone_is_rejected(self):
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="draft", contact_phone="123"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Введите корректный номер")
        self.assertFalse(Listing.objects.exists())

    def test_publication_requires_an_image(self):
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="publish"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "добавьте хотя бы одну фотографию")
        self.assertFalse(Listing.objects.exists())

    def test_listing_with_image_is_sent_to_moderation(self):
        image = self.make_image()
        response = self.client.post(
            reverse("create"),
            self.valid_data(action="publish", images=image),
        )

        self.assertRedirects(response, reverse("create"))
        listing = Listing.objects.get()
        self.assertEqual(listing.status, Listing.Status.PENDING)
        self.assertIsNotNone(listing.submitted_at)
        self.assertEqual(listing.latitude, Decimal("38.570000"))
        self.assertEqual(listing.longitude, Decimal("68.790000"))
        self.assertEqual(ListingImage.objects.filter(listing=listing).count(), 1)

    def test_publication_requires_location_on_map(self):
        image = self.make_image()

        response = self.client.post(
            reverse("create"),
            self.valid_data(
                action="publish",
                images=image,
                latitude="",
                longitude="",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "укажите точное место объекта на карте")
        self.assertFalse(Listing.objects.exists())


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
            "contact_phone": "+992911112233",
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
            latitude=Decimal("38.570000"),
            longitude=Decimal("68.790000"),
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

    def setUp(self):
        translation.activate("ru")
        self.addCleanup(translation.deactivate)

    def test_published_listing_is_visible_to_everyone(self):
        response = self.client.get(self.published.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published.title)
        self.assertContains(response, self.published.contact_phone)
        self.assertNotContains(response, self.owner.phone)
        self.assertContains(response, "/media/listings/test/apartment.jpg")
        self.assertContains(response, "data-listing-characteristics")
        self.assertContains(response, "px-4 sm:grid-cols-3 sm:px-0")

    def test_gallery_has_mobile_slider_and_desktop_lightbox(self):
        response = self.client.get(self.published.get_absolute_url())

        self.assertContains(response, "data-gallery-track")
        self.assertContains(response, 'data-gallery-open="0"')
        self.assertContains(response, "data-gallery-lightbox")
        self.assertContains(response, "Следующая фотография")

    def test_listing_with_coordinates_links_to_focused_map_marker(self):
        response = self.client.get(self.published.get_absolute_url())

        self.assertContains(response, "Показать расположение на карте")
        self.assertContains(response, f"listing={self.published.public_id}")

    def test_listing_without_coordinates_has_no_map_link(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.pending.get_absolute_url())

        self.assertNotContains(response, "Показать расположение на карте")

    def test_listing_without_contact_phone_does_not_fall_back_to_profile_phone(self):
        self.pending.contact_phone = ""
        self.pending.save(update_fields=("contact_phone",))
        self.client.force_login(self.owner)

        response = self.client.get(self.pending.get_absolute_url())

        self.assertNotContains(response, 'href="tel:')
        self.assertNotContains(response, self.owner.phone)

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
        notification = Notification.objects.get(user=self.owner, listing=self.pending)
        self.assertEqual(notification.kind, Notification.Kind.LISTING_APPROVED)
        self.assertFalse(notification.is_read)

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
        notification = Notification.objects.get(user=self.owner, listing=self.pending)
        self.assertEqual(notification.kind, Notification.Kind.LISTING_REJECTED)
        self.assertEqual(notification.message, reason)

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


class ListingReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="report_owner", phone="+992900000070", email="report-owner@example.com", password="test-password-123"
        )
        cls.reporter = User.objects.create_user(
            username="report_sender", phone="+992900000071", email="report-sender@example.com", password="test-password-123"
        )
        cls.regular = User.objects.create_user(
            username="report_regular", phone="+992900000072", email="report-regular@example.com", password="test-password-123"
        )
        cls.moderator = User.objects.create_user(
            username="report_moderator",
            phone="+992900000073",
            email="report-moderator@example.com",
            password="test-password-123",
            is_moderator=True,
        )
        cls.listing = Listing.objects.create(
            owner=cls.owner,
            city=City.objects.get(slug="dushanbe"),
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Объявление для жалобы",
            description="Подробное описание опубликованного объявления для проверки жалоб.",
            price=Decimal("3000.00"),
            address="Улица Рудаки, 12",
        )

    def report_url(self):
        return reverse("report_listing", args=(self.listing.public_id,))

    def test_authenticated_non_owner_can_submit_one_pending_report(self):
        self.client.force_login(self.reporter)
        data = {"reason": ListingReport.Reason.INCORRECT, "details": "Цена указана неверно."}

        first = self.client.post(self.report_url(), data)
        second = self.client.post(self.report_url(), data)

        self.assertRedirects(first, self.listing.get_absolute_url())
        self.assertRedirects(second, self.listing.get_absolute_url())
        self.assertEqual(ListingReport.objects.count(), 1)
        report = ListingReport.objects.get()
        self.assertEqual(report.reporter, self.reporter)
        self.assertEqual(report.status, ListingReport.Status.PENDING)

    def test_owner_cannot_report_own_listing(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.report_url(),
            {"reason": ListingReport.Reason.FRAUD, "details": "Проверка запрета."},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ListingReport.objects.exists())

    def test_other_reason_requires_details(self):
        self.client.force_login(self.reporter)
        response = self.client.post(
            self.report_url(),
            {"reason": ListingReport.Reason.OTHER, "details": "коротко"},
        )
        self.assertRedirects(response, f"{self.listing.get_absolute_url()}#report-listing")
        self.assertFalse(ListingReport.objects.exists())

    def test_report_queue_is_private_and_contains_no_images(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse("listing_reports")).status_code, 403)

        ListingReport.objects.create(
            listing=self.listing,
            reporter=self.reporter,
            reason=ListingReport.Reason.FRAUD,
            details="Подозрительные условия оплаты.",
        )
        self.client.force_login(self.moderator)
        response = self.client.get(reverse("listing_reports"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.listing.title)
        self.assertNotContains(response, "<img")

    def test_moderator_can_confirm_report_with_comment(self):
        report = ListingReport.objects.create(
            listing=self.listing,
            reporter=self.reporter,
            reason=ListingReport.Reason.FRAUD,
        )
        url = reverse("review_listing_report", args=(report.public_id,))
        self.client.force_login(self.moderator)
        self.assertEqual(self.client.get(url).status_code, 405)

        response = self.client.post(
            url,
            {"decision": ListingReport.Status.CONFIRMED, "resolution_note": "Нарушение подтверждено."},
        )

        self.assertRedirects(response, reverse("listing_reports"))
        report.refresh_from_db()
        self.assertEqual(report.status, ListingReport.Status.CONFIRMED)
        self.assertEqual(report.moderator, self.moderator)
        self.assertIsNotNone(report.reviewed_at)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PUBLISHED)


class ModerationBlockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="block_owner",
            phone="+992900000090",
            email="block-owner@example.com",
            password="test-password-123",
        )
        cls.regular = User.objects.create_user(
            username="block_regular",
            phone="+992900000091",
            email="block-regular@example.com",
            password="test-password-123",
        )
        cls.moderator = User.objects.create_user(
            username="block_moderator",
            phone="+992900000092",
            email="block-moderator@example.com",
            password="test-password-123",
            is_moderator=True,
        )
        cls.other_moderator = User.objects.create_user(
            username="block_other_moderator",
            phone="+992900000093",
            email="block-other-moderator@example.com",
            password="test-password-123",
            is_moderator=True,
        )
        cls.listing = Listing.objects.create(
            owner=cls.owner,
            city=City.objects.get(slug="dushanbe"),
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Объявление для блокировки",
            description="Подробное описание объявления для проверки блокировки модератором.",
            price=Decimal("3500.00"),
            address="Улица Сомони, 15",
        )

    def block_listing_url(self):
        return reverse("block_listing", args=(self.listing.public_id,))

    def test_block_actions_require_post_and_moderator(self):
        self.client.force_login(self.regular)
        self.assertEqual(self.client.post(self.block_listing_url(), {"reason": "Нарушение правил", "duration": "7"}).status_code, 403)
        self.client.force_login(self.moderator)
        self.assertEqual(self.client.get(self.block_listing_url()).status_code, 405)

    def test_listing_block_is_reversible_and_preserves_favorites(self):
        Favorite.objects.create(user=self.regular, listing=self.listing)
        self.client.force_login(self.moderator)
        response = self.client.post(
            self.block_listing_url(),
            {"reason": "Подтверждённое нарушение правил.", "duration": "7"},
        )
        self.assertRedirects(response, self.listing.get_absolute_url())
        self.listing.refresh_from_db()
        block = ListingBlock.objects.get(listing=self.listing)
        self.assertEqual(self.listing.status, Listing.Status.BLOCKED)
        self.assertEqual(block.previous_status, Listing.Status.PUBLISHED)
        self.assertIsNotNone(block.expires_at)
        self.assertTrue(Favorite.objects.filter(user=self.regular, listing=self.listing).exists())
        self.assertTrue(Notification.objects.filter(user=self.owner, kind=Notification.Kind.LISTING_BLOCKED).exists())

        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(self.listing.get_absolute_url()).status_code, 404)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.listing.get_absolute_url()), "Подтверждённое нарушение правил")

        self.client.force_login(self.moderator)
        release = self.client.post(
            reverse("unblock_listing", args=(block.public_id,)),
            {"note": "Нарушение устранено."},
        )
        self.assertRedirects(release, reverse("moderation_blocks"))
        self.listing.refresh_from_db()
        block.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PUBLISHED)
        self.assertIsNotNone(block.unblocked_at)
        self.assertEqual(block.unblocked_by, self.moderator)
        self.assertTrue(Notification.objects.filter(user=self.owner, kind=Notification.Kind.LISTING_UNBLOCKED).exists())

    def test_account_block_disables_login_and_hides_published_listings(self):
        self.client.force_login(self.moderator)
        response = self.client.post(
            reverse("block_user", args=(self.owner.username,)),
            {"reason": "Систематическое нарушение правил.", "duration": "permanent"},
        )
        self.assertRedirects(response, reverse("moderation_blocks"))
        self.owner.refresh_from_db()
        block = UserBlock.objects.get(user=self.owner)
        self.assertFalse(self.owner.is_active)
        self.assertIsNone(block.expires_at)
        self.assertFalse(Listing.objects.published().filter(pk=self.listing.pk).exists())
        self.client.logout()
        self.assertFalse(self.client.login(username=self.owner.username, password="test-password-123"))

        self.client.force_login(self.moderator)
        response = self.client.post(
            reverse("unblock_user", args=(block.public_id,)),
            {"note": "Ограничение снято после проверки."},
        )
        self.assertRedirects(response, reverse("moderation_blocks"))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertTrue(Listing.objects.published().filter(pk=self.listing.pk).exists())
        self.assertTrue(Notification.objects.filter(user=self.owner, kind=Notification.Kind.ACCOUNT_UNBLOCKED).exists())

    def test_moderator_cannot_block_self_or_another_moderator(self):
        self.client.force_login(self.moderator)
        data = {"reason": "Недопустимая операция.", "duration": "7"}
        self.assertEqual(self.client.post(reverse("block_user", args=(self.moderator.username,)), data).status_code, 403)
        self.assertEqual(self.client.post(reverse("block_user", args=(self.other_moderator.username,)), data).status_code, 403)

    def test_expired_blocks_are_released_by_middleware(self):
        listing_block = ListingBlock.objects.create(
            listing=self.listing,
            moderator=self.moderator,
            reason="Временная проверка.",
            previous_status=Listing.Status.PUBLISHED,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.listing.status = Listing.Status.BLOCKED
        self.listing.save(update_fields=("status",))
        user_block = UserBlock.objects.create(
            user=self.owner,
            moderator=self.moderator,
            reason="Временная проверка аккаунта.",
            was_active=True,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.owner.is_active = False
        self.owner.save(update_fields=("is_active",))

        self.client.get(reverse("listing_list"))

        self.listing.refresh_from_db()
        self.owner.refresh_from_db()
        listing_block.refresh_from_db()
        user_block.refresh_from_db()
        self.assertEqual(self.listing.status, Listing.Status.PUBLISHED)
        self.assertTrue(self.owner.is_active)
        self.assertIsNotNone(listing_block.unblocked_at)
        self.assertIsNotNone(user_block.unblocked_at)

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
            "contact_phone": "90 765 43 21",
            "address": "Улица Рудаки, 31",
            "latitude": "38.575000",
            "longitude": "68.795000",
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
        self.assertEqual(self.listing.contact_phone, "+992907654321")

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


class ListingLifecycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.owner = User.objects.create_user(
            username="lifecycle_owner",
            phone="+992900000080",
            email="lifecycle-owner@example.com",
            password="test-password-123",
        )
        cls.outsider = User.objects.create_user(
            username="lifecycle_outsider",
            phone="+992900000081",
            email="lifecycle-outsider@example.com",
            password="test-password-123",
        )
        cls.city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.owner,
            "city": cls.city,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Подробное описание объявления для проверки жизненного цикла.",
            "price": Decimal("320000.00"),
            "address": "Проспект Рудаки, 80",
        }
        cls.sale_listing = Listing.objects.create(
            **common,
            deal_type=Listing.DealType.SALE,
            status=Listing.Status.PUBLISHED,
            title="Квартира для продажи и проверки статусов",
        )
        cls.rent_listing = Listing.objects.create(
            **common,
            deal_type=Listing.DealType.RENT,
            status=Listing.Status.PUBLISHED,
            title="Квартира для аренды и проверки статусов",
        )
        cls.draft_listing = Listing.objects.create(
            **common,
            deal_type=Listing.DealType.SALE,
            status=Listing.Status.DRAFT,
            title="Черновик для проверки недоступных действий",
        )

    def action_url(self, action, listing=None):
        return reverse(
            f"{action}_listing",
            args=((listing or self.sale_listing).public_id,),
        )

    def test_lifecycle_routes_require_post_login_and_owner(self):
        self.client.force_login(self.owner)
        for action in ("archive", "restore", "complete", "delete"):
            self.assertEqual(self.client.get(self.action_url(action)).status_code, 405)

        self.client.logout()
        archive_url = self.action_url("archive")
        response = self.client.post(archive_url)
        self.assertRedirects(response, f"{reverse('login')}?next={archive_url}")

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(archive_url).status_code, 404)
        self.sale_listing.refresh_from_db()
        self.assertEqual(self.sale_listing.status, Listing.Status.PUBLISHED)

    def test_owner_can_archive_and_restore_listing(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.action_url("archive"))

        self.assertRedirects(response, self.sale_listing.get_absolute_url())
        self.sale_listing.refresh_from_db()
        self.assertEqual(self.sale_listing.status, Listing.Status.ARCHIVED)
        owner_preview = self.client.get(self.sale_listing.get_absolute_url())
        self.assertContains(owner_preview, "Снято с публикации")
        self.assertContains(owner_preview, self.action_url("restore"))

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.sale_listing.get_absolute_url()).status_code, 404)

        self.client.force_login(self.owner)
        response = self.client.post(self.action_url("restore"))
        self.assertRedirects(response, self.sale_listing.get_absolute_url())
        self.sale_listing.refresh_from_db()
        self.assertEqual(self.sale_listing.status, Listing.Status.PUBLISHED)
        self.assertIsNotNone(self.sale_listing.published_at)

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.sale_listing.get_absolute_url()).status_code, 200)

    def test_completed_status_is_sold_or_rented_by_deal_type(self):
        self.client.force_login(self.owner)

        self.client.post(self.action_url("complete", self.sale_listing))
        self.client.post(self.action_url("complete", self.rent_listing))

        self.sale_listing.refresh_from_db()
        self.rent_listing.refresh_from_db()
        self.assertEqual(self.sale_listing.status, Listing.Status.COMPLETED)
        self.assertEqual(self.rent_listing.status, Listing.Status.COMPLETED)
        self.assertEqual(str(self.sale_listing.status_label), "Продано")
        self.assertEqual(str(self.rent_listing.status_label), "Сдано")
        self.assertContains(self.client.get(self.sale_listing.get_absolute_url()), "Продано")
        self.assertContains(self.client.get(self.rent_listing.get_absolute_url()), "Сдано")

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.sale_listing.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.get(self.rent_listing.get_absolute_url()).status_code, 404)

    def test_invalid_transition_does_not_change_draft(self):
        self.client.force_login(self.owner)

        for action in ("archive", "restore", "complete"):
            response = self.client.post(self.action_url(action, self.draft_listing))
            self.assertRedirects(response, self.draft_listing.get_absolute_url())
            self.draft_listing.refresh_from_db()
            self.assertEqual(self.draft_listing.status, Listing.Status.DRAFT)

    def test_soft_delete_hides_listing_and_preserves_moderation_history(self):
        Favorite.objects.create(user=self.outsider, listing=self.sale_listing)
        decision = ModerationDecision.objects.create(
            listing=self.sale_listing,
            moderator=None,
            decision=ModerationDecision.Decision.APPROVED,
        )
        self.client.force_login(self.owner)

        response = self.client.post(self.action_url("delete"))

        self.assertRedirects(response, reverse("profile_listings"))
        self.sale_listing.refresh_from_db()
        self.assertEqual(self.sale_listing.status, Listing.Status.DELETED)
        self.assertIsNotNone(self.sale_listing.deleted_at)
        self.assertFalse(Favorite.objects.filter(listing=self.sale_listing).exists())
        self.assertTrue(ModerationDecision.objects.filter(pk=decision.pk).exists())
        self.assertEqual(self.client.get(self.sale_listing.get_absolute_url()).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("edit_listing", args=(self.sale_listing.public_id,))).status_code,
            404,
        )
        self.assertNotContains(self.client.get(reverse("profile_listings")), self.sale_listing.title)

    def test_archived_favorite_returns_after_restore(self):
        Favorite.objects.create(user=self.outsider, listing=self.sale_listing)
        self.client.force_login(self.owner)
        self.client.post(self.action_url("archive"))

        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(reverse("favorites")), self.sale_listing.title)

        self.client.force_login(self.owner)
        self.client.post(self.action_url("restore"))

        self.client.force_login(self.outsider)
        self.assertContains(self.client.get(reverse("favorites")), self.sale_listing.title)

    def test_owner_sees_management_controls_only_on_own_listing(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.sale_listing.get_absolute_url())
        self.assertContains(response, "Управление объявлением")
        self.assertContains(response, self.action_url("archive"))
        self.assertContains(response, self.action_url("complete"))
        self.assertContains(response, self.action_url("delete"))
        self.assertContains(response, "data-listing-action-dialog")

        self.client.force_login(self.outsider)
        response = self.client.get(self.sale_listing.get_absolute_url())
        self.assertNotContains(response, "Управление объявлением")
        self.assertNotContains(response, "data-listing-action-dialog")


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


class SeoAndLegalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(
            username="seo_owner",
            email="seo-owner@example.com",
            password="test-password-123",
        )
        cls.city = City.objects.get(slug="dushanbe")
        common = {
            "owner": cls.owner,
            "city": cls.city,
            "deal_type": Listing.DealType.SALE,
            "property_type": Listing.PropertyType.APARTMENT,
            "description": "Подробное описание квартиры для проверки поисковых метаданных.",
            "price": Decimal("480000.00"),
            "address": "Проспект Рудаки, 20",
        }
        cls.published = Listing.objects.create(
            **common,
            title="Квартира для SEO",
            status=Listing.Status.PUBLISHED,
            rooms=2,
            area=Decimal("72.50"),
            latitude=Decimal("38.573100"),
            longitude=Decimal("68.786400"),
        )
        cls.pending = Listing.objects.create(
            **common,
            title="Скрытая квартира",
            status=Listing.Status.PENDING,
        )
        cls.city_listing = Listing.objects.create(
            **{**common, "deal_type": Listing.DealType.RENT},
            title="Квартира в городской выдаче",
            status=Listing.Status.PUBLISHED,
        )

    def setUp(self):
        translation.activate("ru")

    def test_public_page_has_local_css_and_complete_basic_metadata(self):
        response = self.client.get(
            reverse("listing_list"),
            secure=True,
            HTTP_HOST="domly.site",
        )

        self.assertContains(response, 'href="/static/css/app.css?v=20260817-3"')
        self.assertContains(
            response,
            'rel="icon" type="image/png" href="/static/images/domly-icon-v3-clean-transparent.png?v=20260817-1"',
            html=False,
        )
        self.assertContains(
            response,
            'rel="apple-touch-icon" href="/static/images/domly-icon-v3-clean-transparent.png?v=20260817-1"',
            html=False,
        )
        self.assertContains(response, '<meta name="theme-color" content="#000000">')
        self.assertNotContains(response, "cdn.tailwindcss.com")
        self.assertContains(response, '<meta name="description"')
        self.assertContains(response, '<meta name="robots" content="index,follow">')
        self.assertContains(
            response,
            '<link rel="canonical" href="https://domly.site/">',
            html=False,
        )
        self.assertContains(response, '<meta property="og:title"')
        self.assertContains(response, '<meta property="og:url" content="https://domly.site/">')

    def test_private_page_is_not_indexable(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, '<meta name="robots" content="noindex,nofollow">')

    def test_listing_metadata_uses_listing_content(self):
        response = self.client.get(
            self.published.get_absolute_url(),
            secure=True,
            HTTP_HOST="domly.site",
        )

        self.assertContains(response, '<meta property="og:type" content="article">')
        self.assertContains(response, "Квартира для SEO — Душанбе | Domly")
        self.assertContains(response, "Подробное описание квартиры")
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://domly.site{self.published.get_absolute_url()}">',
            html=False,
        )

    def test_city_page_has_stable_url_and_only_its_city_listings(self):
        url = reverse("city_listings", kwargs={"city_slug": self.city.slug})
        response = self.client.get(
            url,
            secure=True,
            HTTP_HOST="domly.site",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.city_listing.title)
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://domly.site{url}">',
            html=False,
        )
        self.assertContains(response, '<meta name="robots" content="index,follow">')
        self.assertEqual(self.client.get("/city/not-a-city/").status_code, 404)

    def test_city_filter_redirects_to_the_selected_city_page(self):
        khujand = City.objects.get(slug="khujand")
        response = self.client.get(
            reverse("city_listings", kwargs={"city_slug": self.city.slug}),
            {"city": khujand.name, "sort": "price_asc"},
        )

        expected = reverse("city_listings", kwargs={"city_slug": khujand.slug})
        self.assertRedirects(response, f"{expected}?sort=price_asc")

    def test_listing_has_reciprocal_language_links(self):
        russian_url = self.published.get_absolute_url()
        with translation.override("tg"):
            tajik_url = self.published.get_absolute_url()
        with translation.override("en"):
            english_url = self.published.get_absolute_url()

        response = self.client.get(
            english_url,
            secure=True,
            HTTP_HOST="domly.site",
        )

        self.assertContains(response, '<html lang="en" data-theme="light">', html=False)
        self.assertContains(response, f'hreflang="ru" href="https://domly.site{russian_url}"')
        self.assertContains(response, f'hreflang="tg" href="https://domly.site{tajik_url}"')
        self.assertContains(response, f'hreflang="en" href="https://domly.site{english_url}"')
        self.assertContains(response, f'hreflang="x-default" href="https://domly.site{russian_url}"')
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://domly.site{english_url}">',
            html=False,
        )

    def test_published_listing_has_safe_json_ld(self):
        unsafe_title = '</script><script>alert("x")</script>'
        self.published.title = unsafe_title
        self.published.save(update_fields=["title", "updated_at"])
        response = self.client.get(
            self.published.get_absolute_url(),
            secure=True,
            HTTP_HOST="domly.site",
        )
        html = response.content.decode()
        marker = '<script type="application/ld+json" id="listing-structured-data">'
        payload = html.split(marker, 1)[1].split("</script>", 1)[0]
        data = json.loads(payload)

        self.assertNotIn(unsafe_title, payload)
        self.assertEqual(data["name"], unsafe_title)
        self.assertEqual(data["@type"], "RealEstateListing")
        self.assertEqual(data["offers"]["price"], "480000.00")
        self.assertEqual(data["offers"]["priceCurrency"], "TJS")
        self.assertEqual(data["about"]["@type"], "Apartment")
        self.assertEqual(data["about"]["numberOfRooms"], 2)
        self.assertEqual(data["about"]["address"]["addressCountry"], "TJ")
        self.assertEqual(data["about"]["geo"]["latitude"], "38.573100")

    def test_unpublished_preview_is_noindex_and_has_no_json_ld(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.pending.get_absolute_url())

        self.assertContains(response, '<meta name="robots" content="noindex,nofollow">')
        self.assertNotContains(response, 'id="listing-structured-data"')

    def test_sitemap_contains_only_published_listings(self):
        response = self.client.get(
            reverse("sitemap"),
            secure=True,
            HTTP_HOST="domly.site",
        )
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"https://domly.site{self.published.get_absolute_url()}", content)
        self.assertNotIn(str(self.pending.public_id), content)
        self.assertIn("https://domly.site/privacy/", content)
        self.assertIn("https://domly.site/city/dushanbe/", content)
        self.assertIn("https://domly.site/tg/city/dushanbe/", content)
        self.assertIn("https://domly.site/en/city/dushanbe/", content)
        self.assertIn('hreflang="x-default"', content)

    def test_robots_references_sitemap_and_blocks_private_sections(self):
        response = self.client.get(
            reverse("robots_txt"),
            secure=True,
            HTTP_HOST="domly.site",
        )
        content = response.content.decode()

        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertIn("Allow: /", content)
        self.assertIn("Disallow: /auth/", content)
        self.assertIn("Disallow: /messages/", content)
        self.assertIn("Disallow: /tg/messages/", content)
        self.assertIn("Disallow: /en/messages/", content)
        self.assertIn("Sitemap: https://domly.site/sitemap.xml", content)

    def test_healthcheck_verifies_database_and_cache(self):
        response = self.client.get(reverse("healthcheck"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

        head_response = self.client.head(reverse("healthcheck"))
        self.assertEqual(head_response.status_code, 200)

    def test_legal_pages_are_public_and_linked_from_footer(self):
        expected = {
            "privacy_policy": "Политика конфиденциальности",
            "terms_of_use": "Пользовательское соглашение",
            "publication_rules": "Правила публикации и модерации",
        }
        for url_name, heading in expected.items():
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)
                self.assertContains(response, settings.LEGAL_CONTACT_EMAIL)
                self.assertContains(response, '<meta name="robots" content="index,follow">')
                self.assertNotContains(response, "Важно перед запуском:")

        privacy = self.client.get(reverse("privacy_policy"))
        self.assertContains(privacy, "трансграничную передачу")
        self.assertContains(privacy, "Обладателем базы персональных данных")

        terms = self.client.get(reverse("terms_of_use"))
        self.assertContains(terms, "не является собственником недвижимости")
        self.assertContains(terms, "обязательных прав")
        self.assertContains(terms, "не заменяют договор")

        rules = self.client.get(reverse("publication_rules"))
        self.assertContains(rules, "Модерация не является юридической экспертизой")

        footer = self.client.get(reverse("listing_list"))
        for url_name in expected:
            self.assertContains(footer, reverse(url_name))

    def test_compiled_css_asset_exists_and_contains_responsive_utilities(self):
        css_path = Path(settings.BASE_DIR) / "static" / "css" / "app.css"
        css = css_path.read_text(encoding="utf-8")

        self.assertGreater(len(css), 10000)
        self.assertIn(".md\\:block", css)
        self.assertIn("html.dark", css)
