from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Notification, RegistrationAttempt, User, UserBlock


@admin.register(User)
class DomlyUserAdmin(UserAdmin):
    list_display = (
        "username",
        "phone",
        "email",
        "is_phone_verified",
        "is_moderator",
        "is_staff",
    )
    list_filter = UserAdmin.list_filter + ("is_moderator",)
    search_fields = ("username", "phone", "email")
    fieldsets = UserAdmin.fieldsets + (
        (
            "Domly",
            {"fields": ("phone", "is_phone_verified", "is_moderator", "avatar")},
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Domly",
            {"fields": ("phone", "email", "is_phone_verified", "is_moderator")},
        ),
    )


@admin.register(RegistrationAttempt)
class RegistrationAttemptAdmin(admin.ModelAdmin):
    list_display = ("username", "phone", "created_at", "expires_at", "failed_attempts")
    search_fields = ("username", "phone", "email")
    readonly_fields = (
        "username",
        "phone",
        "email",
        "password_hash",
        "code_hash",
        "created_at",
        "expires_at",
        "last_sent_at",
        "failed_attempts",
        "request_ip",
    )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "listing", "is_read", "created_at")
    list_filter = ("kind", "is_read", "created_at")
    search_fields = ("user__username", "listing__title", "message")
    readonly_fields = ("public_id", "created_at", "read_at")
    list_select_related = ("user", "listing")


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ("user", "moderator", "blocked_at", "expires_at", "unblocked_at")
    list_filter = ("blocked_at", "expires_at", "unblocked_at")
    search_fields = ("user__username", "reason", "unblock_note")
    readonly_fields = ("public_id", "blocked_at", "unblocked_at")
    list_select_related = ("user", "moderator", "unblocked_by")
