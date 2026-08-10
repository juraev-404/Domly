from django.urls import path

from . import views


urlpatterns = [
    path("messages/", views.message_list, name="messages"),
    path(
        "messages/start/<uuid:public_id>/",
        views.start_conversation,
        name="start_conversation",
    ),
    path(
        "messages/<uuid:public_id>/",
        views.conversation_detail,
        name="conversation_detail",
    ),
    path(
        "messages/<uuid:public_id>/events/",
        views.conversation_events,
        name="conversation_events",
    ),
    path(
        "messages/<uuid:public_id>/delete/",
        views.delete_conversation,
        name="delete_conversation",
    ),
]
