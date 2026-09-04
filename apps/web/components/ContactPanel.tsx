import type { ReactNode } from "react";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { isSafeLinkedInHref, isSafeLinkedInProfileUrl } from "@/lib/linkedinSafety";
import type { ContactChannel, EvidenceItem, ProspectContact } from "@/lib/types";

// --- axis 1: person identity (v1, unchanged from Checkpoint E) ---

const VERIFICATION_TONE: Record<string, BadgeTone> = {
  VERIFIED: "emerald",
  PERSONA_ONLY: "amber",
  UNAVAILABLE: "neutral",
};

const VERIFICATION_COPY: Record<string, string> = {
  VERIFIED: "A named buyer was identified and confirmed by grounded evidence.",
  PERSONA_ONLY:
    "A persona-matching title was found, but no verified individual — outreach is skipped rather than sent to a guess.",
  UNAVAILABLE:
    "No qualifying buyer could be identified from available evidence. This is an intentional outcome, not a missing field — nothing is invented here.",
};

// --- axes 2-5: contact enrichment channel state copy (V2-E §3) ---
//
// Every entry below is provider-neutral by construction — no provider name
// ever appears in this file. Confidence/catch-all (§4) are rendered as
// separate observation chips, never folded into this copy.

interface AxisCopy {
  badge: string;
  tone: BadgeTone;
  explanation: string;
}

const NOT_OBSERVED: AxisCopy = {
  badge: "NOT OBSERVED",
  tone: "neutral",
  explanation: "This axis was not attempted for this prospect.",
};

const UNKNOWN_STATE: AxisCopy = {
  badge: "UNKNOWN STATE",
  tone: "neutral",
  explanation: "This state isn't recognized by this version of the UI — shown neutrally rather than guessed at.",
};

const EMAIL_DISCOVERY_COPY: Record<string, AxisCopy> = {
  FOUND: { badge: "FOUND", tone: "emerald", explanation: "A provider search found an email address for this person." },
  NOT_FOUND: {
    badge: "NOT FOUND",
    tone: "neutral",
    explanation: "A provider search completed successfully but returned no email address.",
  },
  PROVIDER_ERROR: {
    badge: "PROVIDER ERROR",
    tone: "rose",
    explanation: "No successful email lookup has completed yet — the most recent provider call failed.",
  },
};

const EMAIL_VERIFICATION_COPY: Record<string, AxisCopy> = {
  VERIFIED: {
    badge: "VERIFIED",
    tone: "emerald",
    explanation: "This address was confirmed deliverable by a verification check.",
  },
  RISKY: {
    badge: "RISKY",
    tone: "amber",
    explanation: "This address could not be confirmed as safely deliverable.",
  },
  UNVERIFIABLE: {
    badge: "UNVERIFIABLE",
    tone: "amber",
    explanation: "Deliverability could not be determined for this address.",
  },
  INVALID: {
    badge: "INVALID",
    tone: "rose",
    explanation: "This address was confirmed invalid or undeliverable.",
  },
  UNVERIFIED: {
    badge: "UNVERIFIED",
    tone: "neutral",
    explanation: "No verification signal is available for this address.",
  },
};

const LINKEDIN_RESOLUTION_COPY: Record<string, AxisCopy> = {
  RESOLVED: { badge: "RESOLVED", tone: "emerald", explanation: "A LinkedIn profile was found for this person." },
  NOT_FOUND: {
    badge: "NOT FOUND",
    tone: "neutral",
    explanation: "A provider search completed successfully but no LinkedIn profile was found.",
  },
  PROVIDER_ERROR: {
    badge: "PROVIDER ERROR",
    tone: "rose",
    explanation: "No successful LinkedIn lookup has completed yet — the most recent provider call failed.",
  },
};

