import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Terms, privacy and refunds · Sakhi" };

// Review before going live: replace the bracketed details with your own.
const CONTACT_EMAIL = "[support email]";
const BUSINESS = "[your name or business name]";
const ADDRESS = "[business address, city, India]";

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-8 space-y-3">
      <h2 className="font-serif text-[1.75rem] leading-tight text-ink">{title}</h2>
      <div className="space-y-3 text-body leading-relaxed text-ink-2">{children}</div>
    </section>
  );
}

export default function Legal() {
  return (
    <main className="mx-auto w-full max-w-2xl px-4 pb-24 pt-12 sm:px-6">
      <Link href="/" className="text-meta text-ink-3 hover:text-ink-2">
        ← Sakhi
      </Link>
      <h1 className="mt-6 font-serif text-[2.75rem] leading-none text-ink">Terms, privacy and refunds</h1>
      <nav className="mt-6 flex flex-wrap gap-x-4 gap-y-1 text-meta text-ink-3" aria-label="Sections">
        <a href="#terms" className="hover:text-ink-2">Terms</a>
        <a href="#privacy" className="hover:text-ink-2">Privacy</a>
        <a href="#refunds" className="hover:text-ink-2">Cancellation and refunds</a>
        <a href="#contact" className="hover:text-ink-2">Contact</a>
      </nav>

      <div className="mt-12 space-y-14">
        <Section id="terms" title="Terms">
          <p>
            Sakhi is operated by {BUSINESS}. It places conversations on your behalf, using an AI voice agent that
            follows the goals and limits you set. You are responsible for what you ask it to do, and for using it lawfully
            and respectfully toward the people it speaks with.
          </p>
          <p>
            The agent never claims to be human, and will say it is an AI when asked. It will not agree to anything
            beyond the limits you set without asking you first. Outcomes of a negotiation are not guaranteed.
          </p>
          <p>
            Your first mission is free. After that, Sakhi is a monthly subscription, billed in advance through Razorpay
            and renewed automatically each month until you cancel. Fair use applies to talk time.
          </p>
        </Section>

        <Section id="privacy" title="Privacy">
          <p>
            We store your profile, your missions, and the transcripts and outcomes of your conversations so you can
            review them. Speech is processed by our speech provider (Sarvam AI) and language by our AI model providers,
            only to run your conversations. We don&apos;t sell your data.
          </p>
          <p>
            You choose which profile details the agent may share, and when. Payments are handled by Razorpay; we never
            see or store your card or UPI details. Sign-in is through Google. To delete your account and data, write to
            us at {CONTACT_EMAIL}.
          </p>
        </Section>

        <Section id="refunds" title="Cancellation and refunds">
          <p>
            You can cancel anytime from your profile. Cancelling stops future renewals; Sakhi keeps working until the
            end of the period you&apos;ve paid for.
          </p>
          <p>
            If you were charged by mistake, or Sakhi didn&apos;t work for you because of a fault on our side, write to
            us within 7 days of the charge and we&apos;ll refund it. Approved refunds reach your original payment method
            within 5–7 working days.
          </p>
        </Section>

        <Section id="contact" title="Contact">
          <p>
            {BUSINESS}
            <br />
            {ADDRESS}
            <br />
            {CONTACT_EMAIL}
          </p>
        </Section>
      </div>
    </main>
  );
}
