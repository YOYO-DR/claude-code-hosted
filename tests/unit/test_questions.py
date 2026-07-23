"""SP14 — AskUserQuestion: parseo del schema real del CLI, construcción de
`answers` (keyed por texto de pregunta, valor = label), encoding de selecciones
en Redis y teclado de Telegram bajo el límite de 64 bytes de callback_data."""

from __future__ import annotations

import base64
import json

import pytest

from panel.core.services import questions as q
from panel.core.services import telegram as tg

# Payload real reportado por el usuario (3 preguntas, single-select).
REAL_INPUT = {
    "questions": [
        {
            "question": "¿Por dónde arranco la remoción?",
            "header": "Arranque",
            "options": [
                {"label": "Sí, arranca con Fase 0", "description": "Creo rama…"},
                {"label": "Mostrame el plan", "description": "Antes de tocar nada…"},
            ],
            "multiSelect": False,
        },
        {
            "question": "El flag FINANCE_AI_USE_LANGRAPH hoy es False. ¿Cómo lo dejo?",
            "header": "Flag",
            "options": [
                {"label": "Forzar True al limpiar", "description": "Cambio el default…"},
                {"label": "Dejarlo False", "description": "Default sigue False…"},
                {"label": "Eliminar el flag", "description": "Borro el flag…"},
            ],
            "multiSelect": False,
        },
        {
            "question": "¿Qué hago con apps/finances/ai/?",
            "header": "Finances AI",
            "options": [
                {"label": "Eliminar todo", "description": "Borra la carpeta…"},
                {"label": "Mantener la lógica", "description": "Conservo los DraftService…"},
            ],
            "multiSelect": False,
        },
    ]
}


# ---------- parse_questions ----------

def test_parse_real_payload():
    qs = q.parse_questions(REAL_INPUT)
    assert len(qs) == 3
    assert qs[0]["header"] == "Arranque"
    assert qs[0]["multiSelect"] is False
    assert len(qs[1]["options"]) == 3
    assert qs[1]["options"][0]["label"] == "Forzar True al limpiar"


def test_parse_multiselect_flag():
    qs = q.parse_questions({
        "questions": [{
            "question": "¿Qué features?",
            "header": "Features",
            "options": [{"label": "A", "description": ""}, {"label": "B", "description": ""}],
            "multiSelect": True,
        }]
    })
    assert qs[0]["multiSelect"] is True


@pytest.mark.parametrize("bad", [
    None, {}, {"questions": None}, {"questions": []}, {"questions": "x"},
    {"questions": [{}]},                                  # sin texto
    {"questions": [{"question": "¿?"}]},                  # sin opciones
    {"questions": [{"question": "¿?", "options": []}]},   # opciones vacías
    {"questions": [{"question": "", "options": [{"label": "A"}]}]},  # texto vacío
])
def test_parse_degrades_to_empty(bad):
    """Lo que no encaja devuelve [] — el llamador cae al render genérico en vez
    de romper el chat."""
    assert q.parse_questions(bad) == []


def test_parse_tolerates_string_options():
    qs = q.parse_questions({
        "questions": [{"question": "¿A o B?", "options": ["A", "B"]}]
    })
    assert [o["label"] for o in qs[0]["options"]] == ["A", "B"]


def test_parse_skips_malformed_option_but_keeps_rest():
    qs = q.parse_questions({
        "questions": [{
            "question": "¿?",
            "options": [{"label": "OK"}, {"description": "sin label"}, 42],
        }]
    })
    assert [o["label"] for o in qs[0]["options"]] == ["OK"]


# ---------- build_answers ----------

def test_build_answers_keys_by_question_text_not_index():
    """El CLI espera {textoPregunta: label} — no índices, no {'answer': N}."""
    qs = q.parse_questions(REAL_INPUT)
    answers = q.build_answers(qs, {"0": [1], "1": [0], "2": [0]})
    assert answers == {
        "¿Por dónde arranco la remoción?": "Mostrame el plan",
        "El flag FINANCE_AI_USE_LANGRAPH hoy es False. ¿Cómo lo dejo?": "Forzar True al limpiar",
        "¿Qué hago con apps/finances/ai/?": "Eliminar todo",
    }


