/** Phone numbers as Sakhi dials them (E.164), mirroring the backend's rules. */
export function normalizePhone(raw: string): string | null {
  const s = (raw || "").replace(/[^\d+]/g, "");
  let num: string;
  if (s.startsWith("+")) num = "+" + s.slice(1).replace(/\D/g, "");
  else {
    const d = s.replace(/\D/g, "").replace(/^0+/, "");
    num = d.length === 10 ? `+91${d}` : d.length === 12 && d.startsWith("91") ? `+${d}` : `+${d}`;
  }
  if (!/^\+\d{8,15}$/.test(num)) return null;
  if (num.startsWith("+91") && !/^\+91[6-9]\d{9}$/.test(num)) return null;
  return num;
}

/** +919876543210 -> +91 98765 43210 */
export function formatPhone(e164: string | null | undefined): string {
  if (!e164) return "";
  const m = /^\+91(\d{5})(\d{5})$/.exec(e164);
  return m ? `+91 ${m[1]} ${m[2]}` : e164;
}

interface ContactsManager {
  select(props: string[], opts?: { multiple?: boolean }): Promise<{ name?: string[]; tel?: string[] }[]>;
}

/** The Contact Picker API (Chrome on Android). Elsewhere: null. */
export function contactPicker(): ContactsManager | null {
  if (typeof navigator === "undefined") return null;
  const c = (navigator as Navigator & { contacts?: ContactsManager }).contacts;
  return c && typeof c.select === "function" ? c : null;
}
