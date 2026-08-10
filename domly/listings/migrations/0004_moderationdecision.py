import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_submitted_at(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Listing.objects.filter(
        status__in=("pending", "published", "rejected")
    ).update(submitted_at=models.F("updated_at"))


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("listings", "0003_favorite_favorite_favorite_user_listing_unique"),
    ]

    operations = [
        migrations.AddField(
            model_name="listing",
            name="submitted_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_submitted_at, migrations.RunPython.noop),
        migrations.CreateModel(
            name="ModerationDecision",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "decision",
                    models.CharField(
                        choices=[("approved", "Одобрено"), ("rejected", "Отклонено")],
                        max_length=8,
                    ),
                ),
                ("reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "listing",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="moderation_decisions",
                        to="listings.listing",
                    ),
                ),
                (
                    "moderator",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="moderation_decisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "решение модерации",
                "verbose_name_plural": "решения модерации",
                "ordering": ("-created_at", "-pk"),
                "indexes": [
                    models.Index(
                        fields=["listing", "-created_at"],
                        name="moderation_listing_date_idx",
                    )
                ],
            },
        ),
    ]
