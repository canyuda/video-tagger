"""Wire real providers into a Classifier from Settings (Phase 0 CLI)."""

from __future__ import annotations

from pathlib import Path

from sidecar.cache import FileCache
from sidecar.classify.pipeline import Classifier
from sidecar.config import PROJECT_ROOT, Settings
from sidecar.providers.asr import AsrProvider
from sidecar.providers.chat import ChatProvider
from sidecar.providers.jev import JevClient
from sidecar.taxonomy import Taxonomy, builtin_taxonomy

DATA_ROOT = PROJECT_ROOT / "data"


def build_classifier(
    settings: Settings,
    taxonomy: Taxonomy | None = None,
    *,
    cache_root: Path | None = None,
    work_root: Path | None = None,
    with_asr: bool = True,
    use_cache: bool = True,
) -> Classifier:
    chat = ChatProvider(
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key,
        model=settings.dashscope_vlm_model,
    )
    jev = JevClient(
        base_url=settings.typesafe_base_url,
        api_key=settings.typesafe_api_key,
        model=settings.jev_model,
    )
    asr = (
        AsrProvider(
            # paraformer/uploads live on the native DashScope API, not the
            # OpenAI compatible-mode endpoint the VLM uses.
            base_url=settings.dashscope_native_base_url,
            api_key=settings.dashscope_api_key,
        )
        if with_asr
        else None
    )
    return Classifier(
        settings=settings,
        taxonomy=taxonomy or builtin_taxonomy(),
        chat=chat,
        jev=jev,
        asr=asr,
        cache=FileCache(cache_root or DATA_ROOT / "cache"),
        work_root=work_root or DATA_ROOT / "work",
        use_cache=use_cache,
    )
