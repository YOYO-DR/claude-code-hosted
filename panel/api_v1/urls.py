"""URLs del API v1."""

from django.urls import path

from . import auth, github, mcps, permissions, projects, sessions

urlpatterns = [
    # Auth
    path("me/", auth.me, name="api_v1_me"),
    path("login/", auth.login_view, name="api_v1_login"),
    path("logout/", auth.logout_view, name="api_v1_logout"),
    # Sessions
    path("sessions/", sessions.list_sessions, name="api_v1_sessions"),
    path("sessions/<uuid:sid>/", sessions.session_detail, name="api_v1_session_detail"),
    path("sessions/<uuid:sid>/message/", sessions.session_message, name="api_v1_session_message"),
    path("sessions/<uuid:sid>/stop/", sessions.session_stop, name="api_v1_session_stop"),
    path("sessions/<uuid:sid>/events/", sessions.session_events, name="api_v1_session_events"),
    # Projects (+ tree/file/diff FASE C.5)
    path("projects/", projects.list_projects, name="api_v1_projects"),
    path("projects/<slug:slug>/", projects.project_detail, name="api_v1_project_detail"),
    path("projects/<slug:slug>/tree/", projects.project_tree, name="api_v1_project_tree"),
    path("projects/<slug:slug>/file/", projects.project_file, name="api_v1_project_file"),
    path("projects/<slug:slug>/diff/", projects.project_diff, name="api_v1_project_diff"),
    # MCPs
    path("mcps/", mcps.list_mcps, name="api_v1_mcps"),
    # GitHub
    path("github/", github.github_info, name="api_v1_github_info"),
    # Permissions
    path("permissions/", permissions.list_permissions, name="api_v1_permissions"),
    path(
        "permissions/<uuid:perm_id>/resolve/",
        permissions.resolve_permission,
        name="api_v1_permission_resolve",
    ),
]