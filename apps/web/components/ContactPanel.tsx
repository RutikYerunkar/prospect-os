import { Badge, type BadgeTone } from "@/components/ui/Badge";
import type { EvidenceItem, ProspectContact } from "@/lib/types";

const VERIFICATION_TONE: Record<string, BadgeTone> = {
  VERIFIED: "emerald",
  PERSONA_ONLY: "amber",
  UNAVAILABLE: "neutral",
};

const VERIFICATION_COPY: Record<string, string> = {
  VERIFIED: "A named buyer was identified and confirmed by grounded evidence.",
  PERSONA_ONLY: "A persona-matching title was found, but no verified individual — outreach is skipped rather than sent to a guess.",
  UNAVAILABLE:
    "No qualifying buyer could be identified from available evidence. This is an intentional outcome, not a missing field — nothing is invented here.",
};

export function ContactPanel({
  contact,
  evidenceById,
}: {
  contact: ProspectContact | null;
  evidenceById: Record<string, EvidenceItem>;
}) {
  if (!contact) {
    return <p className="p-4 text-sm text-zinc-500">Contact resolution did not run for this prospect.</p>;
  }

  const unavailable = contact.verification === "UNAVAILABLE";

  return (
    <div className="flex flex-col gap-3 p-4 text-sm">
      <Badge tone={VERIFICATION_TONE[contact.verification] ?? "neutral"} className="w-fit">
        {contact.verification}
      </Badge>

      {unavailable ? (
        <p className="text-zinc-500">{VERIFICATION_COPY.UNAVAILABLE}</p>
      ) : (
        <div className="flex flex-col gap-1">
          <span className="text-base font-medium text-zinc-100">{contact.full_name ?? "Name withheld"}</span>
          <span className="text-zinc-400">{contact.title ?? contact.persona ?? "—"}</span>
          {contact.email && <span className="font-mono text-xs text-zinc-500">{contact.email}</span>}
          {contact.linkedin_url && (
            <a
              href={contact.linkedin_url}
              target="_blank"
              rel="noreferrer noopener"
              className="font-mono text-xs text-indigo-400 hover:text-indigo-300"
            >
              {contact.linkedin_url} ↗
            </a>
          )}
          <p className="mt-1 text-xs text-zinc-500">{VERIFICATION_COPY[contact.verification]}</p>
        </div>
      )}

      {contact.evidence_ids.length > 0 && (
        <div className="text-xs text-zinc-500">
          Evidence: {contact.evidence_ids.map((id) => evidenceById[id]?.title ?? id).join(", ")}
        </div>
      )}
    </div>
  );
}