const LINKEDIN_IDENTITY_COPY: Record<string, AxisCopy> = {
  STRONG_MATCH: {
    badge: "STRONG MATCH",
    tone: "emerald",
    explanation: "Both the name and company on this profile match this prospect.",
  },
  WEAK_MATCH: {
    badge: "WEAK MATCH",
    tone: "amber",
    explanation: "Only one of name or company could be confirmed — treated as unconfirmed.",
  },
  MISMATCH: {
    badge: "MISMATCH",
    tone: "rose",
    explanation: "The name or company on this profile does not match this prospect — treated as a different person.",
  },
  UNKNOWN: {
    badge: "UNKNOWN",
    tone: "neutral",
    explanation: "There isn't enough information to confirm whether this profile belongs to the same person and company.",
  },
};

const PRESERVED_STATE_COPY: Record<string, string> = {
  REFRESH_FAILED: "The most recent refresh attempt failed — showing the last known state.",
  REFRESH_FOUND_NOTHING: "The most recent refresh found nothing new — showing the last known state.",
};

function AxisRow({
  label,
  copy,
  extra,
}: {
  label: string;
  copy: AxisCopy;
  extra?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 border-t border-zinc-800 pt-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">{label}</span>
        <Badge tone={copy.tone}>{copy.badge}</Badge>
      </div>
      <p className="text-xs text-zinc-500">{copy.explanation}</p>
      {extra}
    </div>
  );
}

function ProvenanceChips({ channel }: { channel: ContactChannel }) {
  const chips: ReactNode[] = [];
  if (channel.origin) {
    chips.push(
      <Badge key="origin" tone={channel.origin === "LIVE_PROVIDER" ? "sky" : "indigo"}>
        {channel.origin === "LIVE_PROVIDER" ? "Live" : "Demo (synthetic)"}
      </Badge>,
    );
  }
  if (channel.stale) {
    chips.push(
      <Badge key="stale" tone="amber">
        Stale{channel.stale_after_days != null ? ` · >${channel.stale_after_days}d` : ""}
      </Badge>,
    );
  }
  if (chips.length === 0) return null;
  return <div className="flex flex-wrap gap-1">{chips}</div>;
}

function PreservedStateNote({ channel }: { channel: ContactChannel }) {
  if (!channel.preserved_state) return null;
  const text = PRESERVED_STATE_COPY[channel.preserved_state] ?? "The current state was preserved from an earlier attempt.";
  return <p className="text-xs text-amber-500">{text}</p>;
}

function EmailObservations({ channel }: { channel: ContactChannel }) {
  if (channel.provider_confidence == null && channel.is_catch_all == null) return null;
  return (
    <div className="flex flex-wrap gap-1 text-[11px] text-zinc-500">
      {channel.provider_confidence != null && (
        <Badge tone="neutral">Confidence {Math.round(channel.provider_confidence * 100)}%</Badge>
      )}
      {channel.is_catch_all != null && (
        <Badge tone="neutral">{channel.is_catch_all ? "Catch-all domain" : "Not a catch-all domain"}</Badge>
      )}
    </div>
  );
}

function LinkedInIdentifierLine({ channel }: { channel: ContactChannel }) {
  const identifier = channel.identifier;
  if (!identifier) return null;

  const isDemo = identifier.startsWith("demo://");
  if (isDemo) {
    return (
      <p className="font-mono text-xs break-all text-zinc-500">
        {identifier} <span className="text-zinc-600">— simulated profile, not a real URL</span>
      </p>
    );
  }

  const safe = isSafeLinkedInHref({
    channel: channel.channel,
    origin: channel.origin,
    discoveryState: channel.discovery_state,
    identifier,
  });

  if (safe) {
    return (
      <a
        href={identifier}
        target="_blank"
        rel="noreferrer noopener"
        className="font-mono text-xs break-all text-indigo-400 hover:text-indigo-300"
      >
        {identifier} ↗
      </a>
    );
  }

  return <p className="font-mono text-xs break-all text-zinc-500">{identifier}</p>;
}

