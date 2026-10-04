"use client";
import { Suspense } from "react";
import { ChatWindow } from "@/components/ChatWindow";

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="py-10 text-center text-[var(--text-secondary)]">Loading chat…</div>}>
      <ChatWindow />
    </Suspense>
  );
}
