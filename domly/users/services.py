import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.utils.translation import gettext as _


def generate_verification_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def send_email_code(*, email, code, purpose):
    subjects = {
        "registration": _("Код регистрации в Domly"),
        "password_reset": _("Код восстановления пароля Domly"),
        "email_change": _("Код подтверждения нового email в Domly"),
    }
    introductions = {
        "registration": _("Для завершения регистрации в Domly введите код:"),
        "password_reset": _("Для восстановления пароля Domly введите код:"),
        "email_change": _("Для подтверждения нового адреса email введите код:"),
    }
    if purpose not in subjects:
        raise ValueError(_("Неизвестное назначение кода."))

    send_mail(
        subject=subjects[purpose],
        message=(
            f"{introductions[purpose]}\n\n"
            f"{code}\n\n"
            + _(
                "Код действует 10 минут. Если вы не запрашивали его, "
                "просто проигнорируйте это письмо."
            )
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
