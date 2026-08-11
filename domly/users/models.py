from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class User(AbstractUser):
    phone = models.CharField(max_length=16, unique=True)
    email = models.EmailField(blank=True, null=True, unique=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    is_phone_verified = models.BooleanField(default=False)
    is_moderator = models.BooleanField(
        default=False,
        help_text="Даёт доступ к разделу модерации объявлений.",
    )

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["phone"]

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower("username"), name="users_username_case_insensitive_unique"
            ),
            models.UniqueConstraint(
                Lower("email"),
                condition=models.Q(email__isnull=False),
                name="users_email_case_insensitive_unique",
            ),
        ]

    def __str__(self):
        return self.username


class RegistrationAttempt(models.Model):
    username = models.CharField(max_length=150)
    phone = models.CharField(max_length=16, db_index=True)
    email = models.EmailField(blank=True, null=True)
    password_hash = models.CharField(max_length=128)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_sent_at = models.DateTimeField(auto_now_add=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    request_ip = models.GenericIPAddressField(blank=True, null=True)

    MAX_FAILED_ATTEMPTS = 5
    CODE_LIFETIME = timedelta(minutes=5)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_locked(self):
        return self.failed_attempts >= self.MAX_FAILED_ATTEMPTS

    def __str__(self):
        return f"{self.username} ({self.phone})"


class Notification(models.Model):
    class Kind(models.TextChoices):
        LISTING_APPROVED = "listing_approved", "Объявление одобрено"
        LISTING_REJECTED = "listing_rejected", "Объявление отклонено"
        LISTING_BLOCKED = "listing_blocked", "Объявление заблокировано"
        LISTING_UNBLOCKED = "listing_unblocked", "Объявление разблокировано"
        ACCOUNT_BLOCKED = "account_blocked", "Аккаунт заблокирован"
        ACCOUNT_UNBLOCKED = "account_unblocked", "Аккаунт разблокирован"

    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    listing = models.ForeignKey(
        "listings.Listing",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="notifications",
    )
    kind = models.CharField(max_length=24, choices=Kind.choices)
    message = models.TextField(blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "уведомление"
        verbose_name_plural = "уведомления"
        indexes = [
            models.Index(
                fields=("user", "is_read", "-created_at"),
                name="notification_user_read_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user}: {self.get_kind_display()}"


class UserBlock(models.Model):
    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="moderation_blocks",
    )
    moderator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_user_blocks",
    )
    reason = models.TextField()
    was_active = models.BooleanField(default=True)
    blocked_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    unblocked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    unblocked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="released_user_blocks",
    )
    unblock_note = models.TextField(blank=True)

    class Meta:
        ordering = ("-blocked_at", "-pk")
        verbose_name = "блокировка аккаунта"
        verbose_name_plural = "блокировки аккаунтов"
        constraints = [
            models.UniqueConstraint(
                fields=("user",),
                condition=models.Q(unblocked_at__isnull=True),
                name="user_one_active_block",
            ),
        ]
        indexes = [
            models.Index(
                fields=("unblocked_at", "expires_at"),
                name="user_block_expiry_idx",
            ),
        ]

    @property
    def is_active(self):
        return self.unblocked_at is None

    def __str__(self):
        return f"{self.user}: {self.reason[:60]}"
