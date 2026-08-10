from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0003_messageattachment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConversationUserState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("cleared_at", models.DateTimeField(blank=True, null=True)),
                ("is_hidden", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_states",
                        to="chat.conversation",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversation_states",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "состояние диалога пользователя",
                "verbose_name_plural": "состояния диалогов пользователей",
                "indexes": [
                    models.Index(
                        fields=["user", "is_hidden"],
                        name="chat_state_user_hidden_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("conversation", "user"),
                        name="chat_state_conversation_user_unique",
                    )
                ],
            },
        ),
    ]
