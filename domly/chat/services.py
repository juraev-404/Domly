from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from listings.models import Listing

from .models import Conversation, ConversationUserState, Message, MessageAttachment


def conversation_key_for(listing, buyer):
    return f"listing:{listing.pk}:buyer:{buyer.pk}:owner:{listing.owner_id}"


@transaction.atomic
def get_or_create_listing_conversation(*, listing, buyer):
    if listing.status != Listing.Status.PUBLISHED:
        raise ValidationError(_("Начать чат можно только по опубликованному объявлению."))
    if listing.owner_id == buyer.pk:
        raise ValidationError(_("Нельзя начать чат с самим собой."))

    key = conversation_key_for(listing, buyer)
    conversation, created = Conversation.objects.get_or_create(
        conversation_key=key,
        defaults={"listing": listing},
    )

    expected_participant_ids = {buyer.pk, listing.owner_id}
    current_participant_ids = set(
        conversation.participants.values_list("pk", flat=True)
    )
    if created or current_participant_ids != expected_participant_ids:
        conversation.participants.set(expected_participant_ids)
    ConversationUserState.objects.filter(
        conversation=conversation,
        user=buyer,
    ).update(is_hidden=False)
    return conversation, created


def ensure_participant(*, conversation, user):
    if not user.is_authenticated or not conversation.participants.filter(pk=user.pk).exists():
        raise PermissionDenied(_("У вас нет доступа к этому диалогу."))


def visible_messages_for_user(*, conversation, user):
    ensure_participant(conversation=conversation, user=user)
    messages = conversation.messages.all()
    cleared_at = (
        ConversationUserState.objects.filter(
            conversation=conversation,
            user=user,
        )
        .values_list("cleared_at", flat=True)
        .first()
    )
    if cleared_at:
        messages = messages.filter(created_at__gt=cleared_at)
    return messages


@transaction.atomic
def delete_conversation_for_user(*, conversation, user):
    ensure_participant(conversation=conversation, user=user)
    deleted_at = timezone.now()
    state, _ = ConversationUserState.objects.update_or_create(
        conversation=conversation,
        user=user,
        defaults={"cleared_at": deleted_at, "is_hidden": True},
    )
    Message.objects.filter(
        conversation=conversation,
        read_at__isnull=True,
        created_at__lte=deleted_at,
    ).exclude(sender=user).update(read_at=deleted_at)
    return state


@transaction.atomic
def send_message(*, conversation, sender, body, client_id=None, images=()):
    ensure_participant(conversation=conversation, user=sender)
    body = (body or "").strip()
    images = list(images or ())
    if not body and not images:
        raise ValidationError(_("Введите сообщение или прикрепите фотографию."))
    if len(body) > 4000:
        raise ValidationError(_("Сообщение не должно превышать 4000 символов."))

    if client_id:
        message, created = Message.objects.get_or_create(
            conversation=conversation,
            client_id=client_id,
            defaults={"sender": sender, "body": body},
        )
        if not created and (message.sender_id != sender.pk or message.body != body):
            raise ValidationError(_("Идентификатор уже использован другим сообщением."))
    else:
        message = Message.objects.create(
            conversation=conversation,
            sender=sender,
            body=body,
        )
        created = True

    if created:
        for image in images:
            MessageAttachment.objects.create(
                message=message,
                image=image,
                original_name=Path(image.name).name[:255],
                content_type=image.content_type,
                size=image.size,
            )
        Conversation.objects.filter(pk=conversation.pk).update(
            updated_at=message.created_at,
            last_message_at=message.created_at,
        )
        ConversationUserState.objects.filter(
            conversation=conversation,
            is_hidden=True,
        ).update(is_hidden=False)
    return message, created


def mark_conversation_read(*, conversation, reader):
    ensure_participant(conversation=conversation, user=reader)
    return (
        visible_messages_for_user(
            conversation=conversation,
            user=reader,
        ).filter(
            read_at__isnull=True,
        )
        .exclude(sender=reader)
        .update(read_at=timezone.now())
    )
