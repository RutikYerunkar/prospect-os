import { useRouter } from "next/navigation";
import { Table, TBody, TH, THead, TR } from "@/components/ui/Table";
import { ProspectRow } from "@/components/ProspectRow";
import type { ProspectSummary } from "@/lib/types";
import type { RetryInfo } from "@/lib/useRunStream";

export function RunBoard({
  prospects,
  retrying,
}: {
  prospects: ProspectSummary[] | null;
  retrying: Record<string, RetryInfo>;
}) {
  const router = useRouter();

  if (prospects === null) {
    return <p className="p-6 text-sm text-zinc-500">Loading board…</p>;
  }

  if (prospects.length === 0) {
    return (
      <p className="p-6 text-sm text-zinc-500">
        No prospects discovered yet — discovery runs first, rows appear as it completes.
      </p>
    );
  }

  return (
    <Table>
      <THead>
        <TR>
          <TH>Company</TH>
          <TH>Stage</TH>
          <TH>Retry</TH>
          <TH>Signal</TH>
          <TH>Contact</TH>
          <TH>Score</TH>
          <TH>Conf.</TH>
          <TH>Status</TH>
        </TR>
      </THead>
      <TBody>
        {prospects.map((p) => (
          <ProspectRow
            key={p.id}
            prospect={p}
            retry={retrying[p.id]}
            onOpen={(id) => router.push(`/prospects/${id}`)}
          />
        ))}
      </TBody>
    </Table>
  );
}
