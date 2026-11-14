"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError } from "@/services/api";
import { closePaywall, paywallStore } from "@/lib/paywall";
import { useMe } from "@/components/shell/AuthGate";
import { StatusGlyph } from "@/components/orb/StatusGlyph";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";

interface RazorpayResponse {
  razorpay_payment_id: string;
  razorpay_subscription_id: string;
  razorpay_signature: string;
}
interface RazorpayCheckout {
  open(): void;
  on(event: "payment.failed", cb: (r: { error?: { description?: string } }) => void): void;
}
declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => RazorpayCheckout;
  }
}

let checkoutScript: Promise<void> | null = null;
function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  checkoutScript ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve();
    s.onerror = () => {
      checkoutScript = null;
      reject(new Error("Couldn't load Razorpay. Check your connection."));
    };
    document.body.appendChild(s);
  });
  return checkoutScript;
}

/** Razorpay Checkout for the subscription; resolves when Envoy has confirmed the payment. */
function pay(sub: { subscription_id: string; key_id: string }, me: { name: string | null; email: string | null } | null) {
  return new Promise<RazorpayResponse | null>((resolve, reject) => {
    const rzp = new window.Razorpay!({
      key: sub.key_id,
      subscription_id: sub.subscription_id,
      name: "Envoy",
      description: "Monthly plan",
      prefill: { name: me?.name ?? undefined, email: me?.email ?? undefined },
      theme: { color: "#9db0ff" },
      handler: (r: RazorpayResponse) => resolve(r),
      modal: { ondismiss: () => resolve(null), confirm_close: true },
    });
    rzp.on("payment.failed", (r) => reject(new Error(r.error?.description ?? "The payment didn't go through.")));
    rzp.open();
  });
}

export function PaywallSheet() {
  const open = useSyncExternalStore(paywallStore.subscribe, paywallStore.get, paywallStore.getServer);
  const qc = useQueryClient();
  const me = useMe();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const price = qc.getQueryData<{ price_inr: number }>(["billing"])?.price_inr ?? 499;

  const subscribe = async () => {
    setBusy(true);
    setError(null);
    try {
      await loadCheckout();
      const sub = await api.subscribe();
      const result = await pay(sub, me);
      if (!result) return; // closed Razorpay; the sheet stays open
      const billing = await api.verifyPayment(result);
      qc.setQueryData(["billing"], billing);
      toast.success("You're in");
      closePaywall(true);
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => {
        if (!o && !busy) {
          setError(null);
          closePaywall(false);
        }
      }}
    >
      <SheetContent
        side="bottom"
        className="mx-auto w-full max-w-md items-center gap-0 rounded-t-[2rem] border-0 bg-surface px-6 pb-[calc(1.75rem+env(safe-area-inset-bottom))] pt-9 text-center ring-1 ring-aurora/25 sm:bottom-6 sm:rounded-[2rem]"
      >
        <StatusGlyph state="ready" size={32} label="" />
        <SheetTitle className="mt-5 font-serif text-[2rem] font-normal leading-[1.05] text-ink">
          Keep Envoy working for you
        </SheetTitle>
        <SheetDescription className="mt-3 text-body text-ink-2">Unlimited missions. Cancel anytime.</SheetDescription>
        <p className="mt-7 font-serif text-[3rem] leading-none text-ink tabular">
          ₹{price}
          <span className="ml-1 font-sans text-body text-ink-3">/ month</span>
        </p>
        <button
          type="button"
          onClick={subscribe}
          disabled={busy}
          className="mt-8 h-12 w-full rounded-full bg-ink text-body font-medium text-bg transition-[transform,opacity] hover:opacity-90 active:scale-[0.98] disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
        >
          {busy ? "Opening Razorpay…" : "Subscribe"}
        </button>
        <p role="alert" className="mt-3 min-h-5 text-meta text-error">
          {error}
        </p>
        <p className="mt-1 text-meta text-ink-3">
          Secure payment by Razorpay ·{" "}
          <Link href="/legal" className="underline decoration-line underline-offset-4 hover:text-ink-2" onClick={() => closePaywall(false)}>
            Terms &amp; refunds
          </Link>
        </p>
      </SheetContent>
    </Sheet>
  );
}
