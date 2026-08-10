from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import RegistrationAttempt, User


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
