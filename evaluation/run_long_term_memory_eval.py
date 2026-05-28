"""
Run a multi-session long-term memory stress test for LEAi.

This script uses Flask's test_client to exercise the existing LEAi app routes:
/start -> /chat -> /load_session -> /chat.

Run from the repository root with:
    python evaluation/run_long_term_memory_eval.py

If app.py is inside a subfolder such as LEAi/, either run from the repo root or set:
    LEAI_APP_DIR=/path/to/master_thesis/LEAi python evaluation/run_long_term_memory_eval.py
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
import time
import traceback
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios.json"
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class AppContext:
    module: Any
    client: Any
    app_dir: Path


def normalize_text(text: str) -> str:
    """Lowercase and remove accents so Noémie and Noemie match consistently."""
    if text is None:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def keyword_group_hit(answer: str, group: Any) -> bool:
    """
    A group is satisfied if the answer contains at least one alias in that group.
    group can be either a string or a list of strings.
    """
    answer_norm = normalize_text(answer)
    if isinstance(group, str):
        aliases = [group]
    else:
        aliases = list(group)

    return any(normalize_text(alias) in answer_norm for alias in aliases)


def grade_answer(answer: str, expected_facts: List[Any]) -> Dict[str, Any]:
    """
    Simple automatic grading for thesis-friendly evaluation.

    recall_score:
      - 1.0 if all expected fact groups are found
      - 0.5 if at least one but not all expected groups are found
      - 0.0 if none are found
    """
    hits = [keyword_group_hit(answer, group) for group in expected_facts]
    hit_count = sum(1 for hit in hits if hit)

    if not expected_facts:
        recall_score = 0.0
    elif hit_count == len(expected_facts):
        recall_score = 1.0
    elif hit_count > 0:
        recall_score = 0.5
    else:
        recall_score = 0.0

    # Partial matches should be manually inspected — the answer may be a valid paraphrase.
    manual_review_needed = recall_score == 0.5

    return {
        "recall_score": recall_score,
        "expected_hits": hits,
        "expected_hit_count": hit_count,
        "expected_total": len(expected_facts),
        "manual_review_needed": manual_review_needed,
    }


def locate_app_dir() -> Path:
    """Find the directory containing app.py.

    The LEAi/ subfolder is searched before the working directory so that the
    current app is picked even when a legacy app.py lives at the repo root.
    """
    env_dir = os.getenv("LEAI_APP_DIR")
    candidates: List[Path] = []

    if env_dir:
        candidates.append(Path(env_dir).expanduser().resolve())

    cwd = Path.cwd().resolve()
    script_parent = Path(__file__).resolve().parent
    repo_guess = script_parent.parent

    candidates.extend(
        [
            cwd / "LEAi",
            cwd / "leai",
            repo_guess / "LEAi",
            repo_guess / "leai",
            cwd,
            repo_guess,
        ]
    )

    for candidate in candidates:
        if (candidate / "app.py").exists():
            return candidate

    searched = "\n".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Could not locate app.py. Run this script from the repo root, or set LEAI_APP_DIR.\n"
        f"Searched:\n{searched}"
    )


def _disable_remote_tracing() -> None:
    """Stop Langfuse / OpenTelemetry from trying to ship spans to cloud.langfuse.com.

    A long evaluation makes hundreds of @observe-decorated calls; if the Langfuse
    endpoint is unreachable (DNS or network), the OTEL export queue blocks the
    main thread and the run stalls. We do not need traces for the thesis run.
    """
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        os.environ.pop(key, None)


# Default in-context history cap (tokens) for the evaluation. Tight enough that
# all facts age out of the agent's context by recall time (the scenario's
# distractor tail is sized to exceed this window).
DEFAULT_CONTEXT_CAP_TOKENS = 3000


def _apply_context_cap_env(cap_tokens: int) -> None:
    """Set LEAI_CONTEXT_CAP_TOKENS before app import so helpers/companion pick it up."""
    os.environ["LEAI_CONTEXT_CAP_TOKENS"] = str(cap_tokens)


def import_leai_app(
    db_path: Optional[Path] = None,
    context_cap_tokens: Optional[int] = None,
) -> AppContext:
    """
    Import app.py and create a Flask test client.

    This changes the working directory to the app directory so relative paths like
    chat.db and templates/static folders behave the same way as when running
    app.py normally.

    If db_path is provided, helpers.DB_PATH is overridden via the LEAI_DB_PATH
    environment variable before app.py is imported, so the evaluation does not
    write into the user's live chat.db.

    If context_cap_tokens is provided, LEAI_CONTEXT_CAP_TOKENS is set so the
    agents' before_model_callback caps in-context history to that budget.
    """
    _disable_remote_tracing()

    if context_cap_tokens is not None:
        _apply_context_cap_env(context_cap_tokens)

    app_dir = locate_app_dir()
    sys.path.insert(0, str(app_dir))
    os.chdir(app_dir)

    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["LEAI_DB_PATH"] = str(db_path)

    module = importlib.import_module("app")
    flask_app = getattr(module, "app")
    flask_app.config.update(TESTING=True)
    client = flask_app.test_client()
    return AppContext(module=module, client=client, app_dir=app_dir)


def set_rag_enabled(enabled: bool) -> None:
    """Toggle the RAG_ENABLED flag in companion at runtime."""
    import companion  # type: ignore
    companion.RAG_ENABLED = enabled


def set_memory_write_enabled(enabled: bool) -> None:
    """Toggle whether run_companion runs the memory-writing pipeline.

    Disabled during the recall phase so recall questions don't mutate the
    persona's stored state, keeping every recall evaluated against the same
    post-build-up snapshot.
    """
    import companion  # type: ignore
    companion.MEMORY_WRITE_ENABLED = enabled


def chat_history_checkpoint(persona_id: str) -> int:
    import helpers  # type: ignore
    return helpers.count_chat_history(persona_id)


def restore_chat_history(persona_id: str, keep_count: int) -> None:
    """Truncate chat_history back to keep_count rows (the post-build-up snapshot)."""
    import helpers  # type: ignore
    helpers.truncate_chat_history(persona_id, keep_count)


def post_json(client: Any, route: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    response = client.post(route, json=payload)
    try:
        data = response.get_json(silent=True) or {}
    except Exception:
        data = {}
    return response.status_code, data


def ensure_default_persona_compatibility(app_module: Any, persona_id: str) -> None:
    """
    The current app starts default conversations without inserting a row in custom_persona.
    However /load_session expects get_custom_persona(persona_id) to return something, and
    some memory update branches expect custom_personas[persona_id] to exist.

    This helper keeps the evaluation focused on memory persistence instead of crashing on
    that default-session edge case.
    """
    default_persona = getattr(app_module, "LEAI_PERSONALITY", "You are LEAi, a supportive AI companion.")

    if hasattr(app_module, "custom_personas"):
        app_module.custom_personas.setdefault(persona_id, default_persona)

    if hasattr(app_module, "user_information"):
        app_module.user_information.setdefault(persona_id, "")

    if hasattr(app_module, "memories"):
        app_module.memories.setdefault(persona_id, [])

    if hasattr(app_module, "save_persona"):
        try:
            app_module.save_persona(persona_id, default_persona)
        except Exception:
            # This is only a compatibility step. Do not stop the full evaluation if it fails.
            pass

    if hasattr(app_module, "save_user_info"):
        try:
            app_module.save_user_info(persona_id, "")
        except Exception:
            pass


def start_default_conversation(ctx: AppContext) -> str:
    status, data = post_json(ctx.client, "/start", {"custom": False})
    if status >= 400 or "persona_id" not in data:
        raise RuntimeError(f"/start failed with status {status}: {data}")

    persona_id = data["persona_id"]
    ensure_default_persona_compatibility(ctx.module, persona_id)
    return persona_id


# Transient upstream failures we want to retry through. Gemini 503 overload is the
# main one, and it currently masquerades as AttributeError because google-genai
# 1.x references aiohttp.ClientConnectorDNSError which is missing in aiohttp<3.10.6.
_TRANSIENT_MARKERS = (
    "ClientConnectorDNSError",
    "503",
    "UNAVAILABLE",
    "ServerError",
    "Connection aborted",
    "RemoteDisconnected",
    "ReadTimeout",
    "Timeout",            # google-genai DeadlineExceeded / RetryError timeouts
    "DeadlineExceeded",
    "failed to connect",  # gRPC 'failed to connect to all addresses'
    "handshaker shutdown",
)


def _is_transient(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}"
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def send_chat_message(
    ctx: AppContext,
    persona_id: str,
    message: str,
    max_attempts: int = 6,
    backoff_seconds: float = 8.0,
) -> Dict[str, Any]:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            status, data = post_json(ctx.client, "/chat", {"persona_id": persona_id, "message": message})
            if status >= 400:
                raise RuntimeError(f"/chat failed with status {status} for message {message!r}: {data}")
            if "error" in data:
                raise RuntimeError(f"/chat returned error for message {message!r}: {data}")
            return data
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts and _is_transient(exc):
                wait = backoff_seconds * (2 ** (attempt - 1))
                print(f"    transient error on attempt {attempt}/{max_attempts}: "
                      f"{type(exc).__name__}: {exc}. Retrying in {wait:.0f}s.")
                time.sleep(wait)
                continue
            raise
    raise last_exc  # unreachable, but appeases the type checker


def load_session(ctx: AppContext, persona_id: str) -> Dict[str, Any]:
    status, data = post_json(ctx.client, "/load_session", {"persona_id": persona_id})
    if status >= 400 or "error" in data:
        raise RuntimeError(f"/load_session failed with status {status}: {data}")
    return data


def get_stored_memories(app_module: Any, persona_id: str) -> List[str]:
    # Persistence lives in helpers.py (SQLite); companion.py holds the in-memory mirror.
    try:
        import helpers  # type: ignore
        return list(helpers.get_memories(persona_id))
    except Exception:
        pass

    try:
        import companion  # type: ignore
        return list(getattr(companion, "_memories", {}).get(persona_id, []))
    except Exception:
        pass

    # Legacy fallback.
    try:
        if hasattr(app_module, "get_memories"):
            return list(app_module.get_memories(persona_id))
    except Exception:
        pass
    try:
        return list(getattr(app_module, "memories", {}).get(persona_id, []))
    except Exception:
        return []


def probe_rag(ctx: AppContext, persona_id: str, recall_question: str) -> Dict[str, Any]:
    """
    Probe whether the app's RAG layer would retrieve memories for the recall question.
    Mirrors the retrieval logic before the final question is sent.
    """
    try:
        import companion  # type: ignore

        idx = getattr(companion, "_emb_index", {}).get(persona_id)
        mems = getattr(companion, "_memories", {}).get(persona_id, [])

        if idx is None or getattr(idx, "ntotal", 0) == 0 or not mems:
            return {"rag_probe_used": False, "rag_probe_indexes": [], "rag_probe_error": None}

        top_k = getattr(companion, "RAG_TOP_K", 3)
        k = min(top_k, len(mems))
        D, I = companion._embedder.similarity_search(recall_question, idx, k=k)
        threshold = getattr(companion, "SIMILARITY_RETRIEVAL_THRESHOLD", 0.6)

        used_indexes = [
            int(i)
            for d, i in zip(D, I)
            if d > threshold and 0 <= int(i) < len(mems)
        ]
        return {
            "rag_probe_used": bool(used_indexes),
            "rag_probe_indexes": used_indexes,
            "rag_probe_error": None,
        }
    except Exception as exc:
        return {
            "rag_probe_used": None,
            "rag_probe_indexes": [],
            "rag_probe_error": str(exc),
        }


def iter_messages(scenario: Dict[str, Any], max_distractors: Optional[int] = None) -> Iterable[Tuple[str, str]]:
    """Yield (type, text) tuples for the build-up phase.

    Supports two scenario schemas:
    - New: ``messages: [{"type": "fact"|"distractor", "text": "..."}]`` (ordered).
    - Legacy: ``fact_messages: [...]`` + ``distractor_messages: [...]`` (facts then distractors).
    """
    ordered = scenario.get("messages")
    if ordered:
        distractor_count = 0
        for entry in ordered:
            msg_type = entry.get("type", "distractor")
            text = entry.get("text", "")
            if msg_type == "distractor":
                if max_distractors is not None and distractor_count >= max_distractors:
                    continue
                distractor_count += 1
            yield msg_type, text
        return

    for msg in scenario.get("fact_messages", []):
        yield "fact", msg

    distractors = scenario.get("distractor_messages", [])
    if max_distractors is not None:
        distractors = distractors[:max_distractors]

    for msg in distractors:
        yield "distractor", msg


def _iter_recall_questions(scenario: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize 'recall_question' (singular) or 'recall_questions' (plural) to a list."""
    if "recall_questions" in scenario:
        out = []
        for i, rq in enumerate(scenario["recall_questions"]):
            out.append({
                "id": rq.get("id", f"recall_{i+1}"),
                "fact_type": rq.get("fact_type", "unspecified"),
                "question": rq["question"],
                "expected_facts": rq.get("expected_facts", []),
            })
        return out
    # Legacy single-question format.
    return [{
        "id": "recall",
        "fact_type": scenario.get("fact_type", "unspecified"),
        "question": scenario.get("recall_question", ""),
        "expected_facts": scenario.get("expected_facts", []),
    }]


