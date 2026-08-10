from django.contrib import admin

from .models import Conversation, Message, MessageAttachment


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("created_at", "read_at")


class MessageAttachmentInline(admin.TabularInline):
    model = MessageAttachment
    extra = 0
    readonly_fields = ("public_id", "original_name", "content_type", "size", "created_at")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "public_id",
        "listing",
        "created_at",
        "last_message_at",
    )
    readonly_fields = ("public_id", "conversation_key", "last_message_at")
    filter_horizontal = ("participants",)
    inlines = (MessageInline,)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("public_id", "conversation", "sender", "created_at", "read_at")
    readonly_fields = ("public_id", "created_at", "read_at")
    list_filter = ("read_at",)
    search_fields = ("sender__username", "body")
    list_select_related = ("conversation", "sender")
    inlines = (MessageAttachmentInline,)


@admin.register(MessageAttachment)
class MessageAttachmentAdmin(admin.ModelAdmin):
    list_display = ("public_id", "message", "original_name", "size", "created_at")
    readonly_fields = ("public_id", "original_name", "content_type", "size", "created_at")
    list_select_related = ("message",)
