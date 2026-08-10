from django.urls import path

from . import views

urlpatterns = [
    path("auth/login/", views.login_view, name="login"),
    path("auth/register/", views.register_view, name="register"),
    path("auth/verify/", views.verify_view, name="verify"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("profile/", views.profile_view, name="profile"),
    path("profile/listings/", views.profile_listings_view, name="profile_listings"),
    path("profile/drafts/", views.profile_drafts_view, name="profile_drafts"),
    path("author/<str:username>/", views.public_profile_view, name="public_profile"),
    path("favorites/", views.favorites_view, name="favorites"),
]
