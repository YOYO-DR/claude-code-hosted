"""URLs del API v1."""

from django.urls import path

from . import auth, github, mcps, models, permissions, projects, sessions

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
    path("sessions/<uuid:sid>/restart/", sessions.session_restart, name="api_v1_session_restart"),
    path("sessions/<uuid:sid>/events/", sessions.session_events, name="api_v1_session_events"),
    path("sessions/create/", sessions.session_create, name="api_v1_session_create"),
    # Projects (+ tree/file/diff FASE C.5)
    path("projects/", projects.list_projects, name="api_v1_projects"),
    path("projects/form-options/", projects.project_form_options, name="api_v1_project_form_options"),
    path("projects/create/", projects.project_create, name="api_v1_project_create"),
    path("projects/<slug:slug>/", projects.project_detail, name="api_v1_project_detail"),
    path("projects/<slug:slug>/update/", projects.project_update, name="api_v1_project_update"),
    path("projects/<slug:slug>/delete/", projects.project_delete, name="api_v1_project_delete"),
    path("projects/<slug:slug>/recreate/", projects.project_recreate, name="api_v1_project_recreate"),
    path("projects/<slug:slug>/tree/", projects.project_tree, name="api_v1_project_tree"),
    path("projects/<slug:slug>/file/", projects.project_file, name="api_v1_project_file"),
    path("projects/<slug:slug>/raw/", projects.project_raw, name="api_v1_project_raw"),
    path("projects/<slug:slug>/diff/", projects.project_diff, name="api_v1_project_diff"),
    path("projects/<slug:slug>/diff/files/", projects.project_diff_files, name="api_v1_project_diff_files"),
    path("projects/<slug:slug>/diff/file/", projects.project_diff_file, name="api_v1_project_diff_file"),
    path("projects/<slug:slug>/git/", projects.project_git, name="api_v1_project_git"),
    path("projects/<slug:slug>/model/", models.set_project_model, name="api_v1_set_project_model"),
    # MCPs
    path("mcps/", mcps.list_mcps, name="api_v1_mcps"),
    path("mcps/create/", mcps.create_mcp, name="api_v1_mcp_create"),
    path("mcps/<int:mcp_id>/update/", mcps.update_mcp, name="api_v1_mcp_update"),
    path("mcps/<int:mcp_id>/delete/", mcps.delete_mcp, name="api_v1_mcp_delete"),
    # GitHub
    path("github/", github.github_info, name="api_v1_github_info"),
    # ModelProfiles (FASE D)
    path("models/", models.list_models, name="api_v1_models"),
    path("models/create/", models.create_model, name="api_v1_model_create"),
    path("models/<int:pk>/update/", models.update_model, name="api_v1_model_update"),
    path("models/<int:pk>/delete/", models.delete_model, name="api_v1_model_delete"),
    path("models/<int:pk>/test/", models.test_model, name="api_v1_model_test"),
    # Permissions
    path("permissions/", permissions.list_permissions, name="api_v1_permissions"),
    path(
        "permissions/<uuid:perm_id>/resolve/",
        permissions.resolve_permission,
        name="api_v1_permission_resolve",
    ),
]