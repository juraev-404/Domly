from uuid import uuid4
from pathlib import Path

from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver


def message_attachment_upload_to(instance, filename):
    suffix = Path(filename).suffix.lower()
    return (
        f"chat/{instance.message.conversation.public_id}/"
        f"{instance.message.public_id}/{uuid4().hex}{suffix}"
    )


class Conversation(models.Model):
    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    conversation_key = models.CharField(
        max_length=96,
        blank=True,
        null=True,
        unique=True,
        editable=False,
        help_text="Канонический ключ пары участников и объявления.",
    )
    listing = models.ForeignKey(
        "listings.Listing",
        on_delete=models.SET_NULL,
        related_name="conversations",
        blank=True,
        null=True,
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    last_message_at = models.DateTimeField(blank=True, null=True, db_index=True)

    class Meta:
        ordering = ("-updated_at",)
        verbose_name = "диалог"
        verbose_name_plural = "диалоги"

    def __str__(self):
        return f"Диалог {self.pk}"


class ConversationUserState(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="user_states",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_states",
    )
    cleared_at = models.DateTimeField(blank=True, null=True)
    is_hidden = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "состояние диалога пользователя"
        verbose_name_plural = "состояния диалогов пользователей"
        constraints = [
            models.UniqueConstraint(
                fields=("conversation", "user"),
                name="chat_state_conversation_user_unique",
            )
        ]
        indexes = [
            models.Index(
                fields=("user", "is_hidden"),
                name="chat_state_user_hidden_idx",
            )
        ]

    def __str__(self):
        return f"{self.user}: диалог {self.conversation_id}"


class Message(models.Model):
    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    client_id = models.UUIDField(
        blank=True,
        null=True,
        help_text="Идентификатор клиента для защиты от повторной отправки.",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sent_messages",
    )
    body = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("created_at",)
        verbose_name = "сообщение"
        verbose_name_plural = "сообщения"
        indexes = [
            models.Index(
                fields=("conversation", "read_at"),
                name="message_conversation_read_idx",
            ),
            models.Index(fields=("sender", "read_at"), name="message_sender_read_idx"),
            models.Index(
                fields=("conversation", "created_at"),
                name="message_conversation_time_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("conversation", "client_id"),
                condition=Q(client_id__isnull=False),
                name="message_conv_client_unique",
            )
        ]

    def __str__(self):
        return f"{self.sender}: {self.body[:40]}"


class MessageAttachment(models.Model):
    public_id = models.UUIDField(default=uuid4, editable=False, unique=True)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    image = models.ImageField(upload_to=message_attachment_upload_to)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
        verbose_name = "фотография сообщения"
        verbose_name_plural = "фотографии сообщений"

    def __str__(self):
        return self.original_name


@receiver(post_delete, sender=MessageAttachment)
def delete_attachment_file(sender, instance, **kwargs):
    if instance.image:
        storage = instance.image.storage
        name = instance.image.name
        transaction.on_commit(lambda: storage.delete(name))
