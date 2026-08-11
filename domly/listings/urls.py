from django.urls import path
from . import views

urlpatterns = [
    path('', views.listing_list, name='listing_list'),
    path('listing/<uuid:public_id>/', views.listing_detail, name='listing_detail'),
    path('listing/<uuid:public_id>/edit/', views.edit_listing, name='edit_listing'),
    path('listing/<uuid:public_id>/favorite/', views.toggle_favorite, name='toggle_favorite'),
    path('listing/<uuid:public_id>/report/', views.report_listing, name='report_listing'),
    path('listing/<uuid:public_id>/archive/', views.archive_listing, name='archive_listing'),
    path('listing/<uuid:public_id>/restore/', views.restore_listing, name='restore_listing'),
    path('listing/<uuid:public_id>/complete/', views.complete_listing, name='complete_listing'),
    path('listing/<uuid:public_id>/delete/', views.delete_listing, name='delete_listing'),
    path('create/', views.create_listing, name='create'),
    path('help/', views.help, name='help'),
    path('moderation/', views.moderation, name='moderation'),
    path('reports/', views.listing_reports, name='listing_reports'),
    path('reports/<uuid:public_id>/review/', views.review_listing_report, name='review_listing_report'),
    path('moderation/blocks/', views.moderation_blocks, name='moderation_blocks'),
    path('moderation/listing/<uuid:public_id>/block/', views.block_listing, name='block_listing'),
    path('moderation/listing-block/<uuid:public_id>/release/', views.unblock_listing, name='unblock_listing'),
    path('moderation/user/<str:username>/block/', views.block_user, name='block_user'),
    path('moderation/user-block/<uuid:public_id>/release/', views.unblock_user, name='unblock_user'),
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
    path('location/geocode/', views.geocode_location, name='geocode_location'),
    path('location/set/', views.set_city, name='set_city'),
]
