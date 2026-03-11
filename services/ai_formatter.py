from __future__ import annotations

import html
import re


def _inline_markup(line: str) -> str:
    escaped = html.escape(line)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def render_ai_html(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("## "):
            out.append(f"<b>{_inline_markup(stripped[3:].strip())}</b>")
            continue
        if stripped.startswith("# "):
            out.append(f"<b>{_inline_markup(stripped[2:].strip())}</b>")
            continue
        if re.match(r"^[-*•]\s+", stripped):
            cleaned = re.sub(r"^[-*•]\s+", "", stripped)
            out.append(f"• {_inline_markup(cleaned)}")
            continue
        if re.match(r"^\d+[.)]\s+", stripped):
            out.append(_inline_markup(stripped))
            continue
        out.append(_inline_markup(line))
    return "\n".join(out)


def build_ai_answer_message(text: str) -> str:
    body = render_ai_html(text).strip()
    if not body:
        return "<b>🤖 Ответ ИИ</b>"
    return f"<b>🤖 Ответ ИИ</b>\n\n{body}"
