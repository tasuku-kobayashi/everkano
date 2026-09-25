"use client";

import { Button } from "@/components/ui/button";

export function ReloadButton() {
  return (
    <Button className="mt-6" onClick={() => window.location.replace("/")}>
      再読み込み
    </Button>
  );
}
