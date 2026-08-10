from .models import Message


def unread_messages(request):
    if not request.user.is_authenticated:
        return {"unread_message_count": 0}

    count = (
        Message.objects.filter(
            conversation__participants=request.user,
            read_at__isnull=True,
        )
        .exclude(sender=request.user)
        .distinct()
        .count()
    )
    return {"unread_message_count": count}
