"use client";
import { useState } from "react";
import { useI18n } from "@/lib/i18n/provider";
import {
  detectGrievance,
  getGrievanceFields,
  answerGrievanceField,
  finalizeGrievanceDraft,
  type GrievanceFieldSpec,
  type Grievance,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { IconAlertTriangle, IconChevronRight } from "@/components/ui/Icons";

/**
 * Structured, form-driven Grievance UI.
 *
 * Flow:
 *  1. Free-text complaint -> backend detects category/sub-category/
 *     jurisdiction/title/description -> shown in a confirmation panel
 *     with Yes/No.
 *  2. "No" reopens the text box; the corrected complaint is sent as a
 *     fresh conversation, replacing the previous detection.
 *  3. "Yes" opens a tabbed panel: one tab per field, tagged Mandatory
 *     or Optional, each rendering the input control (int/date/text)
 *     the backend says that field needs. Submit is only enabled once
 *     every mandatory tab has a value.
 *  4. On submit, every answer is posted to the backend, which returns
 *     the final canonical draft — rendered here as a table, with the
 *     "draft only" disclaimer, a possible multi-language notice, and
 *     the location-aware official portal link.
 */

type Phase = "intake" | "confirm" | "fields" | "final";

function newId() {
  return crypto.randomUUID();
}

export function GrievanceWizard() {
  const { t, locale } = useI18n();
  const [phase, setPhase] = useState<Phase>("intake");
  const [conversationId, setConversationId] = useState(() => newId());
  const [userId] = useState(() => newId());
  const [complaintText, setComplaintText] = useState("");
  const [revising, setRevising] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [draftSummary, setDraftSummary] = useState<{
    category: string;
    sub_category: string;
    title: string;
    description: string;
    jurisdiction: string;
    department: string;
  } | null>(null);

  const [mandatoryFields, setMandatoryFields] = useState<GrievanceFieldSpec[]>([]);
  const [optionalFields, setOptionalFields] = useState<GrievanceFieldSpec[]>([]);
  const [showOptional, setShowOptional] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});

  const [finalGrievance, setFinalGrievance] = useState<Grievance | null>(null);
  const [mixedLanguage, setMixedLanguage] = useState(false);

  async function submitComplaint(text: string, freshConversation: boolean) {
    if (!text.trim()) return;
    setLoading(true);
    setError(null);
    const cid = freshConversation ? newId() : conversationId;
    try {
      const res = await detectGrievance({
        message: text.trim(),
        conversation_id: cid,
        user_id: userId,
      });
      if (!res.draft) {
        setError(t("common.retry"));
        setLoading(false);
        return;
      }
      setConversationId(cid);
      setDraftSummary({
        category: res.draft.category,
        sub_category: res.draft.sub_category,
        title: res.draft.title,
        description: res.draft.description,
        jurisdiction: res.draft.jurisdiction,
        department: res.draft.department,
      });
      setRevising(false);
      setPhase("confirm");
    } catch {
      setError(t("common.retry"));
    } finally {
      setLoading(false);
    }
  }

  async function confirmYes() {
    setLoading(true);
    setError(null);
    try {
      const res = await getGrievanceFields(conversationId, locale);
      setMandatoryFields(res.mandatory_fields);
      setOptionalFields(res.optional_fields);
      const seeded: Record<string, string> = {};
      for (const f of [...res.mandatory_fields, ...res.optional_fields]) {
        if (f.value) seeded[f.field] = f.value;
      }
      setAnswers(seeded);
      setActiveIndex(0);
      setShowOptional(false);
      setPhase("fields");
    } catch {
      setError(t("common.retry"));
    } finally {
      setLoading(false);
    }
  }

  function confirmNo() {
    setRevising(true);
  }

  const activeList = showOptional ? optionalFields : mandatoryFields;
  const activeField = activeList[activeIndex];
  const mandatoryComplete = mandatoryFields.every((f) => (answers[f.field] ?? "").trim().length > 0);

  async function saveActiveAnswer(value: string) {
    if (!activeField) return;
    setAnswers((prev) => ({ ...prev, [activeField.field]: value }));
    try {
      await answerGrievanceField({ conversation_id: conversationId, field: activeField.field, value });
    } catch {
      // Non-fatal — the value is still kept locally and resent on submit tabs.
    }
  }

  async function finalize() {
    setLoading(true);
    setError(null);
    try {
      const res = await finalizeGrievanceDraft({ conversation_id: conversationId, language: locale });
      setFinalGrievance(res.grievance);
      setMixedLanguage(res.mixed_language);
      setPhase("final");
    } catch {
      setError(t("common.retry"));
    } finally {
      setLoading(false);
    }
  }

  function startOver() {
    setPhase("intake");
    setConversationId(newId());
    setComplaintText("");
    setDraftSummary(null);
    setMandatoryFields([]);
    setOptionalFields([]);
    setAnswers({});
    setFinalGrievance(null);
    setMixedLanguage(false);
    setError(null);
  }

  // ---- Phase: intake -----------------------------------------------
  if (phase === "intake") {
    return (
      <Card className="mx-auto max-w-2xl p-6 md:p-8">
        <h2 className="font-semibold text-[var(--text-primary)]">{t("grievanceWizard.startPrompt")}</h2>
        <textarea
          className="mt-4 w-full rounded-[var(--radius-md)] border border-[var(--border-soft)] bg-[var(--surface-base)] p-3 text-sm text-[var(--ink)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
          rows={5}
          placeholder={t("grievanceWizard.complaintPlaceholder")}
          value={complaintText}
          onChange={(e) => setComplaintText(e.target.value)}
        />
        {error && <p className="mt-2 text-sm text-[var(--state-error)]">{error}</p>}
        <div className="mt-4 flex justify-end">
          <Button
            variant="primary"
            disabled={loading || !complaintText.trim()}
            loading={loading}
            onClick={() => submitComplaint(complaintText, true)}
          >
            {t("common.submit")}
            <IconChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </Card>
    );
  }

  // ---- Phase: confirm -------------------------------------------------
  if (phase === "confirm" && draftSummary) {
    return (
      <Card className="mx-auto max-w-2xl p-6 md:p-8">
        <h2 className="font-semibold text-[var(--text-primary)]">{t("grievanceWizard.confirmTitle")}</h2>
        <table className="mt-4 w-full text-sm">
          <tbody>
            <tr className="border-b border-[var(--border-soft)]">
              <td className="py-2 pr-4 font-medium text-[var(--text-faint)]">{t("grievanceCard.titleLabel")}</td>
              <td className="py-2 text-[var(--ink)]">{draftSummary.title}</td>
            </tr>
            <tr className="border-b border-[var(--border-soft)]">
              <td className="py-2 pr-4 font-medium text-[var(--text-faint)]">{t("grievanceCard.category")}</td>
              <td className="py-2 text-[var(--ink)]">{draftSummary.category}</td>
            </tr>
            <tr className="border-b border-[var(--border-soft)]">
              <td className="py-2 pr-4 font-medium text-[var(--text-faint)]">{t("grievanceCard.subCategory")}</td>
              <td className="py-2 text-[var(--ink)]">{draftSummary.sub_category}</td>
            </tr>
            <tr className="border-b border-[var(--border-soft)]">
              <td className="py-2 pr-4 font-medium text-[var(--text-faint)]">{t("grievanceCard.jurisdiction")}</td>
              <td className="py-2 text-[var(--ink)]">{draftSummary.jurisdiction}</td>
            </tr>
            <tr>
              <td className="py-2 pr-4 align-top font-medium text-[var(--text-faint)]">{t("grievanceCard.description")}</td>
              <td className="py-2 text-[var(--ink)]">{draftSummary.description}</td>
            </tr>
          </tbody>
        </table>

        {!revising ? (
          <div className="mt-6 flex flex-col gap-3 sm:flex-row">
            <label className="flex flex-1 cursor-pointer items-center gap-2 rounded-[var(--radius-md)] border border-[var(--border-soft)] p-3 hover:bg-[var(--cream)]">
              <input type="radio" name="confirm" onClick={confirmYes} />
              <span className="text-sm text-[var(--ink)]">{t("grievanceWizard.confirmYes")}</span>
            </label>
            <label className="flex flex-1 cursor-pointer items-center gap-2 rounded-[var(--radius-md)] border border-[var(--border-soft)] p-3 hover:bg-[var(--cream)]">
              <input type="radio" name="confirm" onClick={confirmNo} />
              <span className="text-sm text-[var(--ink)]">{t("grievanceWizard.confirmNo")}</span>
            </label>
          </div>
        ) : (
          <div className="mt-6">
            <label className="mb-2 block text-sm font-medium text-[var(--text-primary)]">
              {t("grievanceWizard.reviseLabel")}
            </label>
            <textarea
              className="w-full rounded-[var(--radius-md)] border border-[var(--border-soft)] bg-[var(--surface-base)] p-3 text-sm text-[var(--ink)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
              rows={4}
              value={complaintText}
              onChange={(e) => setComplaintText(e.target.value)}
            />
            <div className="mt-3 flex justify-end">
              <Button variant="primary" loading={loading} disabled={loading} onClick={() => submitComplaint(complaintText, true)}>
                {t("grievanceWizard.reviseSubmit")}
              </Button>
            </div>
          </div>
        )}
        {loading && !revising && <p className="mt-3 text-sm text-[var(--text-faint)]">{t("grievanceWizard.detecting")}</p>}
        {error && <p className="mt-2 text-sm text-[var(--state-error)]">{error}</p>}
      </Card>
    );
  }

  // ---- Phase: fields (tabbed mandatory/optional) -----------------------
  if (phase === "fields") {
    return (
      <Card className="mx-auto max-w-2xl p-6 md:p-8">
        <div className="flex flex-wrap gap-2 border-b border-[var(--border-soft)] pb-3">
          {activeList.map((f, i) => (
            <button
              key={f.field}
              type="button"
              onClick={() => setActiveIndex(i)}
              className={`rounded-full px-3 py-1.5 text-xs font-medium ${
                i === activeIndex
                  ? "bg-[var(--accent-primary)] text-[var(--accent-contrast)]"
                  : "bg-[var(--cream)] text-[var(--ink)]"
              }`}
            >
              {f.field.replace(/_/g, " ")}
              {(answers[f.field] ?? "").trim() && " ✓"}
            </button>
          ))}
        </div>

        <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-[var(--text-faint)]">
          {showOptional ? t("grievanceWizard.optionalTab") : t("grievanceWizard.mandatoryTab")} ·{" "}
          {t("grievanceWizard.fieldOf", { n: activeIndex + 1, total: activeList.length })}
        </p>

        {activeField && (
          <div className="mt-4">
            <label className="mb-2 block text-sm font-medium text-[var(--text-primary)]">
              {activeField.question}
            </label>
            <input
              type={activeField.input_type === "int" ? "number" : activeField.input_type === "date" ? "date" : "text"}
              className="w-full rounded-[var(--radius-md)] border border-[var(--border-soft)] bg-[var(--surface-base)] p-3 text-sm text-[var(--ink)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
              value={answers[activeField.field] ?? ""}
              onChange={(e) => saveActiveAnswer(e.target.value)}
            />
          </div>
        )}

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          {showOptional ? (
            <Button variant="secondary" onClick={() => { setShowOptional(false); setActiveIndex(mandatoryFields.length - 1); }}>
              {t("grievanceWizard.backToMandatory")}
            </Button>
          ) : (
            <div />
          )}

          <div className="flex gap-3">
            {!showOptional && activeIndex < mandatoryFields.length - 1 && (
              <Button variant="secondary" onClick={() => setActiveIndex((i) => i + 1)}>
                {t("common.next")}
              </Button>
            )}
            {showOptional && activeIndex < optionalFields.length - 1 && (
              <Button variant="secondary" onClick={() => setActiveIndex((i) => i + 1)}>
                {t("common.next")}
              </Button>
            )}

            {!showOptional && activeIndex === mandatoryFields.length - 1 && (
              <>
                {optionalFields.length > 0 && (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setShowOptional(true);
                      setActiveIndex(0);
                    }}
                  >
                    {t("grievanceWizard.addOptional")}
                  </Button>
                )}
                <Button variant="primary" disabled={!mandatoryComplete || loading} loading={loading} onClick={finalize}>
                  {t("grievanceWizard.submit")}
                </Button>
              </>
            )}

            {showOptional && activeIndex === optionalFields.length - 1 && (
              <Button variant="primary" disabled={!mandatoryComplete || loading} loading={loading} onClick={finalize}>
                {t("grievanceWizard.submit")}
              </Button>
            )}
          </div>
        </div>
        {error && <p className="mt-2 text-sm text-[var(--state-error)]">{error}</p>}
      </Card>
    );
  }

  // ---- Phase: final draft table -----------------------------------------
  if (phase === "final" && finalGrievance) {
    const rows: [string, string | null | undefined][] = [
      [t("grievanceCard.reference"), finalGrievance.reference],
      [t("grievanceCard.category"), finalGrievance.category],
      [t("grievanceCard.subCategory"), finalGrievance.sub_category],
      [t("grievanceCard.department"), finalGrievance.department],
      [t("grievanceCard.jurisdiction"), finalGrievance.jurisdiction],
      [t("grievanceCard.titleLabel"), finalGrievance.title],
      [t("grievanceCard.description"), finalGrievance.description?.display],
      [t("grievanceCard.wardNumber"), finalGrievance.location?.ward_number],
      [t("grievanceCard.locality"), finalGrievance.location?.locality],
      [t("grievanceCard.area"), finalGrievance.location?.area],
      [t("grievanceCard.city"), finalGrievance.location?.city],
      [t("grievanceCard.state"), finalGrievance.location?.state],
    ];
    for (const [field, value] of Object.entries(answers)) {
      if (!value) continue;
      rows.push([field.replace(/_/g, " "), value]);
    }

    return (
      <Card className="mx-auto max-w-2xl p-6 md:p-8">
        <h2 className="font-semibold text-[var(--text-primary)]">{t("grievanceWizard.draftTitle")}</h2>

        <div className="mt-4 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border-soft)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[var(--cream)]">
                <th className="px-3 py-2 text-left font-semibold text-[var(--text-faint)]">{t("grievanceWizard.tableField")}</th>
                <th className="px-3 py-2 text-left font-semibold text-[var(--text-faint)]">{t("grievanceWizard.tableValue")}</th>
              </tr>
            </thead>
            <tbody>
              {rows
                .filter(([, v]) => !!v)
                .map(([field, value]) => (
                  <tr key={field} className="border-t border-[var(--border-soft)]">
                    <td className="px-3 py-2 align-top capitalize text-[var(--text-faint)]">{field}</td>
                    <td className="px-3 py-2 text-[var(--ink)]">{value}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>

        {finalGrievance.submission?.portal_url && (
          <p className="mt-4 text-sm text-[var(--ink)]">
            {t("grievanceWizard.fileHere")}:{" "}
            {/^https?:\/\//.test(finalGrievance.submission.portal_url) ? (
              <a
                href={finalGrievance.submission.portal_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-[var(--accent-primary)] underline decoration-dotted"
              >
                {finalGrievance.submission.portal_url}
              </a>
            ) : (
              <span className="font-medium">{finalGrievance.submission.portal_url}</span>
            )}
          </p>
        )}

        <div className="mt-4 flex items-start gap-2 rounded-[var(--radius-md)] bg-[var(--state-warning)]/10 px-3 py-2 text-xs text-[var(--state-warning)]">
          <IconAlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <p>{t("grievanceWizard.draftWarning")}</p>
        </div>

        {mixedLanguage && (
          <div className="mt-3 flex items-start gap-2 rounded-[var(--radius-md)] bg-[var(--state-error)]/10 px-3 py-2 text-xs text-[var(--state-error)]">
            <IconAlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <p>{t("grievanceWizard.mixedLanguageWarning")}</p>
          </div>
        )}

        <div className="mt-6 flex justify-end">
          <Button variant="secondary" onClick={startOver}>
            {t("grievanceWizard.newComplaint")}
          </Button>
        </div>
      </Card>
    );
  }

  return null;
}
