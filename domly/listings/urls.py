from django.urls import path
from . import views

urlpatterns = [
    path('', views.listing_list, name='listing_list'),
    path('listing/<uuid:public_id>/', views.listing_detail, name='listing_detail'),
    path('listing/<uuid:public_id>/edit/', views.edit_listing, name='edit_listing'),
    path('listing/<uuid:public_id>/favorite/', views.toggle_favorite, name='toggle_favorite'),
    path('create/', views.create_listing, name='create'),
    path('help/', views.help, name='help'),
    path('moderation/', views.moderation, name='moderation'),
    path(
        'moderation/<uuid:public_id>/approve/',
        views.moderation_approve,
        name='moderation_approve',
    ),
    path(
        'moderation/<uuid:public_id>/reject/',
        views.moderation_reject,
        name='moderation_reject',
    ),
    path('map/', views.city_map, name='city_map'),
    path('location/set/', views.set_city, name='set_city'),
]
