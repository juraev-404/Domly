from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import ValidationError
from django.db.models import Q

from .forms import normalize_phone


class MultiIdentifierBackend(ModelBackend):
    """Authenticate by username, phone number, or email plus password."""

    def authenticate(self, request, username=None, password=None, identifier=None, **kwargs):
        if password is None:
            return None

        identifier = (identifier or username or "").strip()
        if not identifier:
            return None

        query = Q(username__iexact=identifier) | Q(email__iexact=identifier)
        try:
            normalized_phone = normalize_phone(identifier)
        except ValidationError:
            normalized_phone = None
        if normalized_phone:
            query |= Q(phone=normalized_phone)
        User = get_user_model()
        users = list(User.objects.filter(query).distinct()[:2])
        if len(users) != 1:
            User().set_password(password)
            return None

        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
