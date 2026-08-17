from django.db import transaction
from django.db.models import Q

from chat.models import Conversation

from .models import RegistrationAttempt


@transaction.atomic
def delete_user_account(user):
    """Delete a user and active personal data that belongs to the account."""
    Conversation.objects.filter(participants=user).delete()

    pending_registration = Q(username__iexact=user.username)
    if user.email:
        pending_registration |= Q(email__iexact=user.email)
    RegistrationAttempt.objects.filter(pending_registration).delete()

    user.delete()
