"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useRouter } from "next/navigation";
import { ApiError, createPlay, startRun } from "@/lib/api";
import type { PlayResponse } from "@/lib/types";
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

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const parseSeqRef = useRef(0);
  const lastParsedSignatureRef = useRef<string | null>(null);

  const signature = JSON.stringify({ objective, industries, sizeMin, sizeMax, minScore, targetCount });

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
      createPlay({ objective, icp_overrides: overrides(), target_count: targetCount })
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
  }, [objective, industries, sizeMin, sizeMax, minScore, targetCount, signature]);

  async function handleRunAgents() {
    setError(null);
    try {
      let play = parsedPlay;
      if (!play || lastParsedSignatureRef.current !== signature) {
        setPhase("parsing");
        play = await createPlay({ objective, icp_overrides: overrides(), target_count: targetCount });
        setParsedPlay(play);
        lastParsedSignatureRef.current = signature;
      }
      setPhase("starting");
      const run = await startRun(play.id, {});
      router.push(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the run");
      setPhase("idle");
    }
  }

  const busy = phase === "starting";
  const canSubmit = objective.trim().length > 0 && !busy;

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

            <Button onClick={handleRunAgents} disabled={!canSubmit} className="w-full">
              {phase === "starting" ? "Starting run…" : "Run Agents"}
            </Button>
            <p className="text-xs text-zinc-500">
              Mode: <span className="font-mono text-zinc-400">demo</span> — objective parsing is
              deterministic in this checkpoint, not an LLM call.
            </p>
          </div>
        </Panel>

        <div className="flex flex-col gap-4">
          {parsedPlay ? (
            <PlanPanel spec={parsedPlay.icp_spec} />
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
