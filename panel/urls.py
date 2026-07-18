from django.contrib import admin
from django.urls import path

from panel.ui import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.session_list, name="session_list"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("sessions/<uuid:sid>/", views.session_detail, name="session_detail"),
    path("sessions/<uuid:sid>/stop/", views.session_stop, name="session_stop"),
    path("projects/<slug:slug>/start/", views.session_start, name="session_start"),
    path("permissions/", views.permission_queue, name="permission_queue"),
    path(
        "permissions/<uuid:request_id>/resolve/",
        views.permission_resolve,
        name="permission_resolve",
    ),
]
