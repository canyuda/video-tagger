"""Report -> Markdown renderer (pure)."""

from __future__ import annotations


def _mmss(seconds) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def report_to_markdown(report: dict) -> str:
    lines: list[str] = [f"# {report.get('title', '视频分析报告')}", ""]
    meta = report.get("meta") or {}
    if meta:
        model = meta.get("model", "")
        template = meta.get("template", "")
        duration = meta.get("duration")
        parts = [p for p in [
            f"模型：{model}" if model else "",
            f"模板：{template}" if template else "",
            f"时长：{_mmss(duration)}" if duration else "",
        ] if p]
        if parts:
            lines += [f"> {' · '.join(parts)}", ""]

    summary = report.get("summary")
    if summary:
        lines += ["## 摘要", "", summary, ""]

    chapters = report.get("chapters") or []
    if chapters:
        lines += ["## 章节", ""]
        for chapter in chapters:
            span = f"{_mmss(chapter.get('start'))}–{_mmss(chapter.get('end'))}"
            lines.append(f"- **{chapter.get('title', '')}**（{span}）{chapter.get('summary', '')}")
        lines.append("")

    viewpoints = report.get("viewpoints") or []
    if viewpoints:
        lines += ["## 观点", ""]
        for viewpoint in viewpoints:
            lines.append(f"### {viewpoint.get('topic', '')}")
            for claim in viewpoint.get("claims") or []:
                anchor = _mmss(claim.get("time"))
                anchor_text = f"`{anchor}` " if anchor else ""
                lines.append(f"- {anchor_text}{claim.get('text', '')}")
            lines.append("")

    todos = report.get("todos") or []
    if todos:
        lines += ["## 待办", ""]
        for todo in todos:
            anchor = _mmss(todo.get("time"))
            anchor_text = f"`{anchor}` " if anchor else ""
            context = todo.get("context", "")
            context_text = f"（{context}）" if context else ""
            lines.append(f"- [ ] {anchor_text}{todo.get('text', '')}{context_text}")
        lines.append("")

    quotes = report.get("quotes") or []
    if quotes:
        lines += ["## 金句", ""]
        for quote in quotes:
            lines.append(f"> {_mmss(quote.get('time'))} {quote.get('text', '')}")
        lines.append("")

    entities = report.get("entities") or []
    if entities:
        lines += ["## 实体", "", "、".join(str(e) for e in entities), ""]

    qa = report.get("qa") or []
    if qa:
        lines += ["## 问答", ""]
        for item in qa:
            lines.append(f"**问：**{item.get('q', '')}")
            lines.append(f"**答：**{item.get('a', '')}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
