from django.urls import path

from . import views

urlpatterns = [
    path("auth/login/", views.login_view, name="login"),
    path("auth/register/", views.register_view, name="register"),
    path("auth/legal-acceptance/", views.legal_acceptance_view, name="legal_acceptance"),
    path("auth/verify/", views.verify_view, name="verify"),
    path("auth/verify/resend/", views.resend_registration_code, name="resend_registration_code"),
    path("auth/password-reset/", views.password_reset_request, name="password_reset"),
    path("auth/password-reset/verify/", views.password_reset_verify, name="password_reset_verify"),
    path("auth/password-reset/resend/", views.resend_password_reset_code, name="resend_password_reset_code"),
    path("auth/password-reset/new/", views.password_reset_new, name="password_reset_new"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/delete/", views.delete_account_view, name="delete_account"),
    path("profile/email/change/", views.email_change_request, name="email_change_request"),
    path("profile/email/verify/", views.email_change_verify, name="email_change_verify"),
    path("profile/email/resend/", views.resend_email_change_code, name="resend_email_change_code"),
    path("profile/listings/", views.profile_listings_view, name="profile_listings"),
    path("profile/drafts/", views.profile_drafts_view, name="profile_drafts"),
    path("author/<str:username>/", views.public_profile_view, name="public_profile"),
    path("favorites/", views.favorites_view, name="favorites"),
    path("notifications/", views.notifications_view, name="notifications"),
    path("notifications/read-all/", views.mark_all_notifications_read, name="mark_all_notifications_read"),
    path("notifications/<uuid:public_id>/read/", views.mark_notification_read, name="mark_notification_read"),
]
