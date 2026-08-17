from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import EmailCodeAttempt, Notification, RegistrationAttempt, User, UserBlock


@admin.register(User)
class DomlyUserAdmin(UserAdmin):
    list_display = (
        "username",
        "email",
        "is_email_verified",
        "is_moderator",
        "is_staff",
    )
    list_filter = UserAdmin.list_filter + ("is_moderator",)
    search_fields = ("username", "phone", "email")
    readonly_fields = UserAdmin.readonly_fields + (
        "terms_accepted_at",
        "terms_version",
        "privacy_consent_at",
        "privacy_policy_version",
    )
    fieldsets = UserAdmin.fieldsets + (
        (
            "Domly",
            {
                "fields": (
                    "phone",
                    "is_phone_verified",
                    "is_email_verified",
                    "is_moderator",
                    "avatar",
                    "terms_accepted_at",
                    "terms_version",
                    "privacy_consent_at",
                    "privacy_policy_version",
                )
            },
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Domly",
            {"fields": ("email", "is_email_verified", "phone", "is_phone_verified", "is_moderator")},
        ),
    )


@admin.register(RegistrationAttempt)
class RegistrationAttemptAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "created_at", "expires_at", "failed_attempts")
    search_fields = ("username", "email")
    readonly_fields = (
        "username",
        "email",
        "password_hash",
        "code_hash",
        "created_at",
        "expires_at",
        "last_sent_at",
        "send_count",
        "failed_attempts",
        "request_ip",
        "terms_accepted_at",
        "terms_version",
        "privacy_consent_at",
        "privacy_policy_version",
    )


@admin.register(EmailCodeAttempt)
class EmailCodeAttemptAdmin(admin.ModelAdmin):
    list_display = ("email", "purpose", "user", "created_at", "expires_at", "verified_at")
    list_filter = ("purpose", "verified_at", "created_at")
    search_fields = ("email", "user__username")
    readonly_fields = (
        "public_id",
        "purpose",
        "email",
        "user",
        "code_hash",
        "created_at",
        "expires_at",
        "last_sent_at",
        "send_count",
        "failed_attempts",
        "verified_at",
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
