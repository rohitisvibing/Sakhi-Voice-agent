"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { openPaywall } from "@/lib/paywall";

/**
 * The free line, shown like an in-call pill until you subscribe. Subscribing hangs it up:
 * it folds into its dot and the dot goes out.
 */
export function CallPill({ show, used, price }: { show: boolean; used: boolean; price: number }) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          key="pill"
          className="fixed inset-x-0 bottom-[calc(1.25rem+env(safe-area-inset-bottom))] z-30 flex justify-center px-4"
          initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, transition: { delay: reduce ? 0 : 0.45, duration: 0.25 } }}
          transition={{ duration: 0.4, ease: [0.2, 0.7, 0.2, 1] }}
        >
          <motion.div
            layout
            className="flex h-11 items-center gap-3 overflow-hidden rounded-full bg-surface/90 pl-4 pr-1.5 text-meta shadow-[0_12px_40px_-12px_rgb(0_0_0/0.6)] ring-1 ring-line backdrop-blur-md"
            exit={reduce ? undefined : { width: 44, paddingRight: 0, transition: { duration: 0.4, ease: [0.6, 0, 0.4, 1] } }}
          >
            <span aria-hidden className="relative grid size-2.5 shrink-0 place-items-center">
              {!reduce && <span className="absolute inset-0 animate-ping rounded-full bg-aurora/50 [animation-duration:2.4s]" />}
              <span className="size-2 rounded-full bg-aurora" />
            </span>
            <motion.span className="whitespace-nowrap text-ink-2" exit={{ opacity: 0, transition: { duration: 0.15 } }}>
              {used ? "Free mission used" : "First mission free"}
            </motion.span>
            <motion.button
              type="button"
              onClick={() => void openPaywall()}
              exit={{ opacity: 0, transition: { duration: 0.15 } }}
              className="h-8 whitespace-nowrap rounded-full px-3.5 font-medium text-aurora transition-colors hover:bg-aurora-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-aurora"
            >
              {used ? `Subscribe for ₹${price}/month` : "Subscribe"}
            </motion.button>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
