from django.urls import path

from . import views

app_name = "requests_app"
urlpatterns = [
    path("", views.home, name="home"),
    path("requests/", views.my_requests, name="my_requests"),
    path("requests/new/", views.upload_request, name="upload"),
    path("requests/<uuid:pk>/edit/", views.edit_request, name="edit"),
    path("requests/<uuid:pk>/", views.request_detail, name="detail"),
    path("requests/<uuid:pk>/document/", views.request_document, name="document"),
    path("procurement/", views.procurement_list, name="procurement_list"),
    path("procurement/<uuid:pk>/", views.procurement_detail, name="procurement_detail"),
]
