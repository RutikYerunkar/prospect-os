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

export function formatScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : String(score);
}

export function formatConfidence(confidence: number | null | undefined): string {
  return confidence === null || confidence === undefined ? "—" : `${Math.round(confidence * 100)}%`;
}
