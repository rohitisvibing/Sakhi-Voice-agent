"use client";

import { createContext, Suspense, useContext, useEffect, useRef, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { usePathname } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/services/api";
import { signInWithGoogle, signInWithPassword, supabase } from "@/lib/supabase";
import { StatusGlyph } from "@/components/orb/StatusGlyph";
import { AmbientGlow } from "./AmbientGlow";

export interface Me {
  id: string;
  name: string | null;
  email: string | null;
  avatar: string | null;
}

const MeContext = createContext<Me | null>(null);

/** The signed-in person, or null when sign-in is off. */
export function useMe() {
  return useContext(MeContext);
}

export async function signOut() {
  await supabase?.auth.signOut();
}

function meFrom(s: Session): Me {
  const m = s.user.user_metadata ?? {};
  return { id: s.user.id, name: m.full_name ?? m.name ?? null, email: s.user.email ?? null, avatar: m.avatar_url ?? m.picture ?? null };
}

const PUBLIC_PATHS = ["/legal"];

function PathWatcher({ onChange }: { onChange: (path: string) => void }) {
  const pathname = usePathname();
  useEffect(() => onChange(pathname), [pathname, onChange]);
  return null;
}

/**
 * The page always renders, so its static shell paints first; once the browser knows
 * nobody is signed in (and the backend asks for sign-in), the sign-in screen replaces it.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const [path, setPath] = useState<string | null>(null);
  const [session, setSession] = useState<Session | null | undefined>(supabase ? undefined : null);
  const userId = useRef<string | null | undefined>(undefined);
  const required = useQuery({ queryKey: ["auth-required"], queryFn: async () => (await api.session()).required, staleTime: Infinity });

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, s) => {
      // A different person (or nobody) now: nothing cached for the last one may show.
      const id = s?.user.id ?? null;
      if (userId.current !== undefined && userId.current !== id) qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth-required" });
      userId.current = id;
      setSession(s);
    });
    return () => data.subscription.unsubscribe();
  }, [qc]);

  const signedOut =
    supabase !== null && session === null && required.data === true && path !== null && !PUBLIC_PATHS.includes(path);
  return (
    <MeContext.Provider value={session ? meFrom(session) : null}>
      <Suspense fallback={null}>
        <PathWatcher onChange={setPath} />
      </Suspense>
      {signedOut ? <SignIn /> : children}
    </MeContext.Provider>
  );
}

function SignIn() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="relative grid min-h-dvh place-items-center px-4">
      <AmbientGlow state="ready" />
      <div className="relative z-10 flex w-full max-w-sm flex-col items-center text-center">
        <StatusGlyph state="ready" size={32} label="Sakhi" />
        <h1 className="mt-4 font-serif text-[2.5rem] leading-none tracking-[-0.01em] text-ink">Sakhi</h1>
        <p className="mt-3 text-body text-ink-2">Tell her what you want. She makes the call.</p>
        <button
          type="button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await signInWithGoogle();
            } catch {
              setError("Couldn't reach Google. Try again.");
              setBusy(false);
            }
          }}
          className="mt-10 inline-flex h-12 items-center gap-3 rounded-full bg-ink px-6 text-body font-medium text-bg transition-[transform,opacity] hover:opacity-90 active:scale-[0.98] disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
        >
          <GoogleMark />
          {busy ? "Opening Google…" : "Continue with Google"}
        </button>
        <p role="alert" className="mt-3 min-h-5 text-meta text-error">
          {error}
        </p>
        <EmailSignIn onError={setError} />
      </div>
    </div>
  );
}

const field =
  "h-12 w-full rounded-full bg-surface px-5 text-body text-ink ring-1 ring-line placeholder:text-ink-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora";

/** Accounts made by an admin (e.g. a reviewer's test login) sign in here; nobody signs up here. */
function EmailSignIn({ onError }: { onError: (e: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) emailRef.current?.focus();
  }, [open]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 rounded-full px-3 py-1.5 text-meta text-ink-2 underline-offset-4 hover:text-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora"
      >
        Sign in with email
      </button>
    );
  }
  return (
    <form
      className="mt-2 flex w-full flex-col gap-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        onError(null);
        let ok = false;
        try {
          ok = await signInWithPassword(email.trim(), password);
        } catch {
          ok = false;
        }
        if (!ok) {
          onError("That email and password don't match.");
          setBusy(false);
        }
      }}
    >
      <label htmlFor="signin-email" className="sr-only">
        Email
      </label>
      <input
        id="signin-email"
        ref={emailRef}
        type="email"
        autoComplete="email"
        required
        placeholder="Email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className={field}
      />
      <label htmlFor="signin-password" className="sr-only">
        Password
      </label>
      <input
        id="signin-password"
        type="password"
        autoComplete="current-password"
        required
        placeholder="Password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        className={field}
      />
      <button
        type="submit"
        disabled={busy}
        className="h-12 rounded-full px-6 text-body font-medium text-aurora ring-1 ring-aurora/40 transition-colors hover:bg-aurora-soft disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" className="size-5" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}
