from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from users.models import Notification, UserBlock

from .models import Listing, ListingBlock


def block_expiry(duration):
    if duration == "permanent":
        return None
    return timezone.now() + timedelta(days=int(duration))


def release_listing_block(block, *, actor=None, note="Срок блокировки истёк."):
    with transaction.atomic():
        block = ListingBlock.objects.select_for_update().select_related("listing").get(pk=block.pk)
        if block.unblocked_at is not None:
            return block, False
        listing = block.listing
        if listing.status == Listing.Status.BLOCKED:
            listing.status = block.previous_status
            listing.save(update_fields=("status", "updated_at"))
        block.unblocked_at = timezone.now()
        block.unblocked_by = actor
        block.unblock_note = note
        block.save(update_fields=("unblocked_at", "unblocked_by", "unblock_note"))
        Notification.objects.create(
            user=listing.owner,
            listing=listing,
            kind=Notification.Kind.LISTING_UNBLOCKED,
            message=note,
        )
    return block, True


def release_expired_listing_blocks():
    expired = ListingBlock.objects.filter(
        unblocked_at__isnull=True,
        expires_at__isnull=False,
        expires_at__lte=timezone.now(),
    ).only("pk")
    for block in expired.iterator():
        release_listing_block(block)
