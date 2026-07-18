from __future__ import annotations

from django import forms
from django.contrib import admin

from panel.core import crypto, models
from panel.core.services import privileged, provisioning


class ModelProfileForm(forms.ModelForm):
    # Campo write-only: se cifra a auth_token_enc. El token existente NUNCA se
    # re-muestra (§5 Fase 2). Dejar en blanco = conservar el actual.
    auth_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Se cifra al guardar. Vacío = conservar el token actual.",
    )

    class Meta:
        model = models.ModelProfile
        # auth_token_enc queda fuera a propósito: solo se escribe cifrado vía
        # el campo write-only auth_token.
        fields = ("name", "provider", "base_url", "model", "extra_env")

    def save(self, commit: bool = True):
        obj = super().save(commit=False)
        token = self.cleaned_data.get("auth_token")
        if token:
            obj.auth_token_enc = crypto.encrypt(token)
        if commit:
            obj.save()
        return obj


@admin.register(models.ModelProfile)
class ModelProfileAdmin(admin.ModelAdmin):
    form = ModelProfileForm
    list_display = ("name", "provider", "model", "base_url", "has_token")
    exclude = ("auth_token_enc",)

    @admin.display(boolean=True, description="token")
    def has_token(self, obj: models.ModelProfile) -> bool:
        return bool(obj.auth_token_enc)


@admin.register(models.PermissionPolicy)
class PermissionPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "mode")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        privileged.run_render()


@admin.register(models.Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("slug", "name", "status", "model_profile", "permission_policy")
    prepopulated_fields = {"slug": ("name",)}
    actions = ["archivar"]

    def get_readonly_fields(self, request, obj=None):
        # El path se deriva del slug al crear; no editable después.
        return ("path",) if obj else ()

    def save_model(self, request, obj, form, change):
        if not change and not obj.path:
            from django.conf import settings

            obj.path = str(settings.PROJECTS_ROOT / obj.slug)
        super().save_model(request, obj, form, change)
        if not change:
            provisioning.provision_project(obj)
        else:
            privileged.run_render()

    @admin.action(description="Archivar (detiene sesiones, conserva datos)")
    def archivar(self, request, queryset):
        for project in queryset:
            provisioning.archive_project(project)


class _RenderOnSaveAdmin(admin.ModelAdmin):
    """Editar skills o MCP re-renderiza. Para MCP, la UI de sesión mostrará
    'reinicio requerido' (los MCP no recargan en caliente, §4.3)."""

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        privileged.run_render()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        privileged.run_render()


@admin.register(models.Skill)
class SkillAdmin(_RenderOnSaveAdmin):
    list_display = ("name", "scope", "project", "enabled")


@admin.register(models.McpServer)
class McpServerAdmin(_RenderOnSaveAdmin):
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
