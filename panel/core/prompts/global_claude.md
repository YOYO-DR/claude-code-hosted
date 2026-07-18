# Reglas globales — todos los agentes de este VPS

- Compartes este VPS con otros agentes en otros proyectos. Acceso amplio ≠ permiso para todo: criterio primero.
- PUERTOS: nunca elijas puerto a mano. MCP `ports`: `allocate_port` antes de exponer cualquier servicio, `list_ports` para consultar, `release_port` al desmontar. Puerto ocupado = de otro agente: pide otro, jamás mates procesos ajenos.
- No detengas, reinicies ni modifiques contenedores, servicios o procesos que no sean de tu proyecto. En duda, genera solicitud de aprobación.
- Trabaja solo dentro de tu directorio. Otros proyectos, ~/.ssh, ~/.claude y /etc están denegados: no intentes rodear el deny.
- Docker: prefija contenedores, redes y volúmenes con el slug de tu proyecto.
- Si una aprobación expira sin respuesta, continúa con lo que no la requiera o deja el trabajo limpio y documentado en NOTES.md.
- Registra en NOTES.md de tu proyecto los recursos compartidos que uses y el estado que otros agentes deban conocer.
- Git: ramas + PRs. Nunca push directo a main/master sin aprobación explícita.
