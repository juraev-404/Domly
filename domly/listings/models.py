from pathlib import PurePath
from uuid import uuid4

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from domly.image_processing import process_listing_image


class City(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    map_zoom = models.PositiveSmallIntegerField(
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
    )
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "город"
        verbose_name_plural = "города"

    def __str__(self):
        return self.name


class ListingQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Listing.Status.PUBLISHED, owner__is_active=True)


class Listing(models.Model):
    class DealType(models.TextChoices):
        SALE = "sale", _("Продажа")
        RENT = "rent", _("Аренда")

    class PropertyType(models.TextChoices):
        APARTMENT = "apartment", _("Квартира")
        HOUSE = "house", _("Дом")
        ROOM = "room", _("Комната")
        LAND = "land", _("Участок")
        COMMERCIAL = "commercial", _("Коммерческая недвижимость")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Черновик")
        PENDING = "pending", _("На модерации")
        PUBLISHED = "published", _("Опубликовано")
        REJECTED = "rejected", _("Отклонено")
        ARCHIVED = "archived", _("Снято с публикации")
        COMPLETED = "completed", _("Сделка завершена")
        BLOCKED = "blocked", _("Заблокировано модератором")
        DELETED = "deleted", _("Удалено")

    class Currency(models.TextChoices):
        TJS = "TJS", _("Сомони")
        USD = "USD", _("Доллар США")

    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    city = models.ForeignKey(
        City,
        on_delete=models.PROTECT,
        related_name="listings",
    )
    deal_type = models.CharField(max_length=8, choices=DealType.choices)
    property_type = models.CharField(max_length=16, choices=PropertyType.choices)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    title = models.CharField(max_length=200)
    description = models.TextField()
    price = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.TJS,
    )
    is_negotiable = models.BooleanField(default=False)
    contact_phone = models.CharField(
        max_length=16,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^\+[1-9]\d{7,14}$",
                message=_("Введите номер в международном формате, например +992900001122."),
            )
        ],
        verbose_name=_("Номер для связи"),
        help_text=_("Необязательно. Этот номер будет виден на странице объявления."),
    )

    address = models.CharField(max_length=255)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        blank=True,
        null=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    rooms = models.PositiveSmallIntegerField(blank=True, null=True)
    area = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Площадь в квадратных метрах",
    )
    floor = models.PositiveSmallIntegerField(blank=True, null=True)
    total_floors = models.PositiveSmallIntegerField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(blank=True, null=True, db_index=True)
    published_at = models.DateTimeField(blank=True, null=True)
    deleted_at = models.DateTimeField(blank=True, null=True, db_index=True)

    objects = ListingQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "объявление"
        verbose_name_plural = "объявления"
        indexes = [
            models.Index(fields=("city", "status", "-created_at"), name="listing_city_status_idx"),
            models.Index(fields=("deal_type", "property_type"), name="listing_deal_type_idx"),
            models.Index(fields=("price",), name="listing_price_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="listing_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(area__isnull=True) | Q(area__gt=0),
                name="listing_area_positive",
            ),
            models.CheckConstraint(
                condition=Q(rooms__isnull=True) | Q(rooms__gt=0),
                name="listing_rooms_positive",
            ),
            models.CheckConstraint(
                condition=Q(latitude__isnull=True) | Q(latitude__range=(-90, 90)),
                name="listing_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__isnull=True) | Q(longitude__range=(-180, 180)),
                name="listing_longitude_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(floor__isnull=True)
                    | Q(total_floors__isnull=True)
                    | Q(floor__lte=F("total_floors"))
                ),
                name="listing_floor_lte_total",
            ),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("listing_detail", kwargs={"public_id": self.public_id})

    @property
    def status_label(self):
        if self.status == self.Status.COMPLETED:
            if self.deal_type == self.DealType.SALE:
                return _("Продано")
            return _("Сдано")
        return self.get_status_display()


def listing_image_upload_to(instance, filename):
    safe_filename = PurePath(filename).name
    return f"listings/{instance.listing.public_id}/{safe_filename}"


def listing_thumbnail_upload_to(instance, filename):
    safe_filename = PurePath(filename).name
    return f"listings/{instance.listing.public_id}/thumbnails/{safe_filename}"


