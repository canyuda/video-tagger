"""Classification pipeline (V1 core, docs/03 §3.0).

Two-layer decision: perception VLM (frames -> English observations) ->
Jev Choice (taxonomy type + probabilities + confidence); L2 audio evidence
on low confidence; automatic degrade to VLM-direct classification when the
decision layer fails (D12).
"""