def _ask_recall(
    ctx: AppContext,
    persona_id: str,
    recall: Dict[str, Any],
    rag_enabled: bool,
    chat_checkpoint: int,
) -> Dict[str, Any]:
    """Reset session, toggle RAG, send the recall question, grade the answer.

    Memory writes are disabled for the duration so the recall question doesn't
    mutate stored state, and chat_history is truncated back to chat_checkpoint
    afterwards so the next phase starts from the same post-build-up snapshot.
    """
    set_rag_enabled(rag_enabled)
    set_memory_write_enabled(False)
    load_session_error = None
    try:
        load_session(ctx, persona_id)
    except Exception as exc:
        load_session_error = str(exc)

    rag_probe = probe_rag(ctx, persona_id, recall["question"])

    try:
        answer_data = send_chat_message(ctx, persona_id, recall["question"])
        answer = answer_data.get("reply", "")
        chat_error = None
    except Exception as exc:
        answer = ""
        chat_error = str(exc)

    # Roll chat_history back to the post-build-up snapshot so B and C, and each
    # subsequent recall question, start from identical state.
    try:
        restore_chat_history(persona_id, chat_checkpoint)
    except Exception:
        pass
    set_memory_write_enabled(True)

    grade = grade_answer(
        answer=answer,
        expected_facts=recall.get("expected_facts", []),
    )
    return {
        "answer": answer,
        "chat_error": chat_error,
        "load_session_error": load_session_error,
        "rag_probe_used": rag_probe["rag_probe_used"],
        "rag_probe_indexes": rag_probe["rag_probe_indexes"],
        "rag_probe_error": rag_probe["rag_probe_error"],
        "recall_score": grade["recall_score"],
        "expected_hits": grade["expected_hits"],
        "expected_hit_count": grade["expected_hit_count"],
        "expected_total": grade["expected_total"],
        "manual_review_needed": grade["manual_review_needed"],
    }


