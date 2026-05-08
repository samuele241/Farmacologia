from __future__ import annotations

import json
import random
import sqlite3
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st


BASE_DIR = Path(__file__).resolve().parent
DATASET_PATH = BASE_DIR / "dataset_farmacologia.json"
DB_PATH = BASE_DIR / "quiz_progress.sqlite3"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@st.cache_data(show_spinner=False)
def load_dataset() -> list[dict]:
    with open(DATASET_PATH, "r", encoding="utf-8") as file:
        data = json.load(file)

    cleaned = []
    for item in data:
        question_id = int(item["id"])
        options = item.get("opzioni", [])
        if not isinstance(options, list):
            options = []

        cleaned.append(
            {
                "id": question_id,
                "domanda": str(item.get("domanda", "")).strip(),
                "opzioni": [str(option).strip() for option in options if str(option).strip()],
                "risposta_corretta": str(item.get("risposta_corretta", "")).strip(),
            }
        )

    cleaned.sort(key=lambda item: item["id"])
    return cleaned


@st.cache_resource(show_spinner=False)
def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS questions (
            question_id INTEGER PRIMARY KEY,
            domanda TEXT NOT NULL,
            opzioni_json TEXT NOT NULL,
            risposta_corretta TEXT NOT NULL,
            times_shown INTEGER NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL DEFAULT 0,
            wrong_count INTEGER NOT NULL DEFAULT 0,
            last_shown_at TEXT,
            last_answered_at TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            selected_option TEXT NOT NULL,
            is_correct INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(question_id) REFERENCES questions(question_id)
        )
        """
    )
    connection.commit()


def sync_dataset(connection: sqlite3.Connection, dataset: list[dict]) -> None:
    for item in dataset:
        connection.execute(
            """
            INSERT INTO questions (
                question_id,
                domanda,
                opzioni_json,
                risposta_corretta,
                times_shown,
                correct_count,
                wrong_count,
                last_shown_at,
                last_answered_at
            ) VALUES (?, ?, ?, ?, 0, 0, 0, NULL, NULL)
            ON CONFLICT(question_id) DO UPDATE SET
                domanda = excluded.domanda,
                opzioni_json = excluded.opzioni_json,
                risposta_corretta = excluded.risposta_corretta
            """,
            (
                item["id"],
                item["domanda"],
                json.dumps(item["opzioni"], ensure_ascii=False),
                item["risposta_corretta"],
            ),
        )
    connection.commit()


def build_question_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["question_id"],
        "domanda": row["domanda"],
        "opzioni": json.loads(row["opzioni_json"]),
        "risposta_corretta": row["risposta_corretta"],
        "times_shown": row["times_shown"],
        "correct_count": row["correct_count"],
        "wrong_count": row["wrong_count"],
        "last_shown_at": row["last_shown_at"],
        "last_answered_at": row["last_answered_at"],
    }


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value).lower())
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def review_reason(question: dict) -> str | None:
    domanda = normalize_text(question.get("domanda", ""))
    risposta = normalize_text(question.get("risposta_corretta", ""))

    if not risposta:
        return "Risposta corretta mancante o non recuperabile"

    if risposta and len(risposta) >= 4 and risposta in domanda:
        return "La risposta compare già nel testo della domanda"

    risposta_tokens = [token for token in risposta.split() if len(token) > 3]
    if risposta_tokens:
        hits = sum(1 for token in risposta_tokens if token in domanda)
        if hits >= 2 and hits / len(risposta_tokens) >= 0.6:
            return "C'è un forte overlap tra domanda e risposta"

    return None


def strip_answer_from_question(question_text: str, answer_text: str) -> str | None:
    answer_normalized = normalize_text(answer_text)
    if not answer_normalized:
        return None

    raw_question = str(question_text).strip()
    for delimiter in (":", "?", "!", ";"):
        delimiter_index = raw_question.rfind(delimiter)
        if delimiter_index == -1:
            continue

        suffix = raw_question[delimiter_index + 1 :].strip(" \t\r\n-–—:;.,")
        if normalize_text(suffix) == answer_normalized:
            cleaned = raw_question[:delimiter_index].rstrip(" \t\r\n-–—:;.,")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned if cleaned else None

    return None


def get_auto_fix_candidates(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT question_id, domanda, opzioni_json, risposta_corretta, times_shown, correct_count, wrong_count, last_shown_at, last_answered_at
        FROM questions
        ORDER BY question_id ASC
        """
    ).fetchall()

    candidates = []
    for row in rows:
        question = build_question_row(row)
        cleaned_domanda = strip_answer_from_question(question["domanda"], question["risposta_corretta"])
        if cleaned_domanda and cleaned_domanda != question["domanda"]:
            question["cleaned_domanda"] = cleaned_domanda
            candidates.append(question)

    return candidates


