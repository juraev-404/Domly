from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class MultiIdentifierBackend(ModelBackend):
    """Authenticate by username or verified email plus password."""

    def authenticate(self, request, username=None, password=None, identifier=None, **kwargs):
        if password is None:
            return None

        identifier = (identifier or username or "").strip()
        if not identifier:
            return None

        query = Q(username__iexact=identifier) | Q(
            email__iexact=identifier,
            is_email_verified=True,
        )
        User = get_user_model()
        users = list(User.objects.filter(query).distinct()[:2])
        if len(users) != 1:
            User().set_password(password)
            return None

        user = users[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
