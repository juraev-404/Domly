from datetime import timedelta

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
