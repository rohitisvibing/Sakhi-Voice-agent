"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthGate } from "./AuthGate";
import { PaywallSheet } from "@/components/billing/PaywallSheet";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5_000 } } }),
  );
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <AuthGate>
          {children}
          <PaywallSheet />
        </AuthGate>
        <Toaster position="top-center" theme="dark" />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
