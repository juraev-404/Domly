from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import EmailCodeAttempt, RegistrationAttempt


class Command(BaseCommand):
    help = "Удаляет устаревшие попытки подтверждения email и сброса пароля."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=1)
        registration_count, _ = RegistrationAttempt.objects.filter(
            expires_at__lt=cutoff,
        ).delete()
        email_code_count, _ = EmailCodeAttempt.objects.filter(
            expires_at__lt=cutoff,
        ).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Удалено попыток регистрации: {registration_count}; "
                f"email-кодов: {email_code_count}."
            )
        )
