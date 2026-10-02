"""Douyin download adapter (D29): signing core ported from the JS reference
implementation (DouyinVideoDownloader/lib, ~1300 lines) to Python, verified by
对拍 (same inputs -> identical signatures -> same API behavior).

The adapter is wholly replaceable (docs/04 §5): failures only stop the
`downloading` stage and never touch the local-file pipeline.
"""
