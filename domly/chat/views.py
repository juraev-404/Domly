from django.contrib import messages as flash_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.utils.translation import gettext as _

from listings.models import Listing

from .forms import MessageForm
from .models import Conversation, ConversationUserState, Message
from .services import (
    delete_conversation_for_user,
    get_or_create_listing_conversation,
    mark_conversation_read,
    send_message,
    visible_messages_for_user,
)


def _wants_json(request):
    return (
        request.headers.get("Accept") == "application/json"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def _avatar_url(request, user):
    if not user.avatar:
        return ""
    return request.build_absolute_uri(user.avatar.url)


def _serialize_message(request, message):
    return {
        "id": message.pk,
        "public_id": str(message.public_id),
        "body": message.body,
        "created_at": message.created_at.isoformat(),
        "created_label": message.created_at.strftime("%H:%M"),
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "is_mine": message.sender_id == request.user.pk,
        "sender": {
            "username": message.sender.username,
            "avatar_url": _avatar_url(request, message.sender),
        },
        "attachments": [
            {
                "public_id": str(attachment.public_id),
                "url": request.build_absolute_uri(attachment.image.url),
                "name": attachment.original_name,
                "content_type": attachment.content_type,
                "size": attachment.size,
            }
            for attachment in message.attachments.all()
        ],
    }


def _conversation_for_user(request, public_id):
    return get_object_or_404(
        Conversation.objects.select_related("listing", "listing__owner").prefetch_related(
            "participants"
        ),
        public_id=public_id,
        participants=request.user,
    )


def _other_participant(conversation, user):
    return next(
        (participant for participant in conversation.participants.all() if participant.pk != user.pk),
        None,
    )


@login_required
def message_list(request):
    last_message = Message.objects.filter(conversation=OuterRef("pk")).order_by(
        "-created_at", "-pk"
    ).annotate(attachment_count=Count("attachments"))
    hidden_for_user = ConversationUserState.objects.filter(
        conversation_id=OuterRef("pk"),
        user=request.user,
        is_hidden=True,
    )
    conversations = list(
        request.user.conversations.select_related("listing", "listing__city")
        .prefetch_related("participants", "listing__images")
        .annotate(
            hidden_for_user=Exists(hidden_for_user),
            unread_count=Count(
                "messages",
                filter=Q(messages__read_at__isnull=True)
                & ~Q(messages__sender=request.user),
                distinct=True,
            ),
            last_message_body=Subquery(last_message.values("body")[:1]),
            last_message_created_at=Subquery(last_message.values("created_at")[:1]),
            last_message_attachment_count=Subquery(
                last_message.values("attachment_count")[:1]
            ),
        )
        .filter(hidden_for_user=False)
        .order_by(F("last_message_at").desc(nulls_last=True), "-created_at")
    )
    for conversation in conversations:
        conversation.other_user = _other_participant(conversation, request.user)

    return render(
        request,
        "chat/message_list.html",
        {"conversations": conversations},
    )


@login_required
@require_POST
def start_conversation(request, public_id):
    listing = get_object_or_404(
        Listing.objects.published().select_related("owner"),
        public_id=public_id,
    )
    try:
        conversation, _ = get_or_create_listing_conversation(
            listing=listing,
            buyer=request.user,
        )
    except ValidationError as error:
        flash_messages.error(request, error.messages[0])
        return redirect("listing_detail", public_id=listing.public_id)
    return redirect("conversation_detail", public_id=conversation.public_id)


@login_required
@require_http_methods(["GET", "POST"])
def conversation_detail(request, public_id):
    conversation = _conversation_for_user(request, public_id)
    other_user = _other_participant(conversation, request.user)

    if request.method == "POST":
        form = MessageForm(request.POST, request.FILES)
        if form.is_valid():
            message, _ = send_message(
                conversation=conversation,
                sender=request.user,
                body=form.cleaned_data["body"],
                client_id=form.cleaned_data.get("client_id"),
                images=form.cleaned_data.get("images"),
            )
            if _wants_json(request):
                return JsonResponse(
                    {"message": _serialize_message(request, message)},
                    status=201,
                )
            return redirect("conversation_detail", public_id=conversation.public_id)

        if _wants_json(request):
            return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    else:
        form = MessageForm()

    mark_conversation_read(conversation=conversation, reader=request.user)
    chat_messages = list(
        visible_messages_for_user(
            conversation=conversation,
            user=request.user,
        ).select_related("sender")
        .prefetch_related("attachments")
        .order_by("-created_at", "-pk")[:100]
    )
    chat_messages.reverse()
    return render(
        request,
        "chat/conversation_detail.html",
        {
            "conversation": conversation,
            "chat_messages": chat_messages,
            "message_form": form,
            "other_user": other_user,
        },
    )


@login_required
@require_GET
def conversation_events(request, public_id):
    conversation = _conversation_for_user(request, public_id)
    try:
        after_id = max(int(request.GET.get("after", 0)), 0)
    except (TypeError, ValueError):
        return JsonResponse({"error": _("Некорректный курсор.")}, status=400)

    mark_conversation_read(conversation=conversation, reader=request.user)
    new_messages = list(
        visible_messages_for_user(
            conversation=conversation,
            user=request.user,
        ).filter(pk__gt=after_id)
        .select_related("sender")
        .prefetch_related("attachments")
        .order_by("pk")[:100]
    )
    return JsonResponse(
        {
            "messages": [
                _serialize_message(request, message) for message in new_messages
            ],
            "next_cursor": new_messages[-1].pk if new_messages else after_id,
        }
    )


@login_required
@require_POST
def delete_conversation(request, public_id):
    conversation = _conversation_for_user(request, public_id)
    delete_conversation_for_user(
        conversation=conversation,
        user=request.user,
    )
    if _wants_json(request):
        return JsonResponse({"deleted": True})
    flash_messages.success(request, _("Чат удалён у вас."))
    return redirect("messages")
