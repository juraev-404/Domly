from django.db import migrations, models

import listings.models


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0008_listing_contact_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="listingimage",
            name="thumbnail",
            field=models.ImageField(
                blank=True,
                upload_to=listings.models.listing_thumbnail_upload_to,
            ),
        ),
    ]
