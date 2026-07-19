import json

from django import forms

from panel.core.models import McpServer, Project


class LoginForm(forms.Form):
    """Login en un solo paso: usuario + contraseña + token TOTP."""

    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)
    token = forms.CharField(max_length=6, label="Código TOTP")


class ProjectForm(forms.ModelForm):
    """Crear/editar proyecto. `path` se deriva del slug (no expuesto). Si
    github_enabled=True y no hay token guardado, falla limpio en validación."""

    class Meta:
        model = Project
        fields = ["name", "slug", "model_profile", "permission_policy",
                  "github_repo", "github_enabled"]
        widgets = {
            "slug": forms.TextInput(attrs={"pattern": "[a-z0-9][a-z0-9-]*"}),
        }

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip().lower()
        # El path debe estar bajo PROJECTS_ROOT y coincidir con el slug para
        # que las deny obligatorias dinámicas (constants.py:28-29) calcen.
        return slug

    def clean_github_repo(self):
        repo = self.cleaned_data.get("github_repo")
        if not repo:
            return repo
        repo = repo.strip()
        # Formato owner/repo, sin protocolo ni .git al final.
        if repo.startswith(("http://", "https://", "git@")) or repo.endswith(".git"):
            raise forms.ValidationError(
                "Usa el formato corto: owner/repo (sin https ni .git)."
            )
        if "/" not in repo or repo.count("/") > 1:
            raise forms.ValidationError("Formato esperado: owner/repo.")
        return repo

    def clean(self):
        cleaned = super().clean() or {}
        gh_enabled = cleaned.get("github_enabled")
        gh_repo = cleaned.get("github_repo")
        if gh_enabled and not gh_repo:
            raise forms.ValidationError(
                "github_repo es obligatorio si github_enabled está activo."
            )
        if gh_enabled and gh_repo:
            # Chequeo lazy del token (import circular evitado).
            from panel.core.services import github as gh_svc
            if not gh_svc.has_token():
                raise forms.ValidationError(
                    "github_enabled=True pero no hay token de GitHub guardado. "
                    "Ve a Ajustes → GitHub y pega uno primero."
                )
        return cleaned

    def save(self, commit: bool = True):
        project = super().save(commit=False)
        # path se deriva del slug. Fijo e inmutable para que las deny casen.
        from django.conf import settings
        project.path = f"{settings.PROJECTS_ROOT}/{project.slug}"
        if commit:
            project.save()
        return project


class McpServerForm(forms.ModelForm):
    """Crear/editar un MCP server. `config` se expone como textarea JSON para
    no atar la UI a un schema concreto (stdio vs http difieren). Validamos
    estructura mínima según `transport`."""

    config_text = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6, "cols": 60}),
        required=False,
        help_text=(
            "JSON. stdio: {\"command\": \"...\", \"args\": [...], \"env\": {...}}. "
            "http: {\"url\": \"http://127.0.0.1:PORT\"}."
        ),
        label="Config (JSON)",
    )

    class Meta:
        model = McpServer
        fields = ["name", "scope", "project", "transport", "enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Si editamos, precargar config_text desde el JSONField.
        if self.instance and self.instance.pk and self.instance.config:
            self.fields["config_text"].initial = json.dumps(
                self.instance.config, indent=2, ensure_ascii=False
            )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        # El renderer usa `name` como clave en mcpServers{} — sin espacios raros.
        if not name.replace("_", "").replace("-", "").isalnum():
            raise forms.ValidationError(
                "Usa solo letras, dígitos, '-' o '_'."
            )
        return name

    def clean(self):
        cleaned = super().clean() or {}
        scope = cleaned.get("scope")
        project = cleaned.get("project")
        if scope == McpServer.Scope.PROJECT and not project:
            raise forms.ValidationError(
                "Si scope=project, debes elegir un proyecto."
            )
        if scope == McpServer.Scope.GLOBAL and project:
            cleaned["project"] = None

        # Parsear y validar config_text según transport.
        raw = (cleaned.get("config_text") or "").strip()
        if not raw:
            raise forms.ValidationError(
                "Config (JSON) es obligatorio."
            )
        try:
            cfg = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"JSON inválido: {exc}") from exc
        if not isinstance(cfg, dict):
            raise forms.ValidationError("Config debe ser un objeto JSON {}.")

        transport = cleaned.get("transport")
        if transport == McpServer.Transport.STDIO:
            if "command" not in cfg or not isinstance(cfg["command"], str):
                raise forms.ValidationError(
                    "stdio requiere `command` (string)."
                )
            if "args" in cfg and not isinstance(cfg["args"], list):
                raise forms.ValidationError("`args` debe ser una lista.")
            if "env" in cfg and not isinstance(cfg["env"], dict):
                raise forms.ValidationError("`env` debe ser un objeto {}.")
        elif transport == McpServer.Transport.HTTP:
            url = cfg.get("url")
            if not url or not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise forms.ValidationError(
                    "http requiere `url` (http:// o https://)."
                )

        cleaned["config"] = cfg
        return cleaned

    def save(self, commit: bool = True):
        mcp = super().save(commit=False)
        mcp.config = self.cleaned_data["config"]
        if commit:
            mcp.save()
        return mcp
