"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ServiceWorkerRegister } from "@/components/pwa/service-worker-register";
import { ToastProvider } from "@/components/ui/toast";
import { useNavigationDepthTracker } from "@/lib/navigation";
import { createQueryClient } from "@/lib/query-client";

/** アプリ全体のクライアント側プロバイダー（React Query・トースト・SW 登録・戻る履歴の追跡） */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);
  useNavigationDepthTracker();

  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        {children}
        <ServiceWorkerRegister />
      </ToastProvider>
    </QueryClientProvider>
  );
}
