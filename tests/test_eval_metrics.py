"""Tests for the D52 regression-report metrics (pure)."""

import pytest

from sidecar.eval import compute_metrics, make_row


def result(type_: str, confidence: float = 0.9, l2: bool = False,
           degraded: bool = False, other_guess: str | None = None):
    return {
        "type": type_, "confidence": confidence, "l2_triggered": l2,
        "degraded": degraded, "other_guess": other_guess,
    }


class TestTop1:
    def test_perfect(self):
        rows = [make_row("a.mp4", "monologue", "confirmed", result("monologue"))]
        m = compute_metrics(rows)
        assert m["top1"] == 1.0

    def test_partial(self):
        rows = [
            make_row("a.mp4", "monologue", "confirmed", result("monologue")),
            make_row("b.mp4", "monologue", "confirmed", result("singing", 0.7)),
        ]
        m = compute_metrics(rows)
        assert m["top1"] == pytest.approx(0.5)

    def test_draft_and_confirmed_reported_separately(self):
        rows = [
            make_row("a.mp4", "monologue", "confirmed", result("monologue")),
            make_row("b.mp4", "monologue", "draft", result("singing")),
        ]
        m = compute_metrics(rows)
        assert m["by_status"]["confirmed"]["top1"] == 1.0
        assert m["by_status"]["draft"]["top1"] == 0.0
        assert m["top1"] == pytest.approx(0.5)  # overall includes drafts


class TestPerClassPR:
    def test_precision_recall(self):
        rows = [
            # monologue: 2 predicted, 1 correct -> P=0.5; 1 expected, 1 hit -> R=1.0
            make_row("a.mp4", "monologue", "confirmed", result("monologue")),
            make_row("b.mp4", "singing", "confirmed", result("monologue")),
            # singing: 2 expected, 0 predicted correct -> R=0
            make_row("c.mp4", "singing", "confirmed", result("dance")),
        ]
        m = compute_metrics(rows)
        assert m["per_class"]["monologue"]["precision"] == pytest.approx(0.5)
        assert m["per_class"]["monologue"]["recall"] == 1.0
        assert m["per_class"]["singing"]["recall"] == 0.0
        assert m["per_class"]["singing"]["support"] == 2

    def test_classes_predicted_but_never_expected(self):
        rows = [make_row("a.mp4", "monologue", "confirmed", result("other"))]
        m = compute_metrics(rows)
        assert m["per_class"]["other"]["precision"] == 0.0  # predicted, all wrong


class TestConfusionMatrix:
    def test_counts(self):
        rows = [
            make_row("a.mp4", "monologue", "confirmed", result("monologue")),
            make_row("b.mp4", "monologue", "confirmed", result("interview")),
            make_row("c.mp4", "singing", "confirmed", result("monologue")),
        ]
        m = compute_metrics(rows)
        cm = m["confusion"]
        assert cm["monologue"]["monologue"] == 1
        assert cm["monologue"]["interview"] == 1
        assert cm["singing"]["monologue"] == 1


class TestErrorsAndExtras:
    def test_error_list_content(self):
        rows = [make_row("b.mp4", "monologue", "draft", result("singing", 0.42, l2=True))]
        m = compute_metrics(rows)
        assert len(m["errors"]) == 1
        err = m["errors"][0]
        assert err["file"] == "b.mp4"
        assert err["expected"] == "monologue" and err["got"] == "singing"
        assert err["confidence"] == pytest.approx(0.42)
        assert err["l2_triggered"] is True

    def test_l2_trigger_rate(self):
        rows = [
            make_row("a.mp4", "monologue", "confirmed", result("monologue", l2=True)),
            make_row("b.mp4", "dance", "confirmed", result("dance", l2=False)),
            make_row("c.mp4", "singing", "confirmed", result("singing", l2=True)),
            make_row("d.mp4", "other", "confirmed", result("other", l2=False)),
        ]
        assert compute_metrics(rows)["l2_trigger_rate"] == pytest.approx(0.5)

    def test_other_guess_clustering(self):
        rows = [
            make_row("a.mp4", "other", "confirmed", result("other", other_guess="asmr eating")),
            make_row("b.mp4", "other", "confirmed", result("other", other_guess="asmr eating")),
            make_row("c.mp4", "monologue", "confirmed", result("monologue")),
        ]
        m = compute_metrics(rows)
        assert m["other_guess"]["asmr eating"] == ["a.mp4", "b.mp4"]

    def test_degraded_count(self):
        rows = [
            make_row("a.mp4", "monologue", "confirmed", result("monologue", degraded=True)),
            make_row("b.mp4", "dance", "confirmed", result("dance")),
        ]
        assert compute_metrics(rows)["degraded_count"] == 1

    def test_l2_low_confidence_all_correct_passes(self):
        # Acceptance (docs/02): videos that triggered L2 must all end correct.
        rows = [
            make_row("a.mp4", "monologue", "confirmed", result("monologue", 0.99, l2=True)),
            make_row("b.mp4", "dance", "confirmed", result("dance", 0.9, l2=True)),
        ]
        m = compute_metrics(rows)
        assert m["l2_all_correct"] is True
        rows.append(make_row("c.mp4", "singing", "confirmed", result("other", 0.6, l2=True)))
        assert compute_metrics(rows)["l2_all_correct"] is False