def get_review_candidates(connection: sqlite3.Connection, search_term: str = "") -> list[dict]:
    rows = connection.execute(
        """
        SELECT question_id, domanda, opzioni_json, risposta_corretta, times_shown, correct_count, wrong_count, last_shown_at, last_answered_at
        FROM questions
        ORDER BY question_id ASC
        """
    ).fetchall()

    candidates = []
    search_value = normalize_text(search_term)

    for row in rows:
        question = build_question_row(row)
        reason = review_reason(question)
        question["review_reason"] = reason
        question["is_suspicious"] = reason is not None

        if search_value:
            haystack = normalize_text(f"{question['id']} {question['domanda']} {question['risposta_corretta']}")
            if search_value not in haystack:
                continue

        if reason is not None:
            candidates.append(question)

    return candidates


def update_question_persistence(
    connection: sqlite3.Connection,
    question_id: int,
    new_domanda: str,
    new_risposta_corretta: str,
    new_opzioni: list[str],
) -> None:
    connection.execute(
        """
        UPDATE questions
        SET domanda = ?,
            opzioni_json = ?,
            risposta_corretta = ?
        WHERE question_id = ?
        """,
        (new_domanda, json.dumps(new_opzioni, ensure_ascii=False), new_risposta_corretta, question_id),
    )
    connection.commit()

    dataset = []
    with open(DATASET_PATH, "r", encoding="utf-8") as file:
        dataset = json.load(file)

    updated = False
    for item in dataset:
        if int(item.get("id", -1)) == question_id:
            item["domanda"] = new_domanda
            item["risposta_corretta"] = new_risposta_corretta
            item["opzioni"] = new_opzioni
            updated = True
            break

    if not updated:
        raise ValueError(f"Domanda {question_id} non trovata nel dataset JSON")

    with open(DATASET_PATH, "w", encoding="utf-8") as file:
        json.dump(dataset, file, indent=4, ensure_ascii=False)

    load_dataset.clear()


def auto_fix_leaked_questions(connection: sqlite3.Connection) -> int:
    dataset = load_dataset()
    changed = 0

    for item in dataset:
        cleaned_domanda = strip_answer_from_question(item["domanda"], item["risposta_corretta"])
        if not cleaned_domanda or cleaned_domanda == item["domanda"]:
            continue

        item["domanda"] = cleaned_domanda
        connection.execute(
            "UPDATE questions SET domanda = ? WHERE question_id = ?",
            (cleaned_domanda, item["id"]),
        )
        changed += 1

    if changed:
        with open(DATASET_PATH, "w", encoding="utf-8") as file:
            json.dump(dataset, file, indent=4, ensure_ascii=False)
        connection.commit()
        load_dataset.clear()

        if st.session_state.get("current_question") and st.session_state.current_question["id"]:
            current_id = st.session_state.current_question["id"]
            refreshed = get_question_by_id(connection, current_id)
            if refreshed is not None:
                st.session_state.current_question = build_question_row(refreshed)

    return changed


def normalize_choices(question: dict) -> list[str]:
    candidates = [question["risposta_corretta"], *question["opzioni"]]
    unique_choices = []
    for choice in candidates:
        choice = str(choice).strip()
        if choice and choice not in unique_choices:
            unique_choices.append(choice)

    random.shuffle(unique_choices)
    return unique_choices


