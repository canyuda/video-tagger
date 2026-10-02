"""Analyzer pure-layer tests: transcript formatting, message assembly,
report validation, markdown rendering."""

import json

import pytest
from pydantic import ValidationError

from sidecar.analyzer.markdown import report_to_markdown
from sidecar.analyzer.prompts import (
    REPORT_JSON_SCHEMA,
    Report,
    analysis_messages,
    format_transcript,
)

SAMPLE_REPORT = {
    "title": "AI 涨粉方法论",
    "summary": "博主分享三步起号方法。",
    "chapters": [
        {"title": "定位", "start": 0, "end": 45, "summary": "选赛道"},
        {"title": "实操", "start": 45, "end": 180, "summary": "批量产出"},
    ],
    "viewpoints": [
        {"topic": "起号", "claims": [{"text": "先做账号定位", "time": 12}]},
    ],
    "todos": [{"text": "注册账号", "time": 30, "context": "第一步"}],
    "quotes": [{"text": "流量是设计出来的", "time": 95}],
    "entities": ["抖音", "AI"],
    "qa": [{"q": "多久见效", "a": "两周"}],
    "meta": {"model": "qwen3-vl-flash", "template": "通用总结", "duration": 185},
}


class TestTranscriptFormat:
    def test_sentence_lines(self):
        sentences = [
            {"text": "你好", "begin_ms": 0, "end_ms": 1500},
            {"text": "世界", "begin_ms": 61000, "end_ms": 62500},
        ]
        lines = format_transcript(sentences).splitlines()
        assert lines[0] == "[00:00-00:01] 你好"
        assert lines[1] == "[01:01-01:02] 世界"

    def test_hours(self):
        line = format_transcript([{"text": "x", "begin_ms": 3600000, "end_ms": 3601000}])
        assert "[1:00:00-1:00:01]" in line


class TestMessages:
    def test_layers_and_frames(self):
        messages = analysis_messages(
            transcript_text="[00:00-00:02] 大家好",
            frames=[(3.0, b"\xff\xd8fake"), (10.0, b"\xff\xd8fake")],
            duration_s=20.0,
            template_name="通用总结",
            template_instructions="输出侧重要点",
            user_instructions="重点看第二步",
        )
        assert messages[0]["role"] == "system"
        text = messages[1]["content"][0]["text"]
        assert "分析模板：通用总结" in text
        assert "用户附加指令：重点看第二步" in text
        images = [p for p in messages[1]["content"] if p["type"] == "image_url"]
        assert len(images) == 2
        labels = [p["text"] for p in messages[1]["content"] if p["type"] == "text"]
        assert "[t=00:03]" in labels

    def test_empty_transcript_note(self):
        messages = analysis_messages(
            transcript_text="", frames=[], duration_s=10.0,
            template_name="t", template_instructions="",
        )
        assert "无语音内容" in messages[1]["content"][0]["text"]


class TestReportSchema:
    def test_valid_report_parses(self):
        report = Report.model_validate(SAMPLE_REPORT)
        assert report.title == "AI 涨粉方法论"
        assert len(report.chapters) == 2

    def test_missing_required_rejected(self):
        with pytest.raises(ValidationError):
            Report.model_validate({"title": "x"})

    def test_schema_json_dumpable(self):
        json.dumps(REPORT_JSON_SCHEMA)


class TestMarkdown:
    def test_render_contains_all_sections(self):
        md = report_to_markdown(SAMPLE_REPORT)
        for needle in ("# AI 涨粉方法论", "## 摘要", "## 章节", "## 观点",
                       "- [ ] `00:30` 注册账号", "> 01:35 流量是设计出来的",
                       "## 实体", "## 问答"):
            assert needle in md, f"missing {needle}"

    def test_empty_sections_omitted(self):
        md = report_to_markdown({"title": "t", "summary": "s"})
        assert "## 章节" not in md
        assert "## 摘要" in md