export function ContactPanel({
  contact,
  contactChannels,
  evidenceById,
}: {
  contact: ProspectContact | null;
  contactChannels: ContactChannel[];
  evidenceById: Record<string, EvidenceItem>;
}) {
  const channelsByName: Record<string, ContactChannel> = {};
  for (const c of contactChannels) channelsByName[c.channel] = c;
  const email = channelsByName.email;
  const linkedin = channelsByName.linkedin;

  const personaVerified = contact?.verification === "VERIFIED" || contact?.verification === "PERSONA_ONLY";
  const contactLinkedInSafe = personaVerified && isSafeLinkedInProfileUrl(contact?.linkedin_url ?? null);

  return (
    <div className="flex flex-col gap-4 p-4 text-sm">
      {/* Axis 1: person identity (v1) */}
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[11px] font-medium tracking-wide text-zinc-500 uppercase">Person identity</span>
          {contact ? (
            <Badge tone={VERIFICATION_TONE[contact.verification] ?? "neutral"}>{contact.verification}</Badge>
          ) : (
            <Badge tone="neutral">{NOT_OBSERVED.badge}</Badge>
          )}
        </div>

        {!contact ? (
          <p className="text-xs text-zinc-500">Contact resolution did not run for this prospect.</p>
        ) : personaVerified ? (
          <div className="flex flex-col gap-1">
            <span className="text-base font-medium text-zinc-100">{contact.full_name ?? "Name withheld"}</span>
            <span className="text-zinc-400">{contact.title ?? "—"}</span>
            {contact.email && <span className="font-mono text-xs text-zinc-500">{contact.email}</span>}
            {contact.linkedin_url &&
              (contactLinkedInSafe ? (
                <a
                  href={contact.linkedin_url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-mono text-xs text-indigo-400 hover:text-indigo-300"
                >
                  {contact.linkedin_url} ↗
                </a>
              ) : (
                <span className="font-mono text-xs text-zinc-500">{contact.linkedin_url}</span>
              ))}
            <p className="mt-1 text-xs text-zinc-500">{VERIFICATION_COPY[contact.verification]}</p>
          </div>
        ) : (
          <p className="text-xs text-zinc-500">{VERIFICATION_COPY.UNAVAILABLE}</p>
        )}

        {contact && contact.evidence_ids.length > 0 && (
          <div className="text-xs text-zinc-500">
            Evidence: {contact.evidence_ids.map((id) => evidenceById[id]?.title ?? id).join(", ")}
          </div>
        )}
      </div>

      {/* Axis 2: email discovery */}
      <AxisRow
        label="Email discovery"
        copy={
          !email
            ? NOT_OBSERVED
            : (EMAIL_DISCOVERY_COPY[email.discovery_state ?? ""] ?? UNKNOWN_STATE)
        }
        extra={
          email && (
            <>
              <ProvenanceChips channel={email} />
              <PreservedStateNote channel={email} />
              <EmailObservations channel={email} />
              {email.identifier && <span className="font-mono text-xs text-zinc-500">{email.identifier}</span>}
            </>
          )
        }
      />

      {/* Axis 3: email verification */}
      <AxisRow
        label="Email verification"
        copy={
          !email
            ? NOT_OBSERVED
            : (EMAIL_VERIFICATION_COPY[email.verification_state ?? ""] ?? UNKNOWN_STATE)
        }
      />

      {/* Axis 4: LinkedIn resolution */}
      <AxisRow
        label="LinkedIn resolution"
        copy={
          !linkedin
            ? NOT_OBSERVED
            : (LINKEDIN_RESOLUTION_COPY[linkedin.discovery_state ?? ""] ?? UNKNOWN_STATE)
        }
        extra={
          linkedin && (
            <>
              <ProvenanceChips channel={linkedin} />
              <PreservedStateNote channel={linkedin} />
              <LinkedInIdentifierLine channel={linkedin} />
            </>
          )
        }
      />

      {/* Axis 5: LinkedIn identity match */}
      <AxisRow
        label="LinkedIn identity match"
        copy={
          !linkedin
            ? NOT_OBSERVED
            : (LINKEDIN_IDENTITY_COPY[linkedin.identity_match_state ?? ""] ?? UNKNOWN_STATE)
        }
      />
    </div>
  );
}
