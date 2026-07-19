from django import forms

from panel.core.models import Project


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
