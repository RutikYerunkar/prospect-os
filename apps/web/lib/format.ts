export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

export function formatElapsedSince(startedAt: string, endedAt?: string | null): string {
  const start = new Date(startedAt).getTime();
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  return formatDuration(Math.max(0, end - start));
}

export function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString(undefined, {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatStage(stage: string): string {
  return stage.charAt(0) + stage.slice(1).toLowerCase();
}

export function formatStatus(status: string): string {
  return status.replaceAll("_", " ");
}

const RUN_STATUS_LABEL: Record<string, string> = {
  RUNNING: "Running",
  COMPLETED: "Completed",
  PARTIAL: "Completed with issues",
  INTERRUPTED: "Interrupted",
};

/**
 * Human-readable run status for display — e.g. PARTIAL (a real backend
 * status meaning "run finished, at least one prospect didn't reach a clean
 * terminal outcome") reads as "Completed with issues" rather than sounding
 * like a bug. The raw backend value is never discarded — callers should
 * still surface it unobtrusively (e.g. a `title` tooltip).
 */
export function formatRunStatus(status: string): string {
  return RUN_STATUS_LABEL[status] ?? formatStatus(status);
}

export function formatScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : String(score);
}

export function formatConfidence(confidence: number | null | undefined): string {
  return confidence === null || confidence === undefined ? "—" : `${Math.round(confidence * 100)}%`;
}

export function formatDateOnly(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatPercent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}
