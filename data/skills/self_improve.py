"""Skill: self_improve
Description: Self-critique and learn. Critique a past answer, extract a durable lesson, store it, and produce an improved answer plus alternatives. Modes: critique (default), recall, list.
"""


def run(**kwargs) -> str:
    mode = (kwargs.get("mode") or "critique").lower()

    from shelby.selfcritique import all_lessons, critique_and_learn, recall_lessons

    # ── Recall: surface relevant past lessons before answering something new ──
    if mode == "recall":
        query = kwargs.get("query") or kwargs.get("question") or ""
        hits = recall_lessons(query, int(kwargs.get("n", 5)))
        if not hits:
            return "No relevant past lessons found yet."
        lines = ["🧭 Relevant lessons from past mistakes:"]
        for h in hits:
            lines.append(f"  • {h['lesson']}")
        return "\n".join(lines)

    # ── List: dump the full learning record ──────────────────────────────────
    if mode == "list":
        lessons = all_lessons()
        if not lessons:
            return "No lessons recorded yet. Run self_improve after answering to start learning."
        lines = [f"📚 {len(lessons)} lesson(s) learned so far:"]
        for i, x in enumerate(lessons[-15:], 1):
            r = f" (rated {x['rating']}/10)" if x.get("rating") else ""
            lines.append(f"  {i}. {x['lesson']}{r}")
        return "\n".join(lines)

    # ── Critique (default): review a Q/A, learn, improve ─────────────────────
    question = kwargs.get("question") or kwargs.get("q") or ""
    answer = kwargs.get("answer") or kwargs.get("a") or ""
    feedback = kwargs.get("feedback") or kwargs.get("user_feedback") or ""

    if not question and not answer:
        return ("self_improve needs something to critique. Pass question=… and answer=… "
                "(optionally feedback=…), or use mode='recall' / mode='list'.")

    r = critique_and_learn(question, answer, feedback)

    lines = ["🔎 SELF-CRITIQUE"]
    if r.get("rating") is not None:
        lines.append(f"Original answer rated: {r['rating']}/10")
    weaknesses = r.get("weaknesses") or []
    if weaknesses:
        lines.append("\nWeaknesses:")
        for w in weaknesses:
            lines.append(f"  ✗ {w}")
    if r.get("lesson"):
        lines.append(f"\n📌 Lesson learned & stored: {r['lesson']}")
    if r.get("improved_answer"):
        lines.append(f"\n✅ Improved answer:\n{r['improved_answer']}")
    alts = r.get("alternatives") or []
    if alts:
        lines.append("\n🔀 Other possibilities:")
        for a in alts:
            lines.append(f"  • {a}")
    if r.get("_heuristic"):
        lines.append("\n(Note: full LLM critic unavailable — heuristic review only.)")
    return "\n".join(lines)
