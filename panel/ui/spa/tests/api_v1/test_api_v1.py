

# ---------- D13: project_diff no devuelve 500 si no es repo git ----------

def test_project_diff_returns_200_when_not_a_repo(tmp_path):
    """Si el path no es un repo git, devolver 200 con not_a_repo=true
    en vez de 500 (que rompía el panel lateral Cambios del SPA)."""
    # Crear dir SIN init git
    p = _project("no-git", tmp_path)
    (tmp_path / "no-git").mkdir(parents=True, exist_ok=True)
    c = _client_verified()
    r = c.get(f"/api/v1/projects/{p.slug}/diff/")
    assert r.status_code == 200, f"esperado 200, got {r.status_code}: {r.content}"
    body = r.json()
    assert body["not_a_repo"] is True
    assert body["diff"] == ""
    assert body["dirty"] is False