def run_scenario(
    ctx: AppContext,
    scenario: Dict[str, Any],
    max_distractors: Optional[int] = None,
    partial_save_path: Optional[Path] = None,
) -> Dict[str, Any]:
    persona_id = start_default_conversation(ctx)

    # ── Build-up phase: send facts + distractors in order ────────────────────────
    sent_messages = []
    msgs = list(iter_messages(scenario, max_distractors=max_distractors))
    total_build = len(msgs)
    for idx, (message_type, message) in enumerate(msgs, start=1):
        send_chat_message(ctx, persona_id, message)
        sent_messages.append({"type": message_type, "message": message})
        if idx % 25 == 0 or idx == total_build:
            print(f"    build-up {idx}/{total_build} turns sent")

    memories_after_buildup = get_stored_memories(ctx.module, persona_id)

    # Checkpoint chat_history length after build-up; each recall phase rolls back
    # to this so B and C (and successive questions) start from identical state.
    chat_checkpoint = chat_history_checkpoint(persona_id)

    # ── Recall phase: B (no RAG) vs C (with RAG) per question ────────────────────
    recall_questions = _iter_recall_questions(scenario)
    recall_results: List[Dict[str, Any]] = []
    for ri, recall in enumerate(recall_questions, start=1):
        rb = _ask_recall(ctx, persona_id, recall, rag_enabled=False, chat_checkpoint=chat_checkpoint)
        rc = _ask_recall(ctx, persona_id, recall, rag_enabled=True, chat_checkpoint=chat_checkpoint)
        recall_results.append({
            "recall_id": recall["id"],
            "fact_type": recall.get("fact_type", "unspecified"),
            "question": recall["question"],
            "expected_facts": recall["expected_facts"],
            "phase_b_no_rag": rb,
            "phase_c_with_rag": rc,
        })
        print(f"    recall {ri}/{len(recall_questions)} [{recall['id']}] "
              f"B={rb['recall_score']:.1f} C={rc['recall_score']:.1f}")
        # Incremental save so a mid-run interruption doesn't lose recall progress.
        if partial_save_path is not None:
            try:
                write_json(partial_save_path, {
                    "scenario_id": scenario.get("id"),
                    "persona_id": persona_id,
                    "completed_recalls": len(recall_results),
                    "total_recalls": len(recall_questions),
                    "recall_results": recall_results,
                })
            except Exception:
                pass

    # Restore default so later scenarios (if any) start clean.
    set_rag_enabled(True)

    # Aggregate stats for this scenario.
    b_scores = [r["phase_b_no_rag"]["recall_score"] for r in recall_results]
    c_scores = [r["phase_c_with_rag"]["recall_score"] for r in recall_results]
    avg_b = sum(b_scores) / len(b_scores) if b_scores else 0.0
    avg_c = sum(c_scores) / len(c_scores) if c_scores else 0.0

    return {
        "scenario_id": scenario.get("id"),
        "category": scenario.get("category"),
        "persona_id": persona_id,
        "system_name": "LEAi memory/RAG",
        "messages_sent_count": len(sent_messages),
        "context_cap_tokens": int(os.environ.get("LEAI_CONTEXT_CAP_TOKENS", "0")),
        "memories_after_buildup": memories_after_buildup,
        "memory_count_after_buildup": len(memories_after_buildup),
        "recall_results": recall_results,
        "avg_recall_b_no_rag": avg_b,
        "avg_recall_c_with_rag": avg_c,
        "rag_marginal_gain": avg_c - avg_b,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, results: List[Dict[str, Any]]) -> None:
    """One row per recall question, with B and C phase scores side-by-side."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "scenario_id",
        "recall_id",
        "question",
        "expected_facts",
        "context_cap_tokens",
        "memory_count_after_buildup",
        "answer_b_no_rag",
        "recall_score_b_no_rag",
        "answer_c_with_rag",
        "recall_score_c_with_rag",
        "rag_marginal_gain",
        "rag_probe_used_c",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for scenario_result in results:
            for r in scenario_result.get("recall_results", []):
                b = r["phase_b_no_rag"]
                c = r["phase_c_with_rag"]
                writer.writerow({
                    "scenario_id": scenario_result.get("scenario_id"),
                    "recall_id": r.get("recall_id"),
                    "question": r.get("question"),
                    "expected_facts": json.dumps(r.get("expected_facts"), ensure_ascii=False),
                    "context_cap_tokens": scenario_result.get("context_cap_tokens"),
                    "memory_count_after_buildup": scenario_result.get("memory_count_after_buildup"),
                    "answer_b_no_rag": b.get("answer"),
                    "recall_score_b_no_rag": b.get("recall_score"),
                    "answer_c_with_rag": c.get("answer"),
                    "recall_score_c_with_rag": c.get("recall_score"),
                    "rag_marginal_gain": c.get("recall_score", 0) - b.get("recall_score", 0),
                    "rag_probe_used_c": c.get("rag_probe_used"),
                })


def build_summary(results: List[Dict[str, Any]], scenarios_path: Path, app_dir: Path) -> str:
    # Flatten per-recall data across all scenarios.
    flat = []
    for sr in results:
        for r in sr.get("recall_results", []):
            flat.append({
                "scenario_id": sr.get("scenario_id"),
                "recall_id": r.get("recall_id"),
                "question": r.get("question"),
                "b": r.get("phase_b_no_rag", {}),
                "c": r.get("phase_c_with_rag", {}),
            })

    n = len(flat)
    avg_b = sum(f["b"].get("recall_score", 0.0) for f in flat) / n if n else 0.0
    avg_c = sum(f["c"].get("recall_score", 0.0) for f in flat) / n if n else 0.0
    rag_fired_c = sum(1 for f in flat if f["c"].get("rag_probe_used") is True)

    total_facts_stored = sum(sr.get("memory_count_after_buildup", 0) for sr in results)
    cap_tokens = results[0].get("context_cap_tokens") if results else None

    lines = []
    lines.append("# Long-Term Memory Stress Test — B (no RAG) vs C (with RAG)")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Scenarios file: `{scenarios_path}`")
    lines.append(f"App directory: `{app_dir}`")
    lines.append(f"Context cap (tokens): {cap_tokens}")
    lines.append("")
    lines.append("## Aggregate results")
    lines.append("")
    lines.append(f"- Scenarios run: {len(results)}")
    lines.append(f"- Recall questions evaluated: {n}")
    lines.append(f"- Facts stored in FAISS across all scenarios: {total_facts_stored}")
    lines.append("")
    lines.append(f"- **Average recall — B (no RAG, profile only):** {avg_b:.2f}")
    lines.append(f"- **Average recall — C (with RAG):** {avg_c:.2f}")
    lines.append(f"- **RAG marginal gain (C − B):** {avg_c - avg_b:+.2f}")
    lines.append("")
    lines.append(f"- RAG probe positive at recall time (C): {rag_fired_c} / {n}")
    lines.append("")

    # ── Breakdown by fact type (user-self vs third-party) ───────────────────────
    type_groups: Dict[str, List[tuple]] = {}
    for sr in results:
        for rq in sr.get("recall_results", []):
            t = rq.get("fact_type", "unspecified")
            b = rq.get("phase_b_no_rag", {}).get("recall_score", 0.0)
            c = rq.get("phase_c_with_rag", {}).get("recall_score", 0.0)
            type_groups.setdefault(t, []).append((b, c))

    if len(type_groups) > 1 or "unspecified" not in type_groups:
        lines.append("## Breakdown by fact type")
        lines.append("")
        lines.append("| Fact type | Questions | B (no RAG) | C (with RAG) | Δ |")
        lines.append("|---|---:|---:|---:|---:|")
        for t in sorted(type_groups):
            items = type_groups[t]
            tb = sum(x[0] for x in items) / len(items)
            tc = sum(x[1] for x in items) / len(items)
            lines.append(f"| {t} | {len(items)} | {tb:.2f} | {tc:.2f} | {tc - tb:+.2f} |")
        lines.append("")

    lines.append("## Per-recall comparison")
    lines.append("")
    lines.append("| Scenario | Recall ID | Question | B (no RAG) | C (with RAG) | Δ |")
    lines.append("|---|---|---|---:|---:|---:|")
    for f in flat:
        b_score = float(f["b"].get("recall_score", 0.0))
        c_score = float(f["c"].get("recall_score", 0.0))
        q = (f["question"] or "")[:60]
        lines.append(
            f"| {f['scenario_id']} | {f['recall_id']} | {q} | "
            f"{b_score:.1f} | {c_score:.1f} | {c_score - b_score:+.1f} |"
        )

    lines.append("")
    lines.append("## Thesis-ready interpretation draft")
    lines.append("")
    lines.append(
        f"This evaluation measured LEAi's long-term recall under a {cap_tokens}-token "
        "in-context history cap, simulating a cost-bounded production setting where "
        "older conversation turns can no longer fit in the agent's context. Each recall "
        "question was asked twice: once with the FAISS retrieval tool disabled (B — the "
        "agent answers from the distilled `user_information` profile alone), and once with "
        "it enabled (C — full LEAi memory stack)."
    )
    lines.append("")
    lines.append(
        f"Across {n} recall questions, the no-RAG configuration achieved an average recall "
        f"of {avg_b:.2f}; with RAG enabled, average recall was {avg_c:.2f}. The marginal "
        f"gain from RAG was {avg_c - avg_b:+.2f}. The RAG probe found a sufficiently "
        f"similar stored memory for {rag_fired_c} of {n} questions at recall time."
    )
    lines.append("")
    lines.append("## Detailed answers")
    lines.append("")
    for f in flat:
        lines.append(f"### {f['scenario_id']} — {f['recall_id']}")
        lines.append(f"**Q:** {f['question']}")
        lines.append("")
        lines.append(f"**B (no RAG, score {f['b'].get('recall_score')}):** {f['b'].get('answer', '')}")
        lines.append("")
        lines.append(f"**C (with RAG, score {f['c'].get('recall_score')}):** {f['c'].get('answer', '')}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LEAi long-term memory stress evaluation.")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS_PATH, help="Path to scenarios.json")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR, help="Directory for result files")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N scenarios")
    parser.add_argument(
        "--max-distractors",
        type=int,
        default=None,
        help="Use only the first N distractor messages per scenario for quick debugging",
    )
    parser.add_argument("--stop-on-error", action="store_true", help="Stop at the first scenario error")
    parser.add_argument(
        "--only-ids",
        nargs="+",
        default=None,
        help="Only run scenarios whose 'id' matches one of these. Combine with --merge "
        "to keep previous successful results.",
    )
    parser.add_argument(
        "--rerun-errors",
        action="store_true",
        help="Read the existing results file and rerun only the scenarios listed in its "
        "'errors' section. Implies --merge.",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge new results with the existing results file instead of overwriting. "
        "Reruns of an id replace the prior entry for that id.",
    )
    parser.add_argument(
        "--context-cap-tokens",
        type=int,
        default=DEFAULT_CONTEXT_CAP_TOKENS,
        help=f"In-context history cap (tokens). Default: {DEFAULT_CONTEXT_CAP_TOKENS}.",
    )
    args = parser.parse_args()

    scenarios_path = args.scenarios.resolve()
    scenarios = json.loads(scenarios_path.read_text(encoding="utf-8"))

    results_dir = args.results_dir.resolve()
    eval_db_path = results_dir / "eval_chat.db"
    results_json_path = results_dir / "long_term_memory_results.json"

    only_ids: Optional[List[str]] = list(args.only_ids) if args.only_ids else None

    if args.rerun_errors:
        if not results_json_path.exists():
            raise FileNotFoundError(
                f"--rerun-errors needs an existing {results_json_path}; none found."
            )
        prior = json.loads(results_json_path.read_text(encoding="utf-8"))
        prior_error_ids = [e.get("scenario_id") for e in prior.get("errors", [])]
        if not prior_error_ids:
            print("No errored scenarios in previous run. Nothing to rerun.")
            return 0
        only_ids = prior_error_ids
        args.merge = True
        print(f"Rerunning previously-errored scenarios: {only_ids}")

    if only_ids is not None:
        scenarios = [s for s in scenarios if s.get("id") in only_ids]

    if args.limit is not None:
        scenarios = scenarios[: args.limit]

    ctx = import_leai_app(db_path=eval_db_path, context_cap_tokens=args.context_cap_tokens)
    print(f"Loaded LEAi app from: {ctx.app_dir}")
    print(f"Using evaluation DB:  {eval_db_path}")
    print(f"Context cap (tokens): {args.context_cap_tokens}")
    print(f"Loaded {len(scenarios)} scenarios from: {scenarios_path}")

    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    partial_path = results_dir / "long_term_memory_results.partial.json"

    for i, scenario in enumerate(scenarios, start=1):
        scenario_id = scenario.get("id", f"scenario_{i}")
        print(f"\n[{i}/{len(scenarios)}] Running scenario: {scenario_id}")
        try:
            result = run_scenario(
                ctx, scenario,
                max_distractors=args.max_distractors,
                partial_save_path=partial_path,
            )
            results.append(result)
            print(
                f"  avg_recall B(no-RAG)={result['avg_recall_b_no_rag']:.2f} "
                f"C(with-RAG)={result['avg_recall_c_with_rag']:.2f} "
                f"Δ={result['rag_marginal_gain']:+.2f} "
                f"memories_stored={result['memory_count_after_buildup']}"
            )
        except Exception as exc:
            error = {
                "scenario_id": scenario_id,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            errors.append(error)
            print(f"  ERROR: {exc}")
            if args.stop_on_error:
                break

    results_dir.mkdir(parents=True, exist_ok=True)

    if args.merge and results_json_path.exists():
        prior = json.loads(results_json_path.read_text(encoding="utf-8"))
        new_ids = {r["scenario_id"] for r in results} | {e["scenario_id"] for e in errors}
        merged_results = [r for r in prior.get("results", []) if r.get("scenario_id") not in new_ids]
        merged_errors = [e for e in prior.get("errors", []) if e.get("scenario_id") not in new_ids]
        merged_results.extend(results)
        merged_errors.extend(errors)
        # Preserve scenarios.json order
        order = {s.get("id"): i for i, s in enumerate(json.loads(scenarios_path.read_text(encoding="utf-8")))}
        merged_results.sort(key=lambda r: order.get(r.get("scenario_id"), 9999))
        merged_errors.sort(key=lambda e: order.get(e.get("scenario_id"), 9999))
        results = merged_results
        errors = merged_errors

    write_json(results_json_path, {"results": results, "errors": errors})
    write_csv(results_dir / "long_term_memory_results.csv", results)

    summary = build_summary(results, scenarios_path=scenarios_path, app_dir=ctx.app_dir)
    (results_dir / "summary.md").write_text(summary, encoding="utf-8")

    if errors:
        write_json(results_dir / "errors.json", errors)

    # Final results written successfully — remove the incremental partial file.
    try:
        partial_path.unlink(missing_ok=True)
    except Exception:
        pass

    print("\nDone.")
    print(f"Results JSON: {results_dir / 'long_term_memory_results.json'}")
    print(f"Results CSV:  {results_dir / 'long_term_memory_results.csv'}")
    print(f"Summary MD:   {results_dir / 'summary.md'}")
    if errors:
        print(f"Errors JSON:  {results_dir / 'errors.json'}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
