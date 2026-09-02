"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  NetworkError,
  createPlay,
  getProviderSettings,
  loginOperator,
  logoutOperator,
  previewPlay,
  startRun,
} from "@/lib/api";
import type { Mode, PlaySpec, PlayResponse, ProviderSettingsResponse } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { PlanPanel } from "@/components/PlanPanel";

const DEFAULT_OBJECTIVE =
  "Find AI infrastructure startups that recently raised funding or are expanding their GTM " +
  "teams. Identify the most relevant sales leader, score each company against our ICP, explain " +
  "the evidence, and draft personalized outreach.";

type Phase = "idle" | "parsing" | "starting";

// Checkpoint I1 Phase 9: prefer the API's own (already-safe, already
// specific) `.detail` over a generic "request failed" message — and tell a
// truly unreachable API apart from a request that reached it and failed.
function friendlyErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.detail || fallback;
  if (err instanceof NetworkError) return err.message;
  return fallback;
}

function ChipInput({
  label,
  placeholder,
  chips,
  onChange,
}: {
  label: string;
  placeholder: string;
  chips: string[];
  onChange: (chips: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  function commit() {
    const value = draft.trim();
    if (value && !chips.includes(value)) onChange([...chips, value]);
    setDraft("");
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit();
    } else if (e.key === "Backspace" && draft === "" && chips.length > 0) {
      onChange(chips.slice(0, -1));
    }
  }

  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-zinc-400">{label}</span>
      <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 focus-within:border-indigo-400">
        {chips.map((chip) => (
          <Badge key={chip} tone="indigo" className="gap-1.5">
            {chip}
            <button
              type="button"
              onClick={() => onChange(chips.filter((c) => c !== chip))}
              aria-label={`Remove ${chip}`}
              className="text-indigo-300 hover:text-indigo-100"
            >
              ×
            </button>
          </Badge>
        ))}
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={commit}
          placeholder={chips.length === 0 ? placeholder : ""}
          className="min-w-[8rem] flex-1 bg-transparent py-0.5 text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
        />
      </div>
    </label>
  );
}

