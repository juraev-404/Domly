from io import BytesIO
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from listings.models import City, Listing
from users.models import User
from PIL import Image

from .models import Conversation, ConversationUserState, Message, MessageAttachment


class MessageFoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="message_user", phone="+992900001140", email="message-user@example.com", password="SafePassword-934"
        )
        cls.other = User.objects.create_user(
            username="message_other", phone="+992900001141", email="message-other@example.com", password="SafePassword-934"
        )
        cls.conversation = Conversation.objects.create()
        cls.conversation.participants.add(cls.user, cls.other)
        Message.objects.create(
            conversation=cls.conversation,
            sender=cls.other,
            body="Непрочитанное сообщение",
        )
        Message.objects.create(
            conversation=cls.conversation,
            sender=cls.other,
            body="Уже прочитано",
            read_at=timezone.now(),
        )
        Message.objects.create(
            conversation=cls.conversation,
            sender=cls.user,
            body="Собственное сообщение",
        )

    def test_message_page_requires_login(self):
        response = self.client.get(reverse("messages"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('messages')}")

    def test_header_counts_only_unread_messages_from_other_users(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("messages"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "непрочитанных: 1")
        self.assertEqual(response.context["unread_message_count"], 1)


class WorkingChatTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="chat_owner",
            phone="+992900001150",
            email="chat-owner@example.com",
            password="SafePassword-934",
        )
        cls.buyer = User.objects.create_user(
            username="chat_buyer",
            phone="+992900001151",
            email="chat-buyer@example.com",
            password="SafePassword-934",
        )
        cls.outsider = User.objects.create_user(
            username="chat_outsider",
            phone="+992900001152",
            email="chat-outsider@example.com",
            password="SafePassword-934",
        )
        cls.listing = Listing.objects.create(
            owner=cls.owner,
            city=City.objects.get(slug="dushanbe"),
            deal_type=Listing.DealType.RENT,
            property_type=Listing.PropertyType.APARTMENT,
            status=Listing.Status.PUBLISHED,
            title="Квартира для диалога",
            description="Подробное описание квартиры для проверки рабочего чата.",
            price="2500.00",
            address="Улица Айни, 15",
        )

    def start_chat(self):
        self.client.force_login(self.buyer)
        return self.client.post(
            reverse("start_conversation", args=(self.listing.public_id,))
        )

    def make_image(self, name="chat-photo.png", size=(2, 2)):
        content = BytesIO()
        Image.new("RGB", size, color="white").save(content, format="PNG")
        return SimpleUploadedFile(
            name,
            content.getvalue(),
            content_type="image/png",
        )

    def test_start_chat_requires_login_and_post(self):
        url = reverse("start_conversation", args=(self.listing.public_id,))

        anonymous_response = self.client.post(url)
        self.assertRedirects(anonymous_response, f"{reverse('login')}?next={url}")

        self.client.force_login(self.buyer)
        get_response = self.client.get(url)

        self.assertEqual(get_response.status_code, 405)

    def test_start_chat_is_unique_for_buyer_owner_and_listing(self):
        first_response = self.start_chat()
        second_response = self.client.post(
            reverse("start_conversation", args=(self.listing.public_id,))
        )

        self.assertEqual(Conversation.objects.count(), 1)
        conversation = Conversation.objects.get()
        self.assertRedirects(
            first_response,
            reverse("conversation_detail", args=(conversation.public_id,)),
        )
        self.assertRedirects(
            second_response,
            reverse("conversation_detail", args=(conversation.public_id,)),
        )
        self.assertSetEqual(
            set(conversation.participants.values_list("pk", flat=True)),
            {self.owner.pk, self.buyer.pk},
        )

    def test_owner_cannot_start_chat_with_self(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("start_conversation", args=(self.listing.public_id,))
        )

        self.assertRedirects(
            response,
            reverse("listing_detail", args=(self.listing.public_id,)),
        )
        self.assertFalse(Conversation.objects.exists())

    def test_conversation_keeps_messages_inside_scrollable_window(self):
        self.start_chat()
        conversation = Conversation.objects.get()

        response = self.client.get(
            reverse("conversation_detail", args=(conversation.public_id,))
        )

        self.assertContains(response, "h-[calc(100dvh-9.25rem)]")
        self.assertContains(response, "md:h-[650px]")
        self.assertContains(response, "flex min-h-0 flex-1")
        self.assertContains(response, "shrink-0 border-t")
        self.assertContains(response, "data-delete-dialog")
        self.assertContains(response, "chat/delete_dialog.js")
        self.assertNotContains(response, "return confirm(")

    def test_only_participants_can_open_conversation(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        self.client.force_login(self.outsider)

        response = self.client.get(
            reverse("conversation_detail", args=(conversation.public_id,))
        )

        self.assertEqual(response.status_code, 404)

    def test_ajax_send_is_trimmed_and_updates_conversation(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        client_id = uuid4()
        url = reverse("conversation_detail", args=(conversation.public_id,))

        response = self.client.post(
            url,
            {"body": "  Добрый день!  ", "client_id": str(client_id)},
            HTTP_ACCEPT="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["message"]["body"], "Добрый день!")
        message = Message.objects.get()
        self.assertEqual(message.client_id, client_id)
        conversation.refresh_from_db()
        self.assertEqual(conversation.last_message_at, message.created_at)

    def test_client_id_makes_retried_send_idempotent(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        url = reverse("conversation_detail", args=(conversation.public_id,))
        payload = {"body": "Одно сообщение", "client_id": str(uuid4())}

        first_response = self.client.post(
            url, payload, HTTP_ACCEPT="application/json"
        )
        second_response = self.client.post(
            url, payload, HTTP_ACCEPT="application/json"
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(
            first_response.json()["message"]["public_id"],
            second_response.json()["message"]["public_id"],
        )

    def test_opening_chat_marks_only_incoming_messages_read(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        incoming = Message.objects.create(
            conversation=conversation,
            sender=self.owner,
            body="Ответ владельца",
        )
        outgoing = Message.objects.create(
            conversation=conversation,
            sender=self.buyer,
            body="Сообщение покупателя",
        )

        response = self.client.get(
            reverse("conversation_detail", args=(conversation.public_id,))
        )

        self.assertEqual(response.status_code, 200)
        incoming.refresh_from_db()
        outgoing.refresh_from_db()
        self.assertIsNotNone(incoming.read_at)
        self.assertIsNone(outgoing.read_at)
        self.assertContains(response, "Ответ владельца")
        self.assertContains(response, "chat/chat.js")

    def test_events_use_cursor_and_are_private(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        first = Message.objects.create(
            conversation=conversation,
            sender=self.owner,
            body="Первое",
        )
        second = Message.objects.create(
            conversation=conversation,
            sender=self.owner,
            body="Второе",
        )
        events_url = reverse("conversation_events", args=(conversation.public_id,))

        response = self.client.get(events_url, {"after": first.pk})
        self.client.force_login(self.outsider)
        outsider_response = self.client.get(events_url, {"after": 0})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [message["body"] for message in response.json()["messages"]],
            [second.body],
        )
        self.assertEqual(response.json()["next_cursor"], second.pk)
        self.assertEqual(outsider_response.status_code, 404)

    def test_conversation_list_shows_counterparty_listing_and_unread_count(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        Message.objects.create(
            conversation=conversation,
            sender=self.owner,
            body="Когда удобно посмотреть?",
        )

        response = self.client.get(reverse("messages"))

        self.assertContains(response, self.owner.username)
        self.assertContains(response, self.listing.title)
        self.assertContains(response, "Когда удобно посмотреть?")
        self.assertEqual(response.context["conversations"][0].unread_count, 1)
        self.assertContains(response, "data-delete-dialog")
        self.assertContains(response, "chat/delete_dialog.js")
        self.assertNotContains(response, "return confirm(")

    def test_delete_conversation_is_post_only_and_private(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        delete_url = reverse(
            "delete_conversation",
            args=(conversation.public_id,),
        )

        get_response = self.client.get(delete_url)
        self.client.force_login(self.outsider)
        outsider_response = self.client.post(delete_url)

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(outsider_response.status_code, 404)
        self.assertFalse(ConversationUserState.objects.exists())

    def test_delete_conversation_hides_it_only_for_current_user(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        old_message = Message.objects.create(
            conversation=conversation,
            sender=self.owner,
            body="Старая переписка",
        )

        response = self.client.post(
            reverse("delete_conversation", args=(conversation.public_id,))
        )

        self.assertRedirects(response, reverse("messages"))
        self.assertTrue(Conversation.objects.filter(pk=conversation.pk).exists())
        self.assertTrue(Message.objects.filter(pk=old_message.pk).exists())
        state = ConversationUserState.objects.get(
            conversation=conversation,
            user=self.buyer,
        )
        self.assertTrue(state.is_hidden)
        self.assertIsNotNone(state.cleared_at)
        buyer_list = self.client.get(reverse("messages"))
        buyer_detail = self.client.get(
            reverse("conversation_detail", args=(conversation.public_id,))
        )
        self.assertNotContains(buyer_list, "Старая переписка")
        self.assertNotContains(buyer_detail, "Старая переписка")

        self.client.force_login(self.owner)
        owner_list = self.client.get(reverse("messages"))
        owner_detail = self.client.get(
            reverse("conversation_detail", args=(conversation.public_id,))
        )
        self.assertContains(owner_list, "Старая переписка")
        self.assertContains(owner_detail, "Старая переписка")

    def test_new_message_restores_deleted_chat_without_old_history(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        Message.objects.create(
            conversation=conversation,
            sender=self.owner,
            body="Удалённая история",
        )
        self.client.post(
            reverse("delete_conversation", args=(conversation.public_id,))
        )

        self.client.force_login(self.owner)
        send_response = self.client.post(
            reverse("conversation_detail", args=(conversation.public_id,)),
            {"body": "Новое сообщение после удаления"},
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(send_response.status_code, 201)

        self.client.force_login(self.buyer)
        buyer_list = self.client.get(reverse("messages"))
        buyer_detail = self.client.get(
            reverse("conversation_detail", args=(conversation.public_id,))
        )
        self.assertContains(buyer_list, "Новое сообщение после удаления")
        self.assertContains(buyer_detail, "Новое сообщение после удаления")
        self.assertNotContains(buyer_detail, "Удалённая история")
        self.assertFalse(
            ConversationUserState.objects.get(
                conversation=conversation,
                user=self.buyer,
            ).is_hidden
        )

    def test_image_only_message_is_saved_and_returned_in_json(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        url = reverse("conversation_detail", args=(conversation.public_id,))

        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                url,
                {
                    "body": "",
                    "client_id": str(uuid4()),
                    "images": [self.make_image()],
                },
                HTTP_ACCEPT="application/json",
            )

            self.assertEqual(response.status_code, 201)
            self.assertEqual(Message.objects.count(), 1)
            self.assertEqual(MessageAttachment.objects.count(), 1)
            attachment_data = response.json()["message"]["attachments"][0]
            self.assertEqual(attachment_data["content_type"], "image/png")
            self.assertTrue(attachment_data["url"].endswith(".png"))

    def test_non_image_attachment_is_rejected(self):
        self.start_chat()
        conversation = Conversation.objects.get()

        response = self.client.post(
            reverse("conversation_detail", args=(conversation.public_id,)),
            {
                "body": "",
                "images": [
                    SimpleUploadedFile(
                        "malware.txt",
                        b"not an image",
                        content_type="text/plain",
                    )
                ],
            },
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Message.objects.exists())
        self.assertFalse(MessageAttachment.objects.exists())

    def test_more_than_five_images_are_rejected(self):
        self.start_chat()
        conversation = Conversation.objects.get()

        response = self.client.post(
            reverse("conversation_detail", args=(conversation.public_id,)),
            {
                "body": "Фотографии",
                "images": [self.make_image(f"photo-{index}.png") for index in range(6)],
            },
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Message.objects.exists())

    def test_retried_image_message_does_not_duplicate_attachment(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        url = reverse("conversation_detail", args=(conversation.public_id,))
        client_id = str(uuid4())

        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            first_response = self.client.post(
                url,
                {
                    "body": "Фото кухни",
                    "client_id": client_id,
                    "images": [self.make_image("kitchen.png")],
                },
                HTTP_ACCEPT="application/json",
            )
            second_response = self.client.post(
                url,
                {
                    "body": "Фото кухни",
                    "client_id": client_id,
                    "images": [self.make_image("kitchen-retry.png")],
                },
                HTTP_ACCEPT="application/json",
            )

            self.assertEqual(first_response.status_code, 201)
            self.assertEqual(second_response.status_code, 201)
            self.assertEqual(Message.objects.count(), 1)
            self.assertEqual(MessageAttachment.objects.count(), 1)

    def test_uploaded_image_is_rendered_in_conversation(self):
        self.start_chat()
        conversation = Conversation.objects.get()
        url = reverse("conversation_detail", args=(conversation.public_id,))

        with TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            self.client.post(
                url,
                {"body": "Планировка", "images": [self.make_image("plan.png")]},
            )
            attachment = MessageAttachment.objects.get()
            response = self.client.get(url)

            self.assertContains(response, attachment.image.url)
            self.assertContains(response, "Планировка")
            self.assertContains(response, "data-chat-image")
            self.assertContains(response, "data-chat-lightbox")
            self.assertNotContains(response, 'target="_blank"')