def test_build_answers_multiselect_joins_labels():
    qs = q.parse_questions({
        "questions": [{
            "question": "¿Cuáles?",
            "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
            "multiSelect": True,
        }]
    })
    assert q.build_answers(qs, {"0": [0, 2]}) == {"¿Cuáles?": "A, C"}


def test_build_answers_single_select_keeps_first_if_multiple_sent():
    """Defensivo: si el cliente manda 2 índices a una pregunta single-select,
    nos quedamos con el primero en vez de emitir un valor inválido."""
    qs = q.parse_questions(REAL_INPUT)
    answers = q.build_answers(qs, {"0": [0, 1]})
    assert answers["¿Por dónde arranco la remoción?"] == "Sí, arranca con Fase 0"


def test_build_answers_dedups_double_tap():
    qs = q.parse_questions({
        "questions": [{
            "question": "¿Cuáles?",
            "options": [{"label": "A"}, {"label": "B"}],
            "multiSelect": True,
        }]
    })
    assert q.build_answers(qs, {"0": [0, 0, 1]}) == {"¿Cuáles?": "A, B"}


@pytest.mark.parametrize("sel", [
    {"9": [0]},        # pregunta fuera de rango
    {"0": [99]},       # opción fuera de rango
    {"0": []},         # sin selección
    {"x": [0]},        # clave no numérica
    {"0": "x"},        # valor no lista
    {"-1": [0]},       # índice negativo
])
def test_build_answers_ignores_invalid(sel):
    qs = q.parse_questions(REAL_INPUT)
    assert q.build_answers(qs, sel) == {}


def test_build_answers_accepts_bare_int():
    qs = q.parse_questions(REAL_INPUT)
    answers = q.build_answers(qs, {"0": 1})
    assert answers["¿Por dónde arranco la remoción?"] == "Mostrame el plan"


# ---------- is_complete ----------

def test_is_complete():
    qs = q.parse_questions(REAL_INPUT)
    assert not q.is_complete(qs, {})
    assert not q.is_complete(qs, {"0": [0]})
    assert not q.is_complete(qs, {"0": [0], "1": [0]})
    assert q.is_complete(qs, {"0": [0], "1": [0], "2": [0]})
    assert not q.is_complete(qs, {"0": [0], "1": [], "2": [0]})
    assert not q.is_complete([], {})


# ---------- encoding en Redis (permissions) ----------

def test_selection_encoding_roundtrip():
    from panel.core.services import permissions as perm

    sel = {"0": [1], "1": [0, 2]}
    blob = base64.urlsafe_b64encode(json.dumps(sel).encode()).decode()
    answer, source, opt, decoded = perm._split_answer(f"allow|web|sel:{blob}")
    assert (answer, source, opt) == ("allow", "web", None)
    assert decoded == sel


def test_selection_encoding_survives_pipe_in_labels():
    """base64 evita que un payload con '|' rompa el split del valor de Redis."""
    from panel.core.services import permissions as perm

    sel = {"0": [0], "weird|key": [1]}
    blob = base64.urlsafe_b64encode(json.dumps(sel).encode()).decode()
    _, _, _, decoded = perm._split_answer(f"allow|telegram|sel:{blob}")
    assert decoded == sel


def test_split_answer_legacy_forms():
    """Requests en vuelo con el formato viejo siguen funcionando."""
    from panel.core.services import permissions as perm

    assert perm._split_answer("allow|web") == ("allow", "web", None, None)
    assert perm._split_answer("deny|telegram|opt:2") == ("deny", "telegram", 2, None)
    assert perm._split_answer("allow") == ("allow", "web", None, None)
    assert perm._split_answer(None) == ("timeout", "web", None, None)
    assert perm._split_answer("allow|web|sel:###") == ("allow", "web", None, None)


# ---------- Telegram ----------

def test_telegram_callback_data_under_64_bytes():
    """Telegram rechaza callback_data > 64 bytes. uuid(36) + prefijo debe caber
    incluso con 2 dígitos de pregunta y opción."""
    uuid = "9f561f3c-a7d2-4b14-8589-9ff6545864aa"
    for token in (f"q0o0:{uuid}", f"q99o99:{uuid}", f"qs:{uuid}",
                  f"deny:{uuid}", f"noop:{uuid}", f"allow_always:{uuid}"):
        assert len(token.encode()) <= tg.CALLBACK_DATA_LIMIT, token