export default function NewPlayPage() {
  const router = useRouter();

  const [objective, setObjective] = useState(DEFAULT_OBJECTIVE);
  // "ai_infrastructure" is the exact industry slug the fixture pack's
  // companies and its own canonical PlaySpec use (domain/scoring.py matches
  // industry_fit by exact string, not a fuzzy label) — a human-readable
  // "AI Infrastructure" chip here would silently downgrade every fixture
  // company's industry_fit from a full 1.0 match to a 0.6 adjacent-match.
  const [industries, setIndustries] = useState<string[]>(["ai_infrastructure"]);
  // 50-250 matches the fixture pack's own canonical size band — running the
  // default form reproduces the exact, documented score for every company
  // (e.g. Northwind Labs at 92), not a number that drifts with whatever the
  // form's placeholder bounds happened to be.
  const [sizeMin, setSizeMin] = useState(50);
  const [sizeMax, setSizeMax] = useState(250);
  const [minScore, setMinScore] = useState(60);
  // 7 matches the demo fixture pack's own company count (6 required + the
  // optional Sable Compute) — the discovered count on the run this creates
  // will equal this number exactly, never a surprise +1.
  const [targetCount, setTargetCount] = useState(7);

  // What the plan panel shows — updated by BOTH the non-persisting preview
  // (every debounced edit, Checkpoint I1 Phase 7) and an explicit commit
  // (`handleParseWithModel`/`handleRunAgents`). Only a commit ever creates a
  // real Play row; `committedPlay`/`committedSignatureRef` below track that
  // separately so Run Agents knows whether it can reuse one or must create
  // a fresh one.
  const [displaySpec, setDisplaySpec] = useState<{
    icp_spec: PlaySpec;
    parse_source: "llm" | "deterministic";
  } | null>(null);
  const [committedPlay, setCommittedPlay] = useState<PlayResponse | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);

  // Checkpoint G: Demo/Live selector. Live disables itself (with an
  // explanation) unless a real OpenAI runtime is actually configured —
  // never a silent fallback to Demo.
  const [mode, setMode] = useState<Mode>("demo");
  const [providerSettings, setProviderSettings] = useState<ProviderSettingsResponse | null>(null);
  const [settingsUnreachable, setSettingsUnreachable] = useState(false);
  const [confirmedSpend, setConfirmedSpend] = useState(false);

  // Checkpoint I1 Phase 8/9 — operator unlock.
  const [passphrase, setPassphrase] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [unlockError, setUnlockError] = useState<string | null>(null);

  function loadProviderSettings() {
    getProviderSettings()
      .then((s) => {
        setProviderSettings(s);
        setSettingsUnreachable(false);
      })
      .catch(() => {
        setProviderSettings(null);
        setSettingsUnreachable(true);
      });
  }

  useEffect(() => {
    loadProviderSettings();
  }, []);

  async function handleUnlock(e: FormEvent) {
    e.preventDefault();
    setUnlockError(null);
    setUnlocking(true);
    try {
      await loginOperator({ passphrase });
      setPassphrase("");
      loadProviderSettings(); // refreshes live.is_operator from the API, not assumed locally
    } catch (err) {
      setUnlockError(friendlyErrorMessage(err, "Could not reach the API — is it running?"));
    } finally {
      setUnlocking(false);
    }
  }

  async function handleLockAgain() {
    try {
      await logoutOperator();
    } catch {
      // best-effort — the cookie may already be gone/expired
    }
    loadProviderSettings();
  }

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const parseSeqRef = useRef(0);
  const committedSignatureRef = useRef<string | null>(null);
  const previewAbortRef = useRef<AbortController | null>(null);

  // Preview is unconditionally deterministic and doesn't accept `mode` at
  // all (Checkpoint I1 Phase 7) — a mode-only change shouldn't trigger a
  // fresh preview request. `signature` (mode included) is still what
  // decides whether Run Agents can reuse an already-committed Play.
  const previewSignature = JSON.stringify({ objective, industries, sizeMin, sizeMax, minScore, targetCount });
  const signature = JSON.stringify({ objective, industries, sizeMin, sizeMax, minScore, targetCount, mode });
  const live = providerSettings?.live;
  // Checkpoint I1 Phase 8/9 — three distinct "why can't I use Live" states,
  // never conflated into one generic "unavailable":
  //   1. providersConfigured=false — OPENAI_API_KEY/TAVILY_API_KEY missing
  //      on the API process. Nothing an operator passphrase can fix.
  //   2. operatorLoginConfigured=false — providers ARE configured, but
  //      OPERATOR_PASSPHRASE/SESSION_SIGNING_KEY aren't — Live is
  //      hard-disabled on this deployment, per Checkpoint I1 Phase 8.
  //   3. isOperator=false — everything above is configured; this browser
  //      just hasn't unlocked it yet. The one state an operator passphrase
  //      actually resolves.
  const providersConfigured = live?.available ?? false;
  const operatorLoginConfigured = live?.operator_login_configured ?? false;
  const isOperator = live?.is_operator ?? false;
  const liveSelectable = providersConfigured && operatorLoginConfigured;
  const liveUnlocked = liveSelectable && isOperator;
  // The Live toggle button is itself disabled while not selectable, so
  // `mode` can never be *set* to "live" in that state — this guards only
  // the (currently unreachable, but cheap to guard) case of availability
  // changing after the fact, without an effect that would just be
  // re-deriving state React already has. Deliberately NOT keyed on
  // `isOperator`: selecting Live before unlocking is a real, reachable
  // state (the whole point of the unlock panel below) — it must show the
  // explicit locked state and block submission, never silently fall back
  // to Demo underneath the user.
  const effectiveMode: Mode = mode === "live" && !liveSelectable ? "demo" : mode;

  // The form only exposes four ICP controls (§18) — the rest of the
  // canonical demo ICP (exclusions, funding stage, tech, persona,
  // confidence floor) isn't user-editable here, but still has to be sent so
  // the fixture pack's exclude-list disqualifier (Cobalt Retail Systems'
  // `retail_pos`) actually fires. Without this, a play created from this
  // form never sends `excluded_industries` and Cobalt silently scores PASS
  // instead of the fixture's intended REJECTED — a real demo-consistency
  // bug, not a hypothetical one.
  function overrides() {
    return {
      target_industries: industries,
      excluded_industries: ["retail_pos"],
      adjacent_industries: { data_tooling: ["ai_infrastructure"] },
      size_band_min: sizeMin,
      size_band_max: sizeMax,
      target_funding_stages: ["series_a", "series_b"],
      target_technologies: ["kubernetes", "pytorch", "triton"],
      persona_titles: ["VP of Sales", "Head of Sales", "VP of Revenue"],
      min_score: minScore,
      min_confidence: 0.6,
    };
  }

  // Preview the objective + controls into a structured PlaySpec via the
  // non-persisting `POST /api/plays/preview` (Checkpoint I1 Phase 7) —
  // deterministic, zero DB writes, zero LLM calls, safe to fire on every
  // debounced keystroke in Demo OR Live Mode alike. All setState calls
  // happen inside the debounce timeout callback, never synchronously in the
  // effect body, so this doesn't cascade renders on every keystroke.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const seq = ++parseSeqRef.current;

    debounceRef.current = setTimeout(() => {
      if (seq !== parseSeqRef.current) return;
      if (!objective.trim()) {
        setDisplaySpec(null);
        return;
      }
      // A superseded request (the user kept typing) is aborted outright,
      // not just ignored on return — no reason to let a stale preview
      // finish server-side once a newer edit has already replaced it.
      previewAbortRef.current?.abort();
      const controller = new AbortController();
      previewAbortRef.current = controller;

      setPhase("parsing");
      setError(null);
      previewPlay({ objective, icp_overrides: overrides(), target_count: targetCount }, controller.signal)
        .then((preview) => {
          if (seq !== parseSeqRef.current) return; // superseded by a newer edit
          setDisplaySpec({ icp_spec: preview.icp_spec, parse_source: preview.parse_source });
          setPhase("idle");
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return; // superseded, not a real error
          if (seq !== parseSeqRef.current) return;
          setError(friendlyErrorMessage(err, "Could not parse objective"));
          setPhase("idle");
        });
    }, 600);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewSignature]);

  // Abort any still-in-flight preview request when the form itself goes
  // away (e.g. Run Agents navigates to /runs/[id]) — nothing left to update.
  useEffect(() => {
    return () => {
      previewAbortRef.current?.abort();
    };
  }, []);

  async function handleParseWithModel() {
    setError(null);
    setPhase("parsing");
    try {
      const play = await createPlay({
        objective, icp_overrides: overrides(), target_count: targetCount, mode: "live",
        use_live_objective_parser: true,
      });
      setCommittedPlay(play);
      committedSignatureRef.current = signature;
      setDisplaySpec({ icp_spec: play.icp_spec, parse_source: play.parse_source });
      setPhase("idle");
    } catch (err) {
      setError(friendlyErrorMessage(err, "Live objective parse failed"));
      setPhase("idle");
    }
  }

  async function handleRunAgents() {
    setError(null);
    try {
      let play = committedPlay;
      if (!play || committedSignatureRef.current !== signature) {
        setPhase("parsing");
        play = await createPlay({ objective, icp_overrides: overrides(), target_count: targetCount, mode: effectiveMode });
        setCommittedPlay(play);
        committedSignatureRef.current = signature;
        setDisplaySpec({ icp_spec: play.icp_spec, parse_source: play.parse_source });
      }
      setPhase("starting");
      const run = await startRun(play.id, { mode: effectiveMode });
      router.push(`/runs/${run.run_id}`);
    } catch (err) {
      setError(friendlyErrorMessage(err, "Could not start the run"));
      setPhase("idle");
    }
  }

  const busy = phase === "starting";
  const needsUnlock = mode === "live" && !liveUnlocked;
  const needsSpendConfirmation = mode === "live" && liveUnlocked && !confirmedSpend;
  const canSubmit = objective.trim().length > 0 && !busy && !needsUnlock && !needsSpendConfirmation;
  const cappedProspectCount = live ? Math.min(targetCount, live.live_max_prospects_per_run) : targetCount;
  const maxAttemptsPerCall = live ? 1 + live.llm_max_transport_retries + live.llm_max_schema_retries : null;
  // Worst-case bound across the three per-prospect LLM operations
  // (research, score, personalize) — objective parse is a separate,
  // one-time call not part of a run's per-prospect bound.
  const worstCaseTokens = live && maxAttemptsPerCall ? 3 * maxAttemptsPerCall * live.llm_max_output_tokens : null;

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 p-6">
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-zinc-100">New play</h1>
        <p className="mt-1 text-sm text-zinc-500">
          Describe a growth objective in plain language. Groundwork parses it into structured
          criteria, then runs evidence-backed research and scoring against every prospect.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Panel title="Objective" bodyClassName="p-4">
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-zinc-400">Mode</span>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setMode("demo")}
                  className={`rounded-md border px-3 py-1.5 text-sm ${mode === "demo" ? "border-indigo-400 bg-indigo-400/10 text-indigo-200" : "border-zinc-700 text-zinc-400"}`}
                >
                  Demo
                </button>
                <button
                  type="button"
                  disabled={!liveSelectable}
                  onClick={() => setMode("live")}
                  title={
                    liveSelectable
                      ? undefined
                      : !providersConfigured
                        ? "Live Mode requires BOTH OPENAI_API_KEY and TAVILY_API_KEY configured on the API process"
                        : "Live Mode is hard-disabled on this deployment — OPERATOR_PASSPHRASE/SESSION_SIGNING_KEY aren't configured"
                  }
                  className={`rounded-md border px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${mode === "live" ? "border-indigo-400 bg-indigo-400/10 text-indigo-200" : "border-zinc-700 text-zinc-400"}`}
                >
                  Live {liveSelectable && !isOperator ? "🔒" : ""}
                </button>
              </div>
              {settingsUnreachable && (
                <p className="text-xs text-rose-400">
                  Can&apos;t reach the API to check Live availability — Demo Mode still works fully offline.{" "}
                  <button type="button" className="underline hover:text-rose-300" onClick={loadProviderSettings}>
                    Retry
                  </button>
                </p>
              )}
              {!settingsUnreachable && !providersConfigured && (
                <p className="text-xs text-zinc-500">
                  Live is unavailable —{" "}
                  {!live?.llm_available && !live?.search_available
                    ? "neither OPENAI_API_KEY nor TAVILY_API_KEY is configured"
                    : !live?.llm_available
                      ? "OPENAI_API_KEY is not configured"
                      : "TAVILY_API_KEY is not configured"}{" "}
                  on the API process — H2 Live Mode requires BOTH a real LLM and a real search provider, never a
                  fixture-search fallback. Demo Mode needs no credentials and reproduces the canonical
                  fixture-driven results.
                </p>
              )}
              {!settingsUnreachable && providersConfigured && !operatorLoginConfigured && (
                <p className="text-xs text-zinc-500">
                  Live is hard-disabled on this deployment — no operator passphrase is configured
                  (<span className="font-mono text-zinc-400">OPERATOR_PASSPHRASE</span>/
                  <span className="font-mono text-zinc-400">SESSION_SIGNING_KEY</span>). There is no other way to
                  unlock it.
                </p>
              )}
              {mode === "live" && liveSelectable && !isOperator && (
                <form
                  onSubmit={handleUnlock}
                  className="mt-1 flex flex-col gap-2 rounded-md border border-zinc-700 bg-zinc-900 p-3"
                >
                  <p className="text-xs font-medium text-zinc-300">Live is locked</p>
                  <p className="text-xs text-zinc-500">
                    This deployment requires an operator passphrase before any Live read or write is allowed — a
                    caller sending <span className="font-mono text-zinc-400">mode=&quot;live&quot;</span> alone gets
                    no Live capability.
                  </p>
                  <div className="flex gap-2">
                    <input
                      type="password"
                      value={passphrase}
                      onChange={(e) => setPassphrase(e.target.value)}
                      placeholder="Operator passphrase"
                      autoComplete="current-password"
                      className="flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-3 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                    />
                    <Button type="submit" variant="secondary" disabled={unlocking || !passphrase}>
                      {unlocking ? "Unlocking…" : "Unlock"}
                    </Button>
                  </div>
                  {unlockError && (
                    <p role="alert" className="text-xs text-rose-400">
                      {unlockError}
                    </p>
                  )}
                </form>
              )}
              {mode === "live" && liveUnlocked && (
                <div className="mt-1 flex items-center justify-between rounded-md border border-emerald-700/40 bg-emerald-400/5 px-3 py-2 text-xs text-emerald-300">
                  <span>Live unlocked for this browser session.</span>
                  <button type="button" className="underline hover:text-emerald-200" onClick={handleLockAgain}>
                    Lock
                  </button>
                </div>
              )}
              {mode === "live" && live && (
                <div className="mt-1 flex flex-col gap-1 rounded-md border border-amber-700/40 bg-amber-400/5 p-3 text-xs text-zinc-400">
                  <p className="font-medium text-amber-300">LIVE LLM · LIVE SEARCH</p>
                  <p>
                    Real OpenAI calls (model <span className="font-mono text-zinc-300">{live.model}</span>,
                    reasoning effort <span className="font-mono text-zinc-300">{live.reasoning_effort ?? "omitted"}</span>)
                    for discovery extraction, research extraction, scoring explanation, and personalization —
                    plus real Tavily web search for discovery and per-company sources. Evidence for real prospects
                    is LIVE_FETCH with real, clickable provider URLs.
                  </p>
                  <p>
                    Prospects this run: <span className="font-mono text-zinc-300">{cappedProspectCount}</span>
                    {cappedProspectCount !== targetCount && (
                      <> (capped from {targetCount} by LIVE_MAX_PROSPECTS_PER_RUN={live.live_max_prospects_per_run})</>
                    )}
                  </p>
                  {worstCaseTokens != null && (
                    <p>
                      Hard worst-case LLM bound per prospect: {maxAttemptsPerCall} provider attempts/call ×{" "}
                      {live.llm_max_output_tokens} output tokens × 3 operations ={" "}
                      <span className="font-mono text-zinc-300">{worstCaseTokens.toLocaleString()}</span> tokens.
                    </p>
                  )}
                  {live.search_hard_bounds && (
                    <p>
                      Search hard bounds: max{" "}
                      <span className="font-mono text-zinc-300">
                        {live.search_hard_bounds.live_max_plan_queries_per_run}
                      </span>{" "}
                      discovery queries, max{" "}
                      <span className="font-mono text-zinc-300">
                        {live.search_hard_bounds.live_max_domain_resolution_queries_per_run}
                      </span>{" "}
                      domain-resolution queries, max{" "}
                      <span className="font-mono text-zinc-300">
                        {live.search_hard_bounds.live_max_source_queries_per_prospect}
                      </span>{" "}
                      source queries/prospect, max{" "}
                      <span className="font-mono text-zinc-300">
                        {live.search_hard_bounds.live_max_search_calls_per_run}
                      </span>{" "}
                      search calls/run, max{" "}
                      <span className="font-mono text-zinc-300">
                        {live.search_hard_bounds.live_max_sources_per_prospect}
                      </span>{" "}
                      unique sources/prospect, max{" "}
                      <span className="font-mono text-zinc-300">
                        {live.search_hard_bounds.live_max_extract_calls_per_run}
                      </span>{" "}
                      extract calls/run.
                    </p>
                  )}
                  <p>
                    {live.soft_budget_enforceable
                      ? `Soft spending threshold: $${live.soft_budget_usd} (advisory — not a hard cap; OpenAI only).`
                      : "Monetary threshold is not enforceable — no pricing configured for this deployment."}{" "}
                    {live.search_pricing_configured
                      ? "Tavily usage is priced too."
                      : "Tavily cost estimate unavailable — hard search/extract call caps above are the real safety controls."}
                  </p>
                  <label className="mt-2 flex items-center gap-2 text-zinc-300">
                    <input
                      type="checkbox"
                      checked={confirmedSpend}
                      onChange={(e) => setConfirmedSpend(e.target.checked)}
                    />
                    I understand this run will make real, billed OpenAI and Tavily API calls.
                  </label>
                </div>
              )}
            </div>

            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-zinc-400">GTM objective</span>
              <textarea
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                rows={6}
                maxLength={2000}
                className="resize-none rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                placeholder="Find AI infrastructure startups that recently raised funding…"
              />
            </label>

            <ChipInput
              label="Target industries"
              placeholder="Add an industry and press Enter"
              chips={industries}
              onChange={setIndustries}
            />

            <div className="grid grid-cols-2 gap-4">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-zinc-400">Company size — min</span>
                <input
                  type="number"
                  min={1}
                  value={sizeMin}
                  onChange={(e) => setSizeMin(Number(e.target.value))}
                  className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-zinc-400">Company size — max</span>
                <input
                  type="number"
                  min={sizeMin}
                  value={sizeMax}
                  onChange={(e) => setSizeMax(Number(e.target.value))}
                  className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-zinc-400">Minimum ICP score</span>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={minScore}
                  onChange={(e) => setMinScore(Number(e.target.value))}
                  className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-zinc-400">Prospect count</span>
                <input
                  type="number"
                  min={1}
                  max={25}
                  value={targetCount}
                  onChange={(e) => setTargetCount(Number(e.target.value))}
                  className="rounded-md border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 outline-none focus:border-indigo-400"
                />
              </label>
            </div>

            {error && (
              <p role="alert" className="text-sm text-rose-400">
                {error}
              </p>
            )}

            {mode === "live" && liveUnlocked && (
              <Button
                onClick={handleParseWithModel}
                disabled={phase === "parsing" || !objective.trim()}
                className="w-full"
                variant="secondary"
              >
                {phase === "parsing" ? "Parsing with model…" : "Parse with model"}
              </Button>
            )}

            <Button onClick={handleRunAgents} disabled={!canSubmit} className="w-full">
              {phase === "starting" ? "Starting run…" : needsUnlock ? "Unlock Live to run agents" : "Run Agents"}
            </Button>
            {needsSpendConfirmation && (
              <p className="text-xs text-amber-400">Confirm the spend checkbox above to run agents in Live Mode.</p>
            )}
            <p className="text-xs text-zinc-500">
              Mode: <span className="font-mono text-zinc-400">{mode}</span> —{" "}
              {mode === "demo"
                ? "objective parsing is deterministic in this checkpoint, not an LLM call."
                : "objective parsing is deterministic unless you click \"Parse with model\"."}
            </p>
          </div>
        </Panel>

        <div className="flex flex-col gap-4">
          {displaySpec ? (
            <>
              {mode === "live" && (
                <p className="text-xs text-zinc-500">
                  parse_source: <span className="font-mono text-zinc-300">{displaySpec.parse_source}</span>
                </p>
              )}
              <PlanPanel spec={displaySpec.icp_spec} />
            </>
          ) : (
            <Panel title="Parsed play spec" bodyClassName="p-6">
              <p className="text-sm text-zinc-500">
                {phase === "parsing" ? "Parsing objective…" : "Enter an objective to see the parsed criteria."}
              </p>
            </Panel>
          )}
        </div>
      </div>
    </main>
  );
}
