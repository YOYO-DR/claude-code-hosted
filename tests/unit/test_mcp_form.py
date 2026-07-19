"""Tests del McpServerForm (UI de MCPs). Valida estructura de config según
transport, scope obligatorio de project, JSON malformado, etc."""

from __future__ import annotations

import pytest

from panel.core.models import McpServer, ModelProfile, PermissionPolicy, Project
from panel.ui.forms import McpServerForm

pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return Project.objects.create(
        slug="alpha",
        name="Alpha",
        path="/srv/projects/alpha",
        model_profile=ModelProfile.objects.create(
            name="p", provider=ModelProfile.Provider.ANTHROPIC, model="m"
        ),
        permission_policy=PermissionPolicy.objects.create(name="pol"),
    )


def _base(**over):
    data = {
        "name": "mi-mcp",
        "scope": "project",
        "project": "",  # se setea en cada test
        "transport": "stdio",
        "enabled": True,
        "config_text": '{"command": "/usr/bin/x"}',
    }
    data.update(over)
    return data


def test_stdio_minimo_ok(project):
    form = McpServerForm(_base(project=project.pk))
    assert form.is_valid(), form.errors


def test_http_requiere_url(project):
    form = McpServerForm(_base(project=project.pk, transport="http", config_text='{"command":"x"}'))
    assert not form.is_valid()
    assert "url" in str(form.errors)


def test_stdio_requiere_command(project):
    form = McpServerForm(_base(project=project.pk, config_text='{"args":["a"]}'))
    assert not form.is_valid()
    assert "command" in str(form.errors)


def test_scope_project_sin_project_falla():
    form = McpServerForm(_base(project=""))
    assert not form.is_valid()
    assert "proyecto" in str(form.errors).lower()


def test_scope_global_limpia_project(project):
    form = McpServerForm(_base(scope="global", project=project.pk))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["project"] is None


def test_json_invalido(project):
    form = McpServerForm(_base(project=project.pk, config_text="{esto no es json"))
    assert not form.is_valid()
    assert "JSON" in str(form.errors)


def test_config_no_es_dict(project):
    form = McpServerForm(_base(project=project.pk, config_text='["lista", "no", "dict"]'))
    assert not form.is_valid()


def test_stdio_env_debe_ser_dict(project):
    form = McpServerForm(_base(project=project.pk, config_text='{"command":"x","env":["a"]}'))
    assert not form.is_valid()
    assert "env" in str(form.errors)


def test_http_url_invalida(project):
    form = McpServerForm(_base(project=project.pk, transport="http", config_text='{"url":"ftp://x"}'))
    assert not form.is_valid()


def test_name_con_espacios_falla(project):
    form = McpServerForm(_base(name="mi mcp"))
    assert not form.is_valid()
    assert "name" in str(form.errors).lower() or "nombre" in str(form.errors).lower()


def test_save_persiste_config(project):
    form = McpServerForm(_base(project=project.pk))
    assert form.is_valid()
    mcp = form.save()
    mcp.refresh_from_db()
    assert mcp.config == {"command": "/usr/bin/x"}
    assert mcp.name == "mi-mcp"
    assert mcp.scope == McpServer.Scope.PROJECT


def test_edit_precarga_config_text(project):
    mcp = McpServer.objects.create(
        name="old", scope="global", transport="stdio",
        config={"command": "/bin/y", "args": ["--flag"]},
    )
    form = McpServerForm(instance=mcp)
    assert '"command": "/bin/y"' in form.fields["config_text"].initial
    assert '"--flag"' in form.fields["config_text"].initial