"""Model access layer (docs/04 §4).

Two provider interfaces:
- chat:    OpenAI-compatible VLM endpoints (DashScope qwen3-vl-flash, Zhipu GLM).
- decision: TypeSafe Jev (state + questions -> typed judgment).

Both go through the shared retry policy (D53) and error taxonomy
(retryable/terminal with code prefixes, docs/04 §7).
"""
