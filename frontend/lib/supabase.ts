"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/** Browser Supabase client, used only for sign-in. `null` when unset (demo: no sign-in). */
export const supabase: SupabaseClient | null =
  url && anonKey
    ? createClient(url, anonKey, {
        auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      })
    : null;

/** The current access token, refreshed by supabase-js when it's close to expiring. */
export async function accessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function signInWithGoogle() {
  if (!supabase) return;
  const here = window.location.origin + window.location.pathname + window.location.search;
  await supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo: here } });
}

/** Email and password sign-in, for accounts created by an admin (no sign-up form). */
export async function signInWithPassword(email: string, password: string): Promise<boolean> {
  if (!supabase) return false;
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  return !error;
}
