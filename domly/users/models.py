from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.db.models.functions import Lower
from django.dispatch import receiver
from django.utils import timezone

from domly.image_processing import process_avatar


class User(AbstractUser):
    phone = models.CharField(max_length=16, blank=True, null=True, unique=True)
    email = models.EmailField(blank=True, null=True, unique=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    is_moderator = models.BooleanField(
        default=False,
        help_text="Даёт доступ к разделу модерации объявлений.",
    )
    terms_accepted_at = models.DateTimeField(blank=True, null=True)
    terms_version = models.CharField(max_length=20, blank=True)
    privacy_consent_at = models.DateTimeField(blank=True, null=True)
    privacy_policy_version = models.CharField(max_length=20, blank=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

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

    def save(self, *args, **kwargs):
        if self.avatar and not self.avatar._committed:
            processed = process_avatar(self.avatar.file, self.avatar.name)
            self.avatar = processed.file
        super().save(*args, **kwargs)


@receiver(pre_save, sender=User)
def remember_replaced_avatar(sender, instance, **kwargs):
    if not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).only("avatar").first()
    if previous and previous.avatar:
        instance._replaced_avatar_name = previous.avatar.name


@receiver(post_save, sender=User)
def delete_replaced_avatar(sender, instance, **kwargs):
    old_name = getattr(instance, "_replaced_avatar_name", "")
    current_name = instance.avatar.name if instance.avatar else ""
    if old_name and old_name != current_name:
        transaction.on_commit(lambda: instance.avatar.storage.delete(old_name))


@receiver(post_delete, sender=User)
def delete_user_avatar(sender, instance, **kwargs):
    if instance.avatar:
        storage = instance.avatar.storage
        name = instance.avatar.name
        transaction.on_commit(lambda: storage.delete(name))


class RegistrationAttempt(models.Model):
    username = models.CharField(max_length=150)
    email = models.EmailField(db_index=True)
    password_hash = models.CharField(max_length=128)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_sent_at = models.DateTimeField(default=timezone.now)
    send_count = models.PositiveSmallIntegerField(default=1)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    request_ip = models.GenericIPAddressField(blank=True, null=True)
    terms_accepted_at = models.DateTimeField()
    terms_version = models.CharField(max_length=20)
    privacy_consent_at = models.DateTimeField()
    privacy_policy_version = models.CharField(max_length=20)

    MAX_FAILED_ATTEMPTS = 5
    MAX_SENDS = 5
    CODE_LIFETIME = timedelta(minutes=10)
    RESEND_COOLDOWN = timedelta(seconds=60)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_locked(self):
        return self.failed_attempts >= self.MAX_FAILED_ATTEMPTS

    @property
    def can_resend(self):
        return (
            self.send_count < self.MAX_SENDS
            and timezone.now() >= self.last_sent_at + self.RESEND_COOLDOWN
        )

    def __str__(self):
        return f"{self.username} ({self.email})"


class EmailCodeAttempt(models.Model):
    class Purpose(models.TextChoices):
        PASSWORD_RESET = "password_reset", "Восстановление пароля"
        EMAIL_CHANGE = "email_change", "Смена email"

    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, db_index=True)
    email = models.EmailField(db_index=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="email_code_attempts",
    )
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_sent_at = models.DateTimeField(default=timezone.now)
    send_count = models.PositiveSmallIntegerField(default=1)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(blank=True, null=True)
    request_ip = models.GenericIPAddressField(blank=True, null=True)

    MAX_FAILED_ATTEMPTS = 5
    MAX_SENDS = 5
    CODE_LIFETIME = timedelta(minutes=10)
    RESEND_COOLDOWN = timedelta(seconds=60)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(
                fields=("purpose", "email", "-created_at"),
                name="email_code_purpose_idx",
            ),
        ]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_locked(self):
        return self.failed_attempts >= self.MAX_FAILED_ATTEMPTS

    @property
    def can_resend(self):
        return (
            self.send_count < self.MAX_SENDS
            and timezone.now() >= self.last_sent_at + self.RESEND_COOLDOWN
        )

    def __str__(self):
        return f"{self.get_purpose_display()}: {self.email}"


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
