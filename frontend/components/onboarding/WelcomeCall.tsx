"use client";

import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Phone, PhoneOff, Volume2, VolumeOff } from "lucide-react";
import type { ConversationState } from "@/types/api";
import { AgentOrb } from "@/components/orb/AgentOrb";
import { AmbientGlow } from "@/components/shell/AmbientGlow";
import { useMe } from "@/components/shell/AuthGate";
import { openPaywall } from "@/lib/paywall";
import { cn } from "@/lib/utils";
import { useWelcomeVoice } from "./useWelcomeVoice";

type Step = "ringing" | "speaking" | "reply";
export type WelcomeChoice = "start" | "subscribed";

function clock(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Envoy's first move is to call you: ring, answer, it speaks, you reply. */
export function WelcomeCall({ price, onClose }: { price: number; onClose: (choice: WelcomeChoice) => void }) {
  const me = useMe();
  const reduce = useReducedMotion();
  const voice = useWelcomeVoice();
  const [picked, setStep] = useState<Step>("ringing");
  const step: Step = picked === "speaking" && voice.done ? "reply" : picked;
  const [seconds, setSeconds] = useState(0);
  const [answered, setAnswered] = useState(false);
  const [paying, setPaying] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const answerRef = useRef<HTMLButtonElement>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  const first = me?.name?.split(" ")[0];

  const answer = () => {
    setAnswered(true);
    setStep("speaking");
    void voice.play();
  };
  const later = () => {
    voice.stop();
    setStep("reply");
  };
  const subscribe = async () => {
    setPaying(true);
    const ok = await openPaywall();
    setPaying(false);
    if (ok) onClose("subscribed");
  };

  useEffect(() => {
    if (!answered) return;
    const t = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [answered]);

  useEffect(() => {
    (step === "ringing" ? answerRef : step === "reply" ? startRef : null)?.current?.focus();
  }, [step]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !paying) {
        e.preventDefault();
        if (step === "reply") onClose("start");
        else later();
      }
      if (e.key === "Tab" && root.current) {
        const items = root.current.querySelectorAll<HTMLElement>("button:not([disabled])");
        if (!items.length) return;
        const firstEl = items[0];
        const lastEl = items[items.length - 1];
        if (e.shiftKey && document.activeElement === firstEl) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          firstEl.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const orbState: ConversationState = step === "ringing" ? "connecting" : step === "speaking" ? "speaking" : "ready";
  const spoken = voice.lines.map((l, i) => (i === 0 && first ? `Hi ${first}, I'm Envoy.` : l.text));
  const said = step === "reply" ? spoken.length - 1 : voice.line;

  return (
    <motion.div
      ref={root}
      role="dialog"
      aria-modal="true"
      aria-labelledby="welcome-title"
      className="fixed inset-0 z-40 flex flex-col overflow-y-auto bg-bg"
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 1.02 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96, filter: "blur(6px)" }}
      transition={{ duration: 0.45, ease: [0.2, 0.7, 0.2, 1] }}
    >
      <AmbientGlow state={orbState} levelRef={voice.levelRef} />

      {step === "ringing" ? (
        <div className="relative z-10 mx-auto flex w-full max-w-sm flex-1 flex-col items-center justify-between px-6 pb-[calc(3rem+env(safe-area-inset-bottom))] pt-[calc(3.5rem+env(safe-area-inset-top))] text-center">
          <p className="text-meta text-ink-3">Envoy</p>
          <div className="flex flex-col items-center">
            <div className="relative grid size-52 place-items-center">
              {!reduce &&
                [0, 0.8].map((delay) => (
                  <motion.span
                    key={delay}
                    aria-hidden
                    className="absolute inset-6 rounded-full ring-1 ring-aurora/50"
                    initial={{ scale: 0.9, opacity: 0 }}
                    animate={{ scale: [0.9, 1.45], opacity: [0.55, 0] }}
                    transition={{ duration: 1.6, delay, repeat: Infinity, ease: "easeOut" }}
                  />
                ))}
              <div className="size-44">
                <AgentOrb state="connecting" label="Envoy is calling" />
              </div>
            </div>
            <h1 id="welcome-title" className="mt-8 font-serif text-[2.5rem] leading-none tracking-[-0.01em] text-ink">
              Envoy is calling
            </h1>
            {first && <p className="mt-3 text-body text-ink-2">Hi, {first}</p>}
          </div>
          <div className="flex w-full items-start justify-around">
            <RoundButton label="Later" onClick={later} tone="decline">
              <PhoneOff className="size-6" />
            </RoundButton>
            <RoundButton label="Answer" onClick={answer} tone="answer" buttonRef={answerRef}>
              <Phone className="size-6" />
            </RoundButton>
          </div>
        </div>
      ) : (
        <div className="relative z-10 mx-auto flex w-full max-w-md flex-1 flex-col px-6 pb-[calc(2.5rem+env(safe-area-inset-bottom))] pt-[calc(1.5rem+env(safe-area-inset-top))]">
          <header className="flex items-center gap-3">
            <div className="size-12 shrink-0">
              <AgentOrb state={orbState} levelRef={voice.levelRef} label="Envoy" />
            </div>
            <h1 id="welcome-title" className="text-body font-medium text-ink">
              Envoy
            </h1>
            {answered && (
              <span className="text-meta text-ink-3 tabular" aria-label={`Call time ${clock(seconds)}`}>
                {clock(seconds)}
              </span>
            )}
            {step === "speaking" && (
              <button
                type="button"
                onClick={() => voice.setMuted(!voice.muted)}
                aria-label={voice.muted ? "Unmute Envoy" : "Mute Envoy"}
                aria-pressed={voice.muted}
                className="ml-auto grid size-10 place-items-center rounded-full text-ink-2 transition-colors hover:bg-elevated hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora"
              >
                {voice.muted ? <VolumeOff className="size-5" /> : <Volume2 className="size-5" />}
              </button>
            )}
          </header>

          <div className="flex flex-1 flex-col justify-center py-10" aria-live="polite">
            {spoken.map((text, i) => {
              if (i > said) return null;
              const words = text.split(" ");
              const live = i === said && step === "speaking";
              const shown = live && !reduce ? Math.ceil(voice.progress * words.length) : words.length;
              return (
                <p
                  key={i}
                  className={cn(
                    "max-w-[32ch] text-lead leading-relaxed transition-colors duration-700",
                    i > 0 && "mt-4",
                    live ? "text-ink" : "text-ink-2",
                    i === spoken.length - 1 && step === "reply" && "text-ink",
                  )}
                >
                  {words.map((w, k) => (
                    <span key={k} className={cn("transition-opacity duration-200", k < shown ? "opacity-100" : "opacity-0")}>
                      {w}
                      {k < words.length - 1 ? " " : ""}
                    </span>
                  ))}
                </p>
              );
            })}
          </div>

          <motion.div
            className="flex flex-col items-end gap-2.5"
            initial={false}
            animate={step === "reply" ? { opacity: 1, y: 0 } : { opacity: 0, y: reduce ? 0 : 10 }}
            transition={{ duration: 0.4, ease: [0.2, 0.7, 0.2, 1] }}
            aria-hidden={step !== "reply"}
          >
            <button
              ref={startRef}
              type="button"
              disabled={step !== "reply" || paying}
              onClick={() => onClose("start")}
              className="h-12 rounded-full bg-ink px-6 text-body font-medium text-bg transition-[transform,opacity] hover:opacity-90 active:scale-[0.98] disabled:pointer-events-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            >
              Start my free mission
            </button>
            <button
              type="button"
              disabled={step !== "reply" || paying}
              onClick={subscribe}
              className="h-12 rounded-full px-6 text-body text-aurora ring-1 ring-aurora/40 transition-colors hover:bg-aurora-soft disabled:pointer-events-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora"
            >
              Subscribe for ₹{price} a month
            </button>
          </motion.div>
        </div>
      )}
    </motion.div>
  );
}

function RoundButton({
  label,
  tone,
  onClick,
  children,
  buttonRef,
}: {
  label: string;
  tone: "answer" | "decline";
  onClick: () => void;
  children: React.ReactNode;
  buttonRef?: React.Ref<HTMLButtonElement>;
}) {
  return (
    <div className="flex flex-col items-center gap-2.5">
      <button
        ref={buttonRef}
        type="button"
        onClick={onClick}
        aria-label={label}
        className={cn(
          "grid size-16 place-items-center rounded-full transition-[transform,background-color] active:scale-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          tone === "answer"
            ? "bg-aurora text-bg shadow-[0_0_48px_-8px_rgb(157_176_255/0.8)] hover:bg-[#b3c2ff] focus-visible:ring-aurora"
            : "text-ink-2 ring-1 ring-line hover:bg-elevated hover:text-ink focus-visible:ring-ink-2",
        )}
      >
        {children}
      </button>
      <span aria-hidden className="text-meta text-ink-3">
        {label}
      </span>
    </div>
  );
}
