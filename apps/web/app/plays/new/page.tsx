"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, createPlay, getProviderSettings, startRun } from "@/lib/api";
import type { Mode, PlayResponse, ProviderSettingsResponse } from "@/lib/types";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { PlanPanel } from "@/components/PlanPanel";

const DEFAULT_OBJECTIVE =
  "Find AI infrastructure startups that recently raised funding or are expanding their GTM " +
  "teams. Identify the most relevant sales leader, score each company against our ICP, explain " +
  "the evidence, and draft personalized outreach.";

type Phase = "idle" | "parsing" | "starting";

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

  const [parsedPlay, setParsedPlay] = useState<PlayResponse | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);

  // Checkpoint G: Demo/Live selector. Live disables itself (with an
  // explanation) unless a real OpenAI runtime is actually configured —
  // never a silent fallback to Demo.
  const [mode, setMode] = useState<Mode>("demo");
  const [providerSettings, setProviderSettings] = useState<ProviderSettingsResponse | null>(null);
  const [confirmedSpend, setConfirmedSpend] = useState(false);

  useEffect(() => {
    getProviderSettings()
      .then(setProviderSettings)
      .catch(() => setProviderSettings(null));
  }, []);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const parseSeqRef = useRef(0);
  const lastParsedSignatureRef = useRef<string | null>(null);

  const signature = JSON.stringify({ objective, industries, sizeMin, sizeMax, minScore, targetCount, mode });
  const live = providerSettings?.live;
  const liveAvailable = live?.available ?? false;
  // The Live toggle button is itself disabled while unavailable, so `mode`
  // can never be *set* to "live" in that state — this guards only the
  // (currently unreachable, but cheap to guard) case of availability
  // changing after the fact, without an effect that would just be
  // re-deriving state React already has.
  const effectiveMode: Mode = mode === "live" && !liveAvailable ? "demo" : mode;

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

  // Live-parse the objective + controls into a structured PlaySpec via the
  // real POST /api/plays endpoint (no separate parse-only endpoint exists —
  // this *is* how the API parses an objective) so the criteria are visible
  // beside the form before the user commits to running agents. All setState
  // calls happen inside the debounce timeout callback, never synchronously
  // in the effect body, so this doesn't cascade renders on every keystroke.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const seq = ++parseSeqRef.current;

    debounceRef.current = setTimeout(() => {
      if (seq !== parseSeqRef.current) return;
      if (!objective.trim()) {
        setParsedPlay(null);
        return;
      }
      setPhase("parsing");
      setError(null);
      // NEVER sets use_live_objective_parser — Live Mode's real parser only
      // fires on an explicit "Parse with model" click or Run Agents, never
      // on this 600ms debounce. In Live Mode this still renders a
      // deterministic preview immediately (icp_overrides applied, no LLM
      // call, no spend) — real live parsing is a separate, deliberate step.
      createPlay({ objective, icp_overrides: overrides(), target_count: targetCount, mode: effectiveMode })
        .then((play) => {
          if (seq !== parseSeqRef.current) return; // superseded by a newer edit
          setParsedPlay(play);
          lastParsedSignatureRef.current = signature;
          setPhase("idle");
        })
        .catch((err: unknown) => {
          if (seq !== parseSeqRef.current) return;
          setError(err instanceof ApiError ? err.message : "Could not parse objective");
          setPhase("idle");
        });
    }, 600);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [objective, industries, sizeMin, sizeMax, minScore, targetCount, effectiveMode, signature]);

  async function handleParseWithModel() {
    setError(null);
    setPhase("parsing");
    try {
      const play = await createPlay({
        objective, icp_overrides: overrides(), target_count: targetCount, mode: "live",
        use_live_objective_parser: true,
      });
      setParsedPlay(play);
      lastParsedSignatureRef.current = signature;
      setPhase("idle");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Live objective parse failed");
      setPhase("idle");
    }
  }

  async function handleRunAgents() {
    setError(null);
    try {
      let play = parsedPlay;
      if (!play || lastParsedSignatureRef.current !== signature) {
        setPhase("parsing");
        play = await createPlay({ objective, icp_overrides: overrides(), target_count: targetCount, mode: effectiveMode });
        setParsedPlay(play);
        lastParsedSignatureRef.current = signature;
      }
      setPhase("starting");
      const run = await startRun(play.id, { mode: effectiveMode });
      router.push(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the run");
      setPhase("idle");
    }
  }

  const busy = phase === "starting";
  const needsSpendConfirmation = mode === "live" && !confirmedSpend;
  const canSubmit = objective.trim().length > 0 && !busy && !needsSpendConfirmation;
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
                  disabled={!liveAvailable}
                  onClick={() => setMode("live")}
                  title={liveAvailable ? undefined : "Live Mode requires OPENAI_API_KEY configured on the API process"}
                  className={`rounded-md border px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${mode === "live" ? "border-indigo-400 bg-indigo-400/10 text-indigo-200" : "border-zinc-700 text-zinc-400"}`}
                >
                  Live
                </button>
              </div>
              {!liveAvailable && (
                <p className="text-xs text-zinc-500">
                  Live is unavailable — no OPENAI_API_KEY is configured on the API process. Demo Mode needs no
                  credentials and reproduces the canonical fixture-driven results.
                </p>
              )}
              {mode === "live" && live && (
                <div className="mt-1 flex flex-col gap-1 rounded-md border border-amber-700/40 bg-amber-400/5 p-3 text-xs text-zinc-400">
                  <p className="font-medium text-amber-300">LIVE LLM · FIXTURE SEARCH</p>
                  <p>
                    Real OpenAI calls (model <span className="font-mono text-zinc-300">{live.model}</span>,
                    reasoning effort <span className="font-mono text-zinc-300">{live.reasoning_effort ?? "omitted"}</span>)
                    for research extraction, scoring explanation, and personalization. Search stays
                    fixture-backed — no live web search yet (Checkpoint H). Evidence remains SYNTHETIC.
                  </p>
                  <p>
                    Prospects this run: <span className="font-mono text-zinc-300">{cappedProspectCount}</span>
                    {cappedProspectCount !== targetCount && (
                      <> (capped from {targetCount} by LIVE_MAX_PROSPECTS_PER_RUN={live.live_max_prospects_per_run})</>
                    )}
                  </p>
                  {worstCaseTokens != null && (
                    <p>
                      Hard worst-case bound per prospect: {maxAttemptsPerCall} provider attempts/call ×{" "}
                      {live.llm_max_output_tokens} output tokens × 3 operations ={" "}
                      <span className="font-mono text-zinc-300">{worstCaseTokens.toLocaleString()}</span> tokens.
                    </p>
                  )}
                  <p>
                    {live.soft_budget_enforceable
                      ? `Soft spending threshold: $${live.soft_budget_usd} (advisory — not a hard cap).`
                      : "Monetary threshold is not enforceable — no pricing configured for this deployment."}
                  </p>
                  <label className="mt-2 flex items-center gap-2 text-zinc-300">
                    <input
                      type="checkbox"
                      checked={confirmedSpend}
                      onChange={(e) => setConfirmedSpend(e.target.checked)}
                    />
                    I understand this run will make real, billed OpenAI API calls.
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

            {mode === "live" && (
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
              {phase === "starting" ? "Starting run…" : "Run Agents"}
            </Button>
            {mode === "live" && needsSpendConfirmation && (
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
          {parsedPlay ? (
            <>
              {parsedPlay.mode === "live" && (
                <p className="text-xs text-zinc-500">
                  parse_source: <span className="font-mono text-zinc-300">{parsedPlay.parse_source}</span>
                </p>
              )}
              <PlanPanel spec={parsedPlay.icp_spec} />
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
