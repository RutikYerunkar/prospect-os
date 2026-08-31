/**
 * Mirrors `Settings.max_concurrent_prospects` in `apps/api/groundwork/config.py`
 * (default 3). Not returned by any Checkpoint C endpoint, so this is the
 * configured bound, not a computed value — the live numerator it's compared
 * against (agents currently inside the semaphore) is always read from real
 * prospect stage data, never hardcoded.
 */
export const MAX_CONCURRENT_PROSPECTS = 3;
