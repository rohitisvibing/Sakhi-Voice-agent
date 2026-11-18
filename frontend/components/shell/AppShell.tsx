"use client";

import Link from "next/link";
import { UserRound } from "lucide-react";
import type { ConversationState } from "@/types/api";
import { cn } from "@/lib/utils";
import { StatusGlyph } from "@/components/orb/StatusGlyph";
import { DemoBadge } from "./DemoBadge";
import { AmbientGlow } from "./AmbientGlow";
import { useMe } from "./AuthGate";

export function Wordmark() {
  return (
    <Link href="/" className="flex items-center gap-2 rounded-full pr-1 text-ink" aria-label="Sakhi home">
      <StatusGlyph state="ready" size={20} label="Sakhi" />
      <span className="font-serif text-[22px] leading-none tracking-[-0.01em]">Sakhi</span>
    </Link>
  );
}

function ProfileLink() {
  const me = useMe();
  return (
    <Link
      href="/profile"
      aria-label="About you"
      className="grid size-10 place-items-center rounded-full text-ink-2 transition-colors hover:bg-elevated hover:text-ink"
    >
      {me?.avatar ? (
        // eslint-disable-next-line @next/next/no-img-element -- a tiny remote avatar; no optimisation needed
        <img src={me.avatar} alt="" referrerPolicy="no-referrer" className="size-7 rounded-full ring-1 ring-line" />
      ) : (
        <UserRound className="size-5" />
      )}
    </Link>
  );
}

/** Page chrome: one quiet line on top, and the room light behind. */
export function AppShell({
  children,
  wide = false,
  glow = "ready",
}: {
  children: React.ReactNode;
  wide?: boolean;
  glow?: ConversationState | null;
}) {
  return (
    <div className="relative flex min-h-dvh flex-col">
      {glow && <AmbientGlow state={glow} />}
      <header className="relative z-10">
        <div className={cn("mx-auto flex h-16 items-center justify-between px-4 sm:px-6", wide ? "max-w-5xl" : "max-w-2xl")}>
          <span className="flex items-center gap-1">
            <Wordmark />
            <DemoBadge />
          </span>
          <ProfileLink />
        </div>
      </header>
      <main
        className={cn(
          "relative z-10 mx-auto w-full flex-1 px-4 pb-16 pt-4 sm:px-6",
          wide ? "max-w-5xl" : "max-w-2xl",
        )}
      >
        {children}
      </main>
    </div>
  );
}
