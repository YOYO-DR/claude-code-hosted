"""AskUserQuestion (SP14): parseo del input del CLI y construcción de la
respuesta que espera de vuelta.

Contrato real del CLI (`sdk-tools.d.ts`, `AskUserQuestionInput`):

    {
      "questions": [
        {
          "question": "¿…?",
          "header": "Chip",              # ≤12 chars, etiqueta corta
          "options": [                    # 2..4 opciones
            {"label": "…", "description": "…", "preview": "…"?}
          ],
          "multiSelect": false
        }
      ],
      "answers": {"<texto de la pregunta>": "<label elegido>"},   # ← respuesta
      "annotations": {"<texto>": {"preview": "…", "notes": "…"}},
      "metadata": {"source": "…"}
    }

La respuesta va en `answers`, **keyed por el TEXTO de la pregunta** (no por
índice) y con el **label** de la opción como valor. Para `multiSelect`, los
labels elegidos se unen con ", ".

SP9.1 asumía `{question, options}` (singular, plano) y devolvía
`{"answer": <int>}` — ninguna de las dos cosas existe en el contrato real, por
eso el bubble caía al preview crudo y el CLI no recibía la elección.
"""

from __future__ import annotations

# Une los labels de un multiSelect. El CLI recibe un string por pregunta.
MULTI_JOIN = ", "


def parse_questions(input_full: object) -> list[dict]:
    """Normaliza `input_full["questions"]` a una lista de dicts seguros de
    renderizar. Tolera formas parciales/ajenas: lo que no encaja se descarta en
    vez de romper el render (una pregunta mal formada no debe tumbar el chat).

    Devuelve [] si no hay nada usable — el llamador cae al render genérico.
    """
    if not isinstance(input_full, dict):
        return []
    raw = input_full.get("questions")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or "").strip()
        if not text:
            continue
        options: list[dict] = []
        for o in q.get("options") or []:
            if isinstance(o, dict):
                label = str(o.get("label") or "").strip()
                if not label:
                    continue
                options.append({
                    "label": label,
                    "description": str(o.get("description") or ""),
                    "preview": str(o.get("preview") or "") or None,
                })
            elif isinstance(o, str) and o.strip():
                # Forma degradada: lista de strings.
                options.append({"label": o.strip(), "description": "", "preview": None})
        if not options:
            continue
        out.append({
            "question": text,
            "header": str(q.get("header") or "")[:32],
            "options": options,
            "multiSelect": bool(q.get("multiSelect")),
        })
    return out


def build_answers(questions: list[dict], selections: dict) -> dict[str, str]:
    """`{qIdx: [optIdx, …]}` → `{textoPregunta: "label"|"labelA, labelB"}`.

    Los índices vienen del cliente (SPA/Telegram) pero los **labels salen
    siempre del `input_full` guardado en la BD**: el cliente nunca inyecta el
    texto de la respuesta, solo señala cuál eligió. Índices fuera de rango o
    preguntas inexistentes se ignoran.
    """
    answers: dict[str, str] = {}
    for q_key, opt_idxs in (selections or {}).items():
        try:
            qi = int(q_key)
        except (TypeError, ValueError):
            continue
        if qi < 0 or qi >= len(questions):
            continue
        q = questions[qi]
        # Acepta int suelto o lista de ints.
        if isinstance(opt_idxs, int):
            opt_idxs = [opt_idxs]
        if not isinstance(opt_idxs, (list, tuple)):
            continue
        labels: list[str] = []
        for raw_oi in opt_idxs:
            try:
                oi = int(raw_oi)
            except (TypeError, ValueError):
                continue
            if 0 <= oi < len(q["options"]):
                label = q["options"][oi]["label"]
                if label not in labels:  # dedup: doble tap en Telegram
                    labels.append(label)
        if not labels:
            continue
        # multiSelect=False con varias selecciones → nos quedamos con la
        # primera (defensivo; la UI no debería permitirlo).
        if not q["multiSelect"]:
            labels = labels[:1]
        answers[q["question"]] = MULTI_JOIN.join(labels)
    return answers


def is_complete(questions: list[dict], selections: dict) -> bool:
    """True si toda pregunta tiene al menos una opción elegida. Se usa para
    auto-enviar (Telegram) y para habilitar el botón Enviar (SPA)."""
    if not questions:
        return False
    for qi in range(len(questions)):
        sel = (selections or {}).get(str(qi), (selections or {}).get(qi))
        if isinstance(sel, int):
            sel = [sel]
        if not sel:
            return False
    return True


def summarize(questions: list[dict], answers: dict[str, str]) -> str:
    """Texto plano de lo respondido, para el mensaje de Telegram ya resuelto."""
    lines = []
    for q in questions:
        chosen = answers.get(q["question"])
        if chosen:
            head = q["header"] or q["question"][:32]
            lines.append(f"• {head}: {chosen}")
    return "\n".join(lines)
