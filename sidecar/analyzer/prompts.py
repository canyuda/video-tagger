"""Analyzer prompts: fixed system layer + template layer + user instructions
(docs/03 §4), report JSON Schema (docs/03 §6), transcript formatting."""

from __future__ import annotations

import base64
import json

from pydantic import BaseModel, Field

SYSTEM_LAYER = """\
You are a rigorous video content analyst. You receive a timestamped transcript \
(sentences with [mm:ss-mm:ss] markers) and sampled keyframes (each labeled \
[t=mm:ss]) from ONE video, plus analysis instructions.

Hard rules:
- Every conclusion must carry a time reference (mm:ss) or frame reference when \
the content supports it; do not invent anchors.
- Never state anything not present in the transcript or frames. Missing \
information is omitted, not guessed.
- When the frames contain burned-in subtitles, treat them as a content source \
and merge them into your understanding, in time order.
- Output ONE JSON object exactly matching the given schema, in Chinese."""

REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "视频标题/主题"},
        "summary": {"type": "string", "description": "150–300字摘要"},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "summary": {"type": "string"},
                },
                "required": ["title", "start", "end", "summary"],
            },
        },
        "viewpoints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "time": {"type": "number"},
                            },
                            "required": ["text", "time"],
                        },
                    },
                },
                "required": ["topic", "claims"],
            },
        },
        "todos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "time": {"type": "number"},
                    "context": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "time": {"type": "number"}},
                "required": ["text", "time"],
            },
        },
        "entities": {"type": "array", "items": {"type": "string"}},
        "qa": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                "required": ["q", "a"],
            },
        },
        "meta": {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "template": {"type": "string"},
                "duration": {"type": "number"},
            },
        },
    },
    "required": ["title", "summary", "chapters", "viewpoints", "todos", "quotes"],
}


class Report(BaseModel):
    title: str
    summary: str
    chapters: list[dict] = Field(default_factory=list)
    viewpoints: list[dict] = Field(default_factory=list)
    todos: list[dict] = Field(default_factory=list)
    quotes: list[dict] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    qa: list[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


def format_transcript(sentences: list[dict]) -> str:
    """Sentences [{text, begin_ms, end_ms}] -> '[mm:ss-mm:ss] text' lines."""
    lines = []
    for sentence in sentences:
        begin = _mmss(sentence.get("begin_ms", 0) / 1000)
        end = _mmss(sentence.get("end_ms", 0) / 1000)
        lines.append(f"[{begin}-{end}] {sentence.get('text', '').strip()}")
    return "\n".join(lines)


def _mmss(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def analysis_messages(
    *,
    transcript_text: str,
    frames: list[tuple[float, bytes]],
    duration_s: float,
    template_name: str,
    template_instructions: str,
    user_instructions: str = "",
    metadata: dict | None = None,
) -> list[dict]:
    """Assemble the three prompt layers (docs/03 §4) into chat messages with
    interleaved [t=mm:ss] frame labels."""
    meta_text = json.dumps(metadata, ensure_ascii=False) if metadata else "无"
    text_parts = [
        f"视频时长 {duration_s:.0f} 秒；平台元数据：{meta_text}。",
        f"分析模板：{template_name}\n模板指令：{template_instructions or '（通用分析）'}",
    ]
    if user_instructions:
        text_parts.append(f"用户附加指令：{user_instructions}")
    text_parts.append("转写（句级时间戳）：")
    text_parts.append(transcript_text or "（无语音内容——请以画面与内嵌字幕为唯一来源）")
    text_parts.append(f"以下是 {len(frames)} 张关键帧（含时间标签）：")

    content: list[dict] = [{"type": "text", "text": "\n".join(text_parts)}]
    for timestamp, jpeg in frames:
        label = _mmss(timestamp)
        content.append({"type": "text", "text": f"[t={label}]"})
        content.append({
            "type": "image_url",
            "image_url": {
                "url": "data:image/jpeg;base64,"
                + base64.b64encode(jpeg).decode("ascii")
            },
        })
    return [
        {"role": "system", "content": SYSTEM_LAYER},
        {"role": "user", "content": content},
    ]
