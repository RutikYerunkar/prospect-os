import Link from "next/link";

/** Checkpoint I1 Phase 9 — Next.js renders this for any unmatched route. */
export default function NotFound() {
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <div className="max-w-md text-center">
        <p className="text-sm text-zinc-300">Page not found.</p>
        <p className="mt-2 text-xs text-zinc-500">There&apos;s nothing at this URL.</p>
        <Link
          href="/plays/new"
          className="mt-4 inline-block rounded-md border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:border-zinc-600 hover:text-zinc-100"
        >
          New Play
        </Link>
      </div>
    </main>
  );
}
