from django.contrib import admin

from panel.core import models


@admin.register(models.ModelProfile)
class ModelProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "model", "base_url")
    # El token cifrado nunca se muestra ni se edita crudo desde el admin.
    exclude = ("auth_token_enc",)


@admin.register(models.PermissionPolicy)
class PermissionPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "mode")


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "status", "model_profile", "permission_policy")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(models.Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "scope", "project", "enabled")


@admin.register(models.McpServer)
class McpServerAdmin(admin.ModelAdmin):
    list_display = ("name", "scope", "transport", "project", "enabled")


@admin.register(models.Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "total_cost_usd", "started_at", "ended_at")
    readonly_fields = ("id",)


@admin.register(models.Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("session", "seq", "type", "ts")
    list_filter = ("type",)


@admin.register(models.PermissionRequest)
class PermissionRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "tool", "status", "resolved_by", "expires_at")


@admin.register(models.PortRegistry)
class PortRegistryAdmin(admin.ModelAdmin):
    list_display = ("port", "project", "purpose", "status")
