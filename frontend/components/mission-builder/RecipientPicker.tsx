"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookUser, Search } from "lucide-react";
import { api } from "@/services/api";
import type { Recipient } from "@/types/api";
import { contactPicker, formatPhone, normalizePhone } from "@/lib/phone";
import { cn } from "@/lib/utils";

const field =
  "h-12 w-full rounded-2xl bg-bg/60 px-4 text-body text-ink ring-1 ring-line placeholder:text-ink-3 focus:outline-none focus:ring-aurora/60";

const noop = () => () => {};

/** Choose who Sakhi calls: a phone contact (where the browser allows), someone called before,
 * or a typed number. Calls `onChange` with a valid recipient, or null while incomplete. */
export function RecipientPicker({ suggestedName, onChange }: { suggestedName?: string | null; onChange: (r: Recipient | null) => void }) {
  const recent = useQuery({ queryKey: ["recipients"], queryFn: api.recentRecipients });
  const canPick = useSyncExternalStore(noop, () => contactPicker() !== null, () => false);
  const [name, setName] = useState(suggestedName ?? "");
  const [number, setNumber] = useState("");
  const [query, setQuery] = useState(suggestedName ?? "");
  const [touched, setTouched] = useState(false);
  const [choices, setChoices] = useState<string[] | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);

  const people = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = recent.data ?? [];
    return q ? all.filter((r) => r.name.toLowerCase().includes(q) || r.phone.includes(q.replace(/\s/g, ""))) : all;
  }, [recent.data, query]);

  const update = (n: string, num: string) => {
    setName(n);
    setNumber(num);
    const phone = normalizePhone(num);
    onChange(n.trim() && phone ? { name: n.trim(), phone } : null);
  };

  const pick = async () => {
    setPickError(null);
    const picker = contactPicker();
    if (!picker) return;
    try {
      const [c] = await picker.select(["name", "tel"], { multiple: false });
      if (!c) return;
      const tels = (c.tel ?? []).filter(Boolean);
      const n = c.name?.[0] ?? "";
      if (!tels.length) {
        setPickError(`${n || "That contact"} has no phone number.`);
        return;
      }
      if (tels.length > 1) setChoices(tels);
      update(n, tels[0]);
    } catch {
      setPickError("Couldn't open your contacts. Type the number instead.");
    }
  };

  const valid = normalizePhone(number);
  const showError = touched && number.trim().length > 0 && !valid;

  return (
    <section aria-labelledby="recipient-title" className="mx-auto mt-10 w-full max-w-md text-left">
      <h2 id="recipient-title" className="text-body font-medium text-ink">
        Who should Sakhi call?
      </h2>
      <p className="mt-1 text-meta text-ink-3">
        {canPick ? "Choose a contact, someone you've called before, or type a number." : "Type the number, or choose someone you've called before."}
      </p>

      {canPick && (
        <button
          type="button"
          onClick={() => void pick()}
          className="mt-4 flex h-12 w-full items-center justify-center gap-2 rounded-full text-body text-aurora ring-1 ring-aurora/40 transition-colors hover:bg-aurora-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora"
        >
          <BookUser className="size-5" aria-hidden /> Choose from contacts
        </button>
      )}
      {pickError && (
        <p role="alert" className="mt-2 text-meta text-error">
          {pickError}
        </p>
      )}
      {choices && (
        <div role="radiogroup" aria-label="Which number?" className="mt-3 flex flex-wrap gap-1.5">
          {choices.map((t) => (
            <button
              key={t}
              type="button"
              role="radio"
              aria-checked={normalizePhone(t) === valid}
              onClick={() => update(name, t)}
              className={cn(
                "min-h-9 rounded-full px-3 text-meta text-ink-2 ring-1 ring-line hover:text-ink",
                normalizePhone(t) === valid && "bg-aurora-soft text-ink ring-aurora/50",
              )}
            >
              {formatPhone(normalizePhone(t)) || t}
            </button>
          ))}
        </div>
      )}

      {(recent.data?.length ?? 0) > 0 && (
        <div className="mt-5">
          <label htmlFor="recipient-search" className="sr-only">
            Search people you&apos;ve called
          </label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
            <input id="recipient-search" className={cn(field, "pl-10")} placeholder="Search people you've called" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <ul className="mt-2 max-h-52 overflow-y-auto" aria-label="People you've called">
            {people.map((r) => {
              const on = valid === r.phone && name.trim() === r.name;
              return (
                <li key={r.phone}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => update(r.name, r.phone)}
                    className={cn(
                      "flex min-h-12 w-full items-center justify-between gap-3 rounded-2xl px-4 text-left transition-colors hover:bg-elevated",
                      on && "bg-aurora-soft ring-1 ring-aurora/40",
                    )}
                  >
                    <span className="truncate text-body text-ink">{r.name}</span>
                    <span className="shrink-0 text-meta text-ink-2 tabular">{formatPhone(r.phone)}</span>
                  </button>
                </li>
              );
            })}
            {people.length === 0 && <li className="px-4 py-3 text-meta text-ink-3">Nobody by that name yet. Type their number below.</li>}
          </ul>
        </div>
      )}

      <div className="mt-5 grid gap-2 sm:grid-cols-[1fr_1.2fr]">
        <div>
          <label htmlFor="recipient-name" className="mb-1 block text-meta text-ink-3">
            Name
          </label>
          <input id="recipient-name" className={field} autoComplete="off" placeholder="Rahul" value={name} onChange={(e) => update(e.target.value, number)} />
        </div>
        <div>
          <label htmlFor="recipient-phone" className="mb-1 block text-meta text-ink-3">
            Phone number
          </label>
          <input
            id="recipient-phone"
            className={cn(field, "tabular", showError && "ring-error/60")}
            type="tel"
            inputMode="tel"
            autoComplete="off"
            placeholder="98765 43210"
            value={number}
            aria-invalid={showError}
            aria-describedby={showError ? "recipient-phone-error" : undefined}
            onBlur={() => setTouched(true)}
            onChange={(e) => update(name, e.target.value)}
          />
        </div>
      </div>
      {showError && (
        <p id="recipient-phone-error" role="alert" className="mt-2 text-meta text-error">
          Enter a 10-digit mobile number, like 98765 43210.
        </p>
      )}
    </section>
  );
}