def test_parse_callback_data_question_forms():
    uuid = "9f561f3c-a7d2-4b14-8589-9ff6545864aa"
    assert tg.parse_callback_data(f"q0o1:{uuid}") == ("q0o1", uuid)
    assert tg.parse_callback_data(f"qs:{uuid}") == ("qs", uuid)
    assert tg.parse_callback_data(f"noop:{uuid}") == ("noop", uuid)
    assert tg.parse_callback_data(f"allow:{uuid}") == ("allow", uuid)
    assert tg.parse_callback_data("basura") is None
    assert tg.parse_callback_data(f"qXoY:{uuid}") is None


def test_parse_option_callback():
    assert tg.parse_option_callback("q0o1") == (0, 1)
    assert tg.parse_option_callback("q12o3") == (12, 3)
    assert tg.parse_option_callback("qs") is None
    assert tg.parse_option_callback("allow") is None
    assert tg.parse_option_callback("q1") is None


def test_keyboard_for_questions_has_button_per_option():
    qs = q.parse_questions(REAL_INPUT)
    kb = tg.keyboard_for_questions("uuid-1", qs)
    rows = kb["inline_keyboard"]
    # 3 cabeceras + 2+3+2 opciones + 1 fila de acciones
    assert len(rows) == 3 + 7 + 1
    # Sin multiSelect → no hay botón Enviar, solo Cancelar.
    assert [b["text"] for b in rows[-1]] == ["⛔ Cancelar"]


def test_keyboard_shows_submit_when_multiselect():
    qs = q.parse_questions({
        "questions": [{
            "question": "¿Cuáles?",
            "options": [{"label": "A"}, {"label": "B"}],
            "multiSelect": True,
        }]
    })
    kb = tg.keyboard_for_questions("uuid-1", qs)
    assert any(b["text"] == "✔️ Enviar" for b in kb["inline_keyboard"][-1])


def test_keyboard_marks_selection():
    qs = q.parse_questions(REAL_INPUT)
    kb = tg.keyboard_for_questions("uuid-1", qs, {"0": [1]})
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert any(t.startswith("🔘") for t in labels)
    assert any(t.startswith("⚪") for t in labels)


# ---------- toggle (Telegram) ----------

def test_toggle_single_select_replaces():
    from panel.core.services import tg_notify

    qs = q.parse_questions(REAL_INPUT)
    sel = tg_notify.toggle_selection(qs, {}, 0, 0)
    assert sel["0"] == [0]
    sel = tg_notify.toggle_selection(qs, sel, 0, 1)
    assert sel["0"] == [1]          # reemplaza, no acumula
    sel = tg_notify.toggle_selection(qs, sel, 0, 1)
    assert sel["0"] == []           # re-tap deselecciona


def test_toggle_multiselect_accumulates():
    from panel.core.services import tg_notify

    qs = q.parse_questions({
        "questions": [{
            "question": "¿Cuáles?",
            "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
            "multiSelect": True,
        }]
    })
    sel = tg_notify.toggle_selection(qs, {}, 0, 0)
    sel = tg_notify.toggle_selection(qs, sel, 0, 2)
    assert sel["0"] == [0, 2]
    sel = tg_notify.toggle_selection(qs, sel, 0, 0)
    assert sel["0"] == [2]          # toggle off


def test_toggle_ignores_out_of_range():
    from panel.core.services import tg_notify

    qs = q.parse_questions(REAL_INPUT)
    assert tg_notify.toggle_selection(qs, {}, 99, 0) == {}
    assert tg_notify.toggle_selection(qs, {}, 0, 99) == {}


def test_toggle_keeps_other_questions():
    from panel.core.services import tg_notify

    qs = q.parse_questions(REAL_INPUT)
    sel = {"0": [0], "2": [1]}
    out = tg_notify.toggle_selection(qs, sel, 1, 2)
    assert out == {"0": [0], "2": [1], "1": [2]}


# ---------- summarize ----------

def test_summarize_uses_header():
    qs = q.parse_questions(REAL_INPUT)
    answers = q.build_answers(qs, {"0": [0], "1": [2], "2": [1]})
    text = q.summarize(qs, answers)
    assert "• Arranque: Sí, arranca con Fase 0" in text
    assert "• Flag: Eliminar el flag" in text
    assert "• Finances AI: Mantener la lógica" in text
