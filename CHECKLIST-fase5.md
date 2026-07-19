# CHECKLIST-fase5.md — confirmación manual de Yoiner

Todo lo automatizable + el e2e real ya está verde (88 tests; ver PROGRESS.md →
Gate 5). El agente abrió un PR real en `plantilla-django-react` (#1).

## Ya verificado en vivo (por mí)
- Token: pegado y validado (usuario YOYO-DR, 53 repos), guardado cifrado.
- Proyecto `webtpl` clonado desde el repo en la rama `agent/webtpl`, sin token en
  `.git/config`.
- El agente editó README, commiteó y abrió **PR #1** vía el MCP `open_pull_request`.
- El PAT no aparece en logs, eventos, ni en el repo (grep exhaustivo = 0).

## Checklist para ti
- [ ] Revisa el **PR #1** en `github.com/YOYO-DR/plantilla-django-react/pull/1`
      (es de prueba, "Ajuste de prueba via panel"). Ciérralo/mergéalo tú si quieres.
- [ ] En `/github/` del panel, prueba pegar un token inválido → debe decir
      "Token inválido" sin romper nada.
- [ ] **Recomendado para el candado de "no merge":** activa **branch protection**
      en `main` del repo (requiere PR + revisión). Así aunque el token pueda
      mergear por API, nadie mergea sin tu revisión.
- [ ] (Opcional) Crea otro proyecto desde un repo distinto y confirma que se
      clona en su rama `agent/<slug>`.

## Notas
- El MCP de agentes NO tiene tool de merge: los agentes solo abren PRs / comentan.
- El token se guarda cifrado en la BD (tabla Config, clave `github_token_enc`);
  nunca se re-muestra ni se escribe a disco de proyecto.
- Identidad git de los agentes: "Agente Claude Code".
