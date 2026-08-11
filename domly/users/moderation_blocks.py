from django.db import transaction
from django.utils import timezone

from .models import Notification, User, UserBlock


def release_user_block(block, *, actor=None, note="Срок блокировки истёк."):
    with transaction.atomic():
        block = UserBlock.objects.select_for_update().select_related("user").get(pk=block.pk)
        if block.unblocked_at is not None:
            return block, False
        user = block.user
        if user.is_active != block.was_active:
            user.is_active = block.was_active
            user.save(update_fields=("is_active",))
        block.unblocked_at = timezone.now()
        block.unblocked_by = actor
        block.unblock_note = note
        block.save(update_fields=("unblocked_at", "unblocked_by", "unblock_note"))
        Notification.objects.create(
            user=user,
            kind=Notification.Kind.ACCOUNT_UNBLOCKED,
            message=note,
        )
    return block, True


def release_expired_user_blocks():
    expired = UserBlock.objects.filter(
        unblocked_at__isnull=True,
        expires_at__isnull=False,
        expires_at__lte=timezone.now(),
    ).only("pk")
    for block in expired.iterator():
        release_user_block(block)
