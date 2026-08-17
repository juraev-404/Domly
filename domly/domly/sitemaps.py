from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from listings.models import City, Listing


class LocalizedSitemap(Sitemap):
    i18n = True
    alternates = True
    x_default = True


class StaticViewSitemap(LocalizedSitemap):
    changefreq = "monthly"

    def items(self):
        return (
            "listing_list",
            "help",
            "privacy_policy",
            "terms_of_use",
            "publication_rules",
        )

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0 if item == "listing_list" else 0.4


class CitySitemap(LocalizedSitemap):
    changefreq = "daily"
    priority = 0.9

    def items(self):
        return City.objects.filter(is_active=True).order_by("slug")

    def location(self, city):
        return reverse("city_listings", kwargs={"city_slug": city.slug})


class ListingSitemap(LocalizedSitemap):
    changefreq = "daily"
    priority = 0.8

    def items(self):
        return Listing.objects.published().select_related("owner").order_by("pk")

    def lastmod(self, listing):
        return listing.updated_at


sitemaps = {
    "static": StaticViewSitemap,
    "cities": CitySitemap,
    "listings": ListingSitemap,
}
