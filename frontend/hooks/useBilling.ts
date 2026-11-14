"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/services/api";

/** Plan state from the backend. Dates only: talk time is never exposed. */
export function useBilling() {
  const query = useQuery({ queryKey: ["billing"], queryFn: api.billing });
  const status = query.data?.enabled ? query.data.status : null;
  return {
    billing: query.data,
    subscribed: status === "active" || status === "exhausted",
    onTrial: status === "trial" || status === "trial_used",
    firstVisit: status === "trial",
  };
}
