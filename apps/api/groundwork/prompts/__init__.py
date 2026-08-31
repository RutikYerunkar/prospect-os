"""Real prompts for the four Live LLM operations (Checkpoint G Phase 2).

Prompts live here, not scattered through `engine/steps/*.py`. Each operation
has a typed input (constructed only from a `ProspectContext`, or — for
`objective_parse` — from the raw user-submitted objective text, since that
call happens before any `Play`/`ProspectContext` exists), a prompt version
string, and a builder that renders a `PromptEnvelope`.

`DemoLLMProvider` never reads `envelope.system`/`envelope.user` — it stays
deterministic by reading only `envelope.metadata`, which every builder here
still populates. `OpenAILLMProvider` reads `system`/`user` and ignores
`metadata`.
"""
