from django.contrib import admin

from .models import City, Favorite, Listing, ListingImage, ModerationDecision


class ListingImageInline(admin.TabularInline):
    model = ListingImage
    extra = 0


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    list_filter = ("is_active",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "city",
        "deal_type",
        "property_type",
        "price",
        "currency",
        "status",
        "created_at",
    )
    list_filter = ("status", "deal_type", "property_type", "currency", "city")
    search_fields = ("title", "description", "address", "owner__username")
    readonly_fields = (
        "public_id",
        "created_at",
        "updated_at",
        "submitted_at",
        "published_at",
    )
    autocomplete_fields = ("owner", "city")
    inlines = (ListingImageInline,)


@admin.register(ListingImage)
class ListingImageAdmin(admin.ModelAdmin):
    list_display = ("listing", "position", "created_at")
    list_select_related = ("listing",)


@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "listing", "created_at")
    search_fields = ("user__username", "listing__title")
    autocomplete_fields = ("user", "listing")
    list_select_related = ("user", "listing")


@admin.register(ModerationDecision)
class ModerationDecisionAdmin(admin.ModelAdmin):
    list_display = ("listing", "decision", "moderator", "created_at")
    list_filter = ("decision", "created_at")
    search_fields = ("listing__title", "moderator__username", "reason")
    autocomplete_fields = ("listing", "moderator")
    readonly_fields = ("created_at",)
    list_select_related = ("listing", "moderator")
