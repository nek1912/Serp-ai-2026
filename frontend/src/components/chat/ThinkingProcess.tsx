"use client";

import { useState, useEffect } from "react";
import { IconChevronDown } from "@/components/ui/Icons";
import type { StepEvent } from "@/lib/api";

interface ThinkingProcessProps {
  steps: StepEvent[];
  lang?: string;
  isStreaming: boolean;
}

const STEP_ORDER = ["retrieval_start", "static_done", "web_done", "evidence_merge", "llm_generate", "citation_verify"];

function Spinner() {
  return (
    <svg className="h-3.5 w-3.5 animate-spin text-[var(--primary)]" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="h-3.5 w-3.5 text-[var(--success, #22c55e)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function sortSteps(steps: StepEvent[]): StepEvent[] {
  return [...steps].sort((a, b) => {
    const ai = STEP_ORDER.indexOf(a.id);
    const bi = STEP_ORDER.indexOf(b.id);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });
}

export function ThinkingProcess({ steps, isStreaming }: ThinkingProcessProps) {
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    if (isStreaming) setExpanded(false);
  }, [isStreaming]);

  const sorted = sortSteps(steps);
  const allComplete = sorted.length > 0 && sorted.every((s) => s.status === "completed");
  const completedCount = sorted.filter((s) => s.status === "completed").length;

  if (sorted.length === 0) return null;

  const summaryText = allComplete
    ? `${completedCount} steps complete`
    : sorted.find((s) => s.status === "active")?.label || "Processing...";

  return (
    <div className="flex gap-3">
      <div className="flex flex-col gap-1 min-w-0">
        <div className="rounded-2xl bg-[var(--surface-card)] px-4 py-3">
          {isStreaming && !expanded && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="flex items-center gap-2 w-full text-left group"
            >
              <CheckIcon />
              <span className="text-sm font-medium text-[var(--body)] truncate">{summaryText}</span>
              <IconChevronDown className="h-3.5 w-3.5 text-[var(--muted-soft)] transition-transform duration-200 group-hover:text-[var(--body)]" />
            </button>
          )}

          {(!isStreaming || expanded) && (
            <div className="flex flex-col gap-1.5">
              {sorted.map((step) => (
                <div key={step.id} className="flex items-start gap-2 animate-in fade-in slide-in-from-top-1 duration-300">
                  <span className="mt-0.5 shrink-0">
                    {step.status === "active" && <Spinner />}
                    {step.status === "completed" && <CheckIcon />}
                    {step.status === "pending" && (
                      <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-[var(--muted-soft)] opacity-40" />
                    )}
                  </span>
                  <div className="flex flex-col min-w-0">
                    <span className="text-sm font-medium text-[var(--body)]">{step.label}</span>
                    {step.detail && (
                      <span className="text-xs text-[var(--muted-soft)] truncate">{step.detail}</span>
                    )}
                  </div>
                </div>
              ))}

              {isStreaming && (
                <button
                  type="button"
                  onClick={() => setExpanded(false)}
                  className="flex items-center gap-1 mt-1 text-xs text-[var(--muted-soft)] hover:text-[var(--body)] transition-colors self-start"
                >
                  <IconChevronDown className="h-3 w-3 rotate-180" />
                  <span>Collapse</span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
