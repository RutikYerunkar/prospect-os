"""Live Mode providers (Checkpoint G): real OpenAI LLM + fixture-backed
search — `LIVE LLM · FIXTURE SEARCH`. Never imported on the pure-Demo-Mode
path (see `providers/registry.py`'s lazy import) so a public clone with no
`OPENAI_API_KEY` never needs the `openai` SDK installed to run Demo Mode.
"""
