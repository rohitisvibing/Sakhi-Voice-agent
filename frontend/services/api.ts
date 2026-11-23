import type {
  AgentTemplate,
  AppErrorPayload,
  AuthSession,
  Billing,
  Conversation,
  ConversationDetail,
  ConversationMode,
  Health,
  Mission,
  MissionSpec,
  MissionSummary,
  OutcomeReport,
  PrincipalProfile,
  Recipient,
} from "@/types/api";
import { accessToken, supabase } from "@/lib/supabase";
import { openPaywall } from "@/lib/paywall";

// Empty means "same origin" (deployed: the backend is served under /api and /ws).
export const API_URL = (
  process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "production" ? "" : "http://localhost:8000")
).replace(/\/$/, "");

export function wsUrl(path: string): string {
  const base = API_URL || (typeof window !== "undefined" ? window.location.origin : "");
  return base.replace(/^http/, "ws") + path;
}

/** The backend rejected our sign-in: drop the local session so the sign-in screen returns. */
export function signedOut() {
  void supabase?.auth.signOut({ scope: "local" });
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public payload: AppErrorPayload,
  ) {
    super(payload.message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  const token = await accessToken();
  try {
    res = await fetch(API_URL + path, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, {
      kind: "network",
      message: API_URL
        ? `Can't reach the backend at ${API_URL}. Start it with: cd backend && uv run uvicorn app.main:app --reload`
        : "Can't reach the server. Check your connection and try again.",
    });
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const payload: AppErrorPayload =
      body?.error ??
      (Array.isArray(body?.detail)
        ? { kind: "validation", message: body.detail.map((d: { msg: string }) => d.msg).join("; ") }
        : { kind: "http", message: body?.detail ?? `Request failed (${res.status})` });
    if (payload.kind === "unauthorized" && !path.startsWith("/api/auth/")) signedOut();
    // Needs a subscription: show the payment sheet, and once paid, carry on as if nothing happened.
    if (payload.kind === "payment_required" && payload.reason === "subscribe" && !path.startsWith("/api/billing")) {
      if (await openPaywall()) return request<T>(path, init);
    }
    throw new ApiError(res.status, payload);
  }
  return body as T;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  health: () => request<Health>("/api/health"),
  session: () => request<AuthSession>("/api/auth/session"),
  billing: () => request<Billing>("/api/billing"),
  subscribe: () => request<{ subscription_id: string; key_id: string }>("/api/billing/subscribe", { method: "POST" }),
  verifyPayment: (body: { razorpay_payment_id: string; razorpay_subscription_id: string; razorpay_signature: string }) =>
    request<Billing>("/api/billing/verify", { method: "POST", body: json(body) }),
  cancelSubscription: () => request<Billing>("/api/billing/cancel", { method: "POST" }),
  profile: () => request<PrincipalProfile>("/api/profile"),
  saveProfile: (p: Partial<PrincipalProfile>) =>
    request<PrincipalProfile>("/api/profile", { method: "PUT", body: json(p) }),
  templates: () => request<AgentTemplate[]>("/api/templates"),
  transcribe: (wav: Blob) => {
    const form = new FormData();
    form.append("audio", wav, "request.wav");
    return request<{ text: string; language: string | null }>("/api/transcribe", { method: "POST", body: form });
  },
  missions: () => request<MissionSummary[]>("/api/missions"),
  mission: (id: string) => request<Mission>(`/api/missions/${id}`),
  extract: (body: { text: string; counterparty_type?: string; languages?: string[]; tone?: string; template_id?: string }) =>
    request<Mission>("/api/missions/extract", { method: "POST", body: json(body) }),
  refine: (id: string, answers: Record<string, string>) =>
    request<Mission>(`/api/missions/${id}/refine`, { method: "POST", body: json({ answers }) }),
  updateSpec: (id: string, spec: MissionSpec) =>
    request<Mission>(`/api/missions/${id}/spec`, { method: "PUT", body: json(spec) }),
  approve: (id: string) => request<Mission>(`/api/missions/${id}/approve`, { method: "POST" }),
  prepare: (id: string) => request<Mission>(`/api/missions/${id}/prepare`, { method: "POST" }),
  deleteMission: (id: string) => request<void>(`/api/missions/${id}`, { method: "DELETE" }),
  createConversation: (mission_id: string, mode: ConversationMode, voice_enabled = true, recipient?: Recipient) =>
    request<Conversation>("/api/conversations", {
      method: "POST",
      body: json({ mission_id, mode, voice_enabled, recipient }),
    }),
  dial: (id: string) => request<Conversation>(`/api/conversations/${id}/dial`, { method: "POST", body: json({ confirm: true }) }),
  recentRecipients: () => request<Recipient[]>("/api/recipients/recent"),
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),
  report: (id: string) => request<OutcomeReport>(`/api/conversations/${id}/report`),
};