class ListingImage(models.Model):
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to=listing_image_upload_to)
    thumbnail = models.ImageField(
        upload_to=listing_thumbnail_upload_to,
        blank=True,
    )
    alt_text = models.CharField(max_length=200, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "фотография объявления"
        verbose_name_plural = "фотографии объявлений"
        indexes = [
            models.Index(fields=("listing", "position"), name="listing_image_order_idx"),
        ]

    def __str__(self):
        return f"{self.listing}: {self.position}"

    def save(self, *args, **kwargs):
        if self.image and not self.image._committed:
            processed, thumbnail = process_listing_image(
                self.image.file,
                self.image.name,
            )
            self.image = processed.file
            self.thumbnail = thumbnail.file
        super().save(*args, **kwargs)


@receiver(pre_save, sender=ListingImage)
def remember_replaced_listing_files(sender, instance, **kwargs):
    if not instance.pk:
        return
    previous = sender.objects.filter(pk=instance.pk).only("image", "thumbnail").first()
    if previous:
        instance._replaced_listing_files = {
            previous.image.name if previous.image else "",
            previous.thumbnail.name if previous.thumbnail else "",
        }


@receiver(post_save, sender=ListingImage)
def delete_replaced_listing_files(sender, instance, **kwargs):
    current_names = {
        instance.image.name if instance.image else "",
        instance.thumbnail.name if instance.thumbnail else "",
    }
    for name in getattr(instance, "_replaced_listing_files", set()) - current_names - {""}:
        transaction.on_commit(lambda name=name: instance.image.storage.delete(name))


@receiver(post_delete, sender=ListingImage)
def delete_listing_image_files(sender, instance, **kwargs):
    storage = instance.image.storage
    names = {
        instance.image.name if instance.image else "",
        instance.thumbnail.name if instance.thumbnail else "",
    }
    for name in names - {""}:
        transaction.on_commit(lambda name=name: storage.delete(name))


class Favorite(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorites",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="favorited_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "избранное"
        verbose_name_plural = "избранное"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "listing"),
                name="favorite_user_listing_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "-created_at"), name="favorite_user_created_idx"),
        ]

    def __str__(self):
        return f"{self.user} — {self.listing}"


class ModerationDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved", _("Одобрено")
        REJECTED = "rejected", _("Отклонено")

    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="moderation_decisions",
    )
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="moderation_decisions",
    )
    decision = models.CharField(max_length=8, choices=Decision.choices)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = _("решение модерации")
        verbose_name_plural = _("решения модерации")
        indexes = [
            models.Index(
                fields=("listing", "-created_at"),
                name="moderation_listing_date_idx",
            ),
        ]

    def __str__(self):
        return f"{self.listing}: {self.get_decision_display()}"


class ListingReport(models.Model):
    class Reason(models.TextChoices):
        FRAUD = "fraud", _("Мошенничество")
        DUPLICATE = "duplicate", _("Дубликат")
        INCORRECT = "incorrect", _("Неверная информация")
        UNAVAILABLE = "unavailable", _("Объект уже недоступен")
        OFFENSIVE = "offensive", _("Недопустимое содержание")
        OTHER = "other", _("Другое")

    class Status(models.TextChoices):
        PENDING = "pending", _("На рассмотрении")
        CONFIRMED = "confirmed", _("Подтверждена")
        DISMISSED = "dismissed", _("Отклонена")

    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="listing_reports",
    )
    reason = models.CharField(max_length=16, choices=Reason.choices)
    details = models.TextField(blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_listing_reports",
    )
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("created_at", "pk")
        verbose_name = _("жалоба на объявление")
        verbose_name_plural = _("жалобы на объявления")
        constraints = [
            models.UniqueConstraint(
                fields=("listing", "reporter"),
                condition=Q(status="pending"),
                name="listing_report_one_pending",
            ),
        ]
        indexes = [
            models.Index(
                fields=("status", "created_at"),
                name="report_status_created_idx",
            ),
        ]

    def __str__(self):
        return f"{self.listing}: {self.get_reason_display()}"


class ListingBlock(models.Model):
    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="moderation_blocks",
    )
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_listing_blocks",
    )
    reason = models.TextField()
    previous_status = models.CharField(max_length=12, choices=Listing.Status.choices)
    blocked_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    unblocked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    unblocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="released_listing_blocks",
    )
    unblock_note = models.TextField(blank=True)

    class Meta:
        ordering = ("-blocked_at", "-pk")
        verbose_name = _("блокировка объявления")
        verbose_name_plural = _("блокировки объявлений")
        constraints = [
            models.UniqueConstraint(
                fields=("listing",),
                condition=Q(unblocked_at__isnull=True),
                name="listing_one_active_block",
            ),
        ]
        indexes = [
            models.Index(
                fields=("unblocked_at", "expires_at"),
                name="listing_block_expiry_idx",
            ),
        ]

    @property
    def is_active(self):
        return self.unblocked_at is None

    def __str__(self):
        return f"{self.listing}: {self.reason[:60]}"
