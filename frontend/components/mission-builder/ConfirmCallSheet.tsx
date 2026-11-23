"use client";

import { Phone } from "lucide-react";
import type { Recipient } from "@/types/api";
import { formatPhone } from "@/lib/phone";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { ErrorNote } from "@/components/shell/ErrorNote";

/** Nothing is dialled until the person confirms who Sakhi is about to call. */
export function ConfirmCallSheet({
  recipient,
  objective,
  open,
  calling,
  error,
  onOpenChange,
  onConfirm,
}: {
  recipient: Recipient | null;
  objective: string;
  open: boolean;
  calling: boolean;
  error: unknown;
  onOpenChange: (o: boolean) => void;
  onConfirm: () => void;
}) {
  if (!recipient) return null;
  return (
    <Sheet open={open} onOpenChange={(o) => !calling && onOpenChange(o)}>
      <SheetContent
        side="bottom"
        className="mx-auto max-h-[90dvh] w-full max-w-xl gap-5 overflow-y-auto rounded-t-[2rem] border-0 bg-surface px-6 pb-[calc(1.5rem+env(safe-area-inset-bottom))] pt-7 ring-1 ring-aurora/30 sm:bottom-6 sm:rounded-[2rem]"
      >
        <div>
          <SheetTitle className="font-serif text-[2rem] font-normal leading-tight text-ink">Call {recipient.name}?</SheetTitle>
          <SheetDescription className="mt-1 text-lead text-ink-2 tabular">{formatPhone(recipient.phone)}</SheetDescription>
        </div>
        <p className="border-l-2 border-aurora/70 pl-3.5 text-body text-ink">{objective}</p>
        <p className="text-meta text-ink-2">
          Sakhi will speak for you on this call. You can follow it live and end it any time.
        </p>
        <ErrorNote error={error} />
        <div className="flex items-center gap-2">
          <Button className="h-12 flex-1 rounded-full" disabled={calling} onClick={onConfirm}>
            <Phone className="size-4" aria-hidden /> {calling ? "Calling…" : "Call now"}
          </Button>
          <Button variant="secondary" className="h-12 rounded-full px-6" disabled={calling} onClick={() => onOpenChange(false)}>
            Change
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