def pick_next_question(connection: sqlite3.Connection) -> sqlite3.Row | None:
    min_row = connection.execute("SELECT MIN(times_shown) AS min_times FROM questions").fetchone()
    if min_row is None or min_row["min_times"] is None:
        return None

    return connection.execute(
        """
        SELECT *
        FROM questions
        WHERE times_shown = ?
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (min_row["min_times"],),
    ).fetchone()


def get_question_by_id(connection: sqlite3.Connection, question_id: int) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM questions WHERE question_id = ?", (question_id,)).fetchone()


def mark_question_shown(connection: sqlite3.Connection, question_id: int) -> None:
    connection.execute(
        """
        UPDATE questions
        SET times_shown = times_shown + 1,
            last_shown_at = ?
        WHERE question_id = ?
        """,
        (now_iso(), question_id),
    )
    connection.commit()


def record_attempt(connection: sqlite3.Connection, question_id: int, selected_option: str, is_correct: bool) -> None:
    timestamp = now_iso()
    connection.execute(
        """
        INSERT INTO attempts (question_id, selected_option, is_correct, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (question_id, selected_option, int(is_correct), timestamp),
    )
    connection.execute(
        """
        UPDATE questions
        SET correct_count = correct_count + CASE WHEN ? = 1 THEN 1 ELSE 0 END,
            wrong_count = wrong_count + CASE WHEN ? = 0 THEN 1 ELSE 0 END,
            last_answered_at = ?
        WHERE question_id = ?
        """,
        (int(is_correct), int(is_correct), timestamp, question_id),
    )
    connection.commit()


def get_global_stats(connection: sqlite3.Connection) -> dict:
    totals = connection.execute(
        """
        SELECT
            COUNT(*) AS total_questions,
            COALESCE(SUM(times_shown), 0) AS total_shows,
            COALESCE(SUM(correct_count), 0) AS total_correct,
            COALESCE(SUM(wrong_count), 0) AS total_wrong,
            COALESCE(SUM(CASE WHEN times_shown > 0 THEN 1 ELSE 0 END), 0) AS seen_questions,
            COALESCE(SUM(CASE WHEN times_shown = 0 THEN 1 ELSE 0 END), 0) AS unseen_questions
        FROM questions
        """
    ).fetchone()

    attempts = totals["total_correct"] + totals["total_wrong"]
    accuracy = (totals["total_correct"] / attempts * 100) if attempts else 0.0

    return {
        "total_questions": totals["total_questions"],
        "total_shows": totals["total_shows"],
        "total_correct": totals["total_correct"],
        "total_wrong": totals["total_wrong"],
        "seen_questions": totals["seen_questions"],
        "unseen_questions": totals["unseen_questions"],
        "attempts": attempts,
        "accuracy": accuracy,
    }


def get_question_table(connection: sqlite3.Connection, min_times: int, max_times: int, search_term: str) -> list[sqlite3.Row]:
    query = """
        SELECT question_id, domanda, times_shown, correct_count, wrong_count, last_shown_at, last_answered_at
        FROM questions
        WHERE times_shown BETWEEN ? AND ?
    """
    params: list[object] = [min_times, max_times]

    if search_term.strip():
        query += " AND LOWER(domanda) LIKE ?"
        params.append(f"%{search_term.strip().lower()}%")

    query += " ORDER BY times_shown ASC, question_id ASC"
    return connection.execute(query, params).fetchall()


def get_all_question_ids(connection: sqlite3.Connection) -> list[int]:
    rows = connection.execute("SELECT question_id FROM questions ORDER BY question_id ASC").fetchall()
    return [row["question_id"] for row in rows]


def set_next_question(connection: sqlite3.Connection) -> None:
    next_row = pick_next_question(connection)
    if next_row is None:
        st.session_state.current_question = None
        st.session_state.current_choices = []
        st.session_state.current_answered = False
        st.session_state.feedback = None
        return

    mark_question_shown(connection, next_row["question_id"])
    refreshed = get_question_by_id(connection, next_row["question_id"])
    question = build_question_row(refreshed)

    st.session_state.current_question = question
    st.session_state.current_choices = normalize_choices(question)
    st.session_state.current_answered = False
    st.session_state.feedback = None


def ensure_session_state(connection: sqlite3.Connection) -> None:
    if "current_question" not in st.session_state:
        st.session_state.current_question = None
    if "current_choices" not in st.session_state:
        st.session_state.current_choices = []
    if "current_answered" not in st.session_state:
        st.session_state.current_answered = False
    if "feedback" not in st.session_state:
        st.session_state.feedback = None

    if st.session_state.current_question is None:
        set_next_question(connection)


def render_header(stats: dict) -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.2rem;
            padding-bottom: 2rem;
            max-width: 1200px;
        }
        .hero {
            background: linear-gradient(135deg, #f7f2e8 0%, #ffffff 55%, #e7f1ef 100%);
            border: 1px solid rgba(0, 0, 0, 0.08);
            border-radius: 24px;
            padding: 1.25rem 1.4rem;
            margin-bottom: 1rem;
        }
        .hero h1 {
            margin-bottom: 0.2rem;
            font-size: 2rem;
            color: #102a43;
        }
        .hero p {
            margin: 0;
            color: #486581;
        }
        .small-pill {
            display: inline-block;
            padding: 0.25rem 0.65rem;
            border-radius: 999px;
            background: #102a43;
            color: white;
            font-size: 0.8rem;
            margin-right: 0.35rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="hero">
            <span class="small-pill">Persistente</span>
            <span class="small-pill">Random bilanciato</span>
            <h1>Dashboard farmacologia</h1>
            <p>{stats['total_questions']} domande totali, {stats['seen_questions']} già viste, accuratezza globale {stats['accuracy']:.1f}%</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Dashboard Farmacologia", page_icon="🧪", layout="wide")

    if not DATASET_PATH.exists():
        st.error(f"Dataset non trovato: {DATASET_PATH}")
        st.stop()

    dataset = load_dataset()
    connection = get_connection()
    initialize_database(connection)
    sync_dataset(connection, dataset)
    ensure_session_state(connection)

    stats = get_global_stats(connection)
    render_header(stats)

    left_col, right_col = st.columns([1.35, 0.9], gap="large")

    with left_col:
        st.subheader("Quiz")
        current_question = st.session_state.current_question

        if current_question is None:
            st.info("Non ci sono domande disponibili al momento.")
        else:
            st.caption(
                f"Domanda ID {current_question['id']} · mostrata {current_question['times_shown']} volte · corrette {current_question['correct_count']} · errate {current_question['wrong_count']}"
            )
            st.markdown(f"### {current_question['domanda']}")

            if not st.session_state.current_answered:
                with st.form("answer_form", clear_on_submit=False):
                    selected_option = st.radio(
                        "Scegli una risposta",
                        st.session_state.current_choices,
                        index=None,
                        label_visibility="collapsed",
                    )
                    submitted = st.form_submit_button("Conferma risposta")

                if submitted:
                    if selected_option is None:
                        st.warning("Seleziona una risposta prima di confermare.")
                    else:
                        is_correct = selected_option == current_question["risposta_corretta"]
                        record_attempt(connection, current_question["id"], selected_option, is_correct)
                        st.session_state.current_answered = True
                        st.session_state.feedback = {
                            "is_correct": is_correct,
                            "selected_option": selected_option,
                        }
                        st.rerun()
            else:
                feedback = st.session_state.feedback or {}
                if feedback.get("is_correct"):
                    st.success("Risposta corretta.")
                else:
                    st.error("Risposta sbagliata.")

                st.write(f"**Hai risposto:** {feedback.get('selected_option', '-')}")
                st.write(f"**Risposta corretta:** {current_question['risposta_corretta']}")

                if st.button("Prossima domanda", type="primary"):
                    set_next_question(connection)
                    st.rerun()

        with st.expander("Storico sessione recente", expanded=False):
            recent = connection.execute(
                """
                SELECT a.created_at, a.question_id, q.domanda, a.selected_option, a.is_correct
                FROM attempts a
                JOIN questions q ON q.question_id = a.question_id
                ORDER BY a.attempt_id DESC
                LIMIT 10
                """
            ).fetchall()
            if recent:
                for item in recent:
                    status = "corretta" if item["is_correct"] else "sbagliata"
                    st.write(f"{item['created_at']} · Q{item['question_id']} · {status} · {item['selected_option']}")
            else:
                st.caption("Nessun tentativo registrato ancora.")

    with right_col:
        st.subheader("Performance")
        metric_cols = st.columns(2)
        metric_cols[0].metric("Accuratezza", f"{stats['accuracy']:.1f}%")
        metric_cols[1].metric("Tentativi", str(stats["attempts"]))

        metric_cols_2 = st.columns(2)
        metric_cols_2[0].metric("Risposte corrette", str(stats["total_correct"]))
        metric_cols_2[1].metric("Risposte errate", str(stats["total_wrong"]))

        st.markdown("---")
        st.subheader("Filtro esposizioni")

        max_shows_row = connection.execute("SELECT COALESCE(MAX(times_shown), 0) AS max_shows FROM questions").fetchone()
        max_shows = int(max_shows_row["max_shows"] or 0)

        min_shows, max_shows_selected = st.slider(
            "Filtra domande per numero di volte mostrate",
            min_value=0,
            max_value=max_shows,
            value=(0, max_shows),
        )
        search_term = st.text_input("Cerca nel testo della domanda", value="")

        filtered_rows = get_question_table(connection, min_shows, max_shows_selected, search_term)
        st.caption(f"{len(filtered_rows)} domande nel filtro corrente")
        st.dataframe(
            [
                {
                    "id": row["question_id"],
                    "domanda": row["domanda"],
                    "mostrata": row["times_shown"],
                    "corrette": row["correct_count"],
                    "errate": row["wrong_count"],
                }
                for row in filtered_rows
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("---")
        st.subheader("Editor domande")
        review_only = st.checkbox("Mostra solo domande sospette o incomplete", value=True)
        editor_search = st.text_input("Cerca per ID o testo nella sezione editor", value="")
        auto_fix_candidates = get_auto_fix_candidates(connection)
        st.caption(f"{len(auto_fix_candidates)} domande sono correggibili automaticamente in modo sicuro")

        if st.button(
            f"Correggi automaticamente le {len(auto_fix_candidates)} domande sicure",
            type="primary",
            disabled=not auto_fix_candidates,
        ):
            changed = auto_fix_leaked_questions(connection)
            st.success(f"Corrette automaticamente {changed} domande nel JSON e nel database.")
            st.rerun()

        if review_only:
            editor_candidates = get_review_candidates(connection, editor_search)
        else:
            rows = connection.execute(
                """
                SELECT question_id, domanda, opzioni_json, risposta_corretta, times_shown, correct_count, wrong_count, last_shown_at, last_answered_at
                FROM questions
                ORDER BY question_id ASC
                """
            ).fetchall()
            editor_candidates = []
            search_value = normalize_text(editor_search)
            for row in rows:
                question = build_question_row(row)
                question["review_reason"] = review_reason(question)
                if search_value:
                    haystack = normalize_text(f"{question['id']} {question['domanda']} {question['risposta_corretta']}")
                    if search_value not in haystack:
                        continue
                editor_candidates.append(question)

        if not editor_candidates:
            st.caption("Nessuna domanda corrisponde ai filtri correnti.")
        else:
            selected_question_id = st.selectbox(
                "Seleziona la domanda da modificare",
                options=[question["id"] for question in editor_candidates],
                format_func=lambda question_id: f"{question_id} · {next(item['domanda'][:90] for item in editor_candidates if item['id'] == question_id)}",
            )
            selected_row = get_question_by_id(connection, int(selected_question_id))
            selected_question = build_question_row(selected_row)
            selected_reason = review_reason(selected_question)

            if selected_reason:
                st.warning(selected_reason)
            else:
                st.info("Nessun problema automatico rilevato, ma puoi comunque modificare il testo.")

            with st.form("edit_question_form"):
                edited_domanda = st.text_area(
                    "Testo domanda",
                    value=selected_question["domanda"],
                    height=120,
                )
                edited_risposta = st.text_input(
                    "Risposta corretta",
                    value=selected_question["risposta_corretta"],
                )
                edited_opzioni = st.text_area(
                    "Distrattori / opzioni errate, una per riga",
                    value="\n".join(selected_question["opzioni"]),
                    height=160,
                )
                save_changes = st.form_submit_button("Salva modifiche")

            if save_changes:
                cleaned_options = [line.strip() for line in edited_opzioni.splitlines() if line.strip()]
                if not edited_domanda.strip():
                    st.error("Il testo della domanda non può essere vuoto.")
                elif not edited_risposta.strip():
                    st.error("La risposta corretta non può essere vuota.")
                else:
                    update_question_persistence(
                        connection,
                        selected_question["id"],
                        edited_domanda.strip(),
                        edited_risposta.strip(),
                        cleaned_options,
                    )
                    st.success("Domanda aggiornata nel JSON e nel database.")
                    st.rerun()


if __name__ == "__main__":
    main()