from decimal import Decimal

from django.db import migrations


CITIES = (
    ("Душанбе", "dushanbe", "38.559800", "68.787000", 12),
    ("Худжанд", "khujand", "40.283300", "69.622200", 12),
    ("Бохтар", "bokhtar", "37.836400", "68.780300", 13),
    ("Куляб", "kulob", "37.914600", "69.784500", 13),
    ("Истаравшан", "istaravshan", "39.914200", "69.003300", 13),
    ("Турсунзаде", "tursunzoda", "38.510800", "68.230300", 13),
    ("Пенджикент", "panjakent", "39.495200", "67.609300", 13),
    ("Хорог", "khorugh", "37.491700", "71.555800", 13),
)


def seed_cities(apps, schema_editor):
    City = apps.get_model("listings", "City")
    for name, slug, latitude, longitude, map_zoom in CITIES:
        City.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "latitude": Decimal(latitude),
                "longitude": Decimal(longitude),
                "map_zoom": map_zoom,
                "is_active": True,
            },
        )


def remove_seeded_cities(apps, schema_editor):
    City = apps.get_model("listings", "City")
    City.objects.filter(slug__in=[city[1] for city in CITIES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_cities, remove_seeded_cities),
    ]
