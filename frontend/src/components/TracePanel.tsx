import { ChevronDown, ChevronRight, RefreshCw, ShieldAlert } from 'lucide-react';
import { useState } from 'react';
import type { TraceStep } from '../types';

interface TracePanelProps {
  trace: TraceStep[];
}

const STEP_LABELS: Record<TraceStep['step_type'], string> = {
  tool_call: 'SQL / Araç Çağrısı',
  tool_result: 'Veritabanı Çıktısı',
  sql_retry: 'SQL Self-Healing',
  guardrail: 'Guardrail',
  citation_gate: 'Kaynak Doğrulama',
};

function getDotColor(step: TraceStep): string {
  if (step.step_type === 'sql_retry' || step.is_error) return 'bg-amber-400';
  if (step.step_type === 'guardrail') return 'bg-rose-400';
  if (step.step_type === 'citation_gate' || step.step_type === 'tool_result') return 'bg-teal-400';
  return 'bg-zinc-400';
}

export function TracePanel({ trace }: TracePanelProps) {
  const retryCount = trace.filter((s) => s.step_type === 'sql_retry').length;
  const hasGuardrail = trace.some((s) => s.step_type === 'guardrail');
  const [expanded, setExpanded] = useState(retryCount > 0 || hasGuardrail);

  if (trace.length === 0) return null;

  const totalStepMs = trace.reduce((sum, step) => sum + step.duration_ms, 0);

  return (
    <div className="mt-3 border-t border-[#20242d] pt-2.5">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center justify-between text-[11px] text-[#8b93a1] hover:text-[#eceff4] transition-colors cursor-pointer"
      >
        <span className="inline-flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-[#6b7280]" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-[#6b7280]" aria-hidden="true" />
          )}
          <span className="font-mono uppercase tracking-wider text-[10px] font-medium">
            Çalışma İzi ({trace.length} adım · {totalStepMs.toFixed(1)} ms)
          </span>
          {retryCount > 0 && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30 font-mono text-[10px]">
              <RefreshCw className="w-2.5 h-2.5" aria-hidden="true" />
              {retryCount} SQL Self-Healing
            </span>
          )}
          {hasGuardrail && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-rose-500/10 text-rose-300 border border-rose-500/30 font-mono text-[10px]">
              <ShieldAlert className="w-2.5 h-2.5" aria-hidden="true" />
              Engellendi
            </span>
          )}
        </span>
        <span className="font-mono text-[10px] text-[#6b7280]">
          {expanded ? 'Daralt' : 'İncele'}
        </span>
      </button>

      {expanded && (
        <ol className="mt-2.5 ml-1.5 border-l border-[#232732] pl-3.5 space-y-2.5">
          {trace.map((step) => (
            <li key={`${step.step_index}-${step.step_type}`} className="relative text-xs">
              <span
                className={`absolute -left-[18px] top-1.5 w-1.5 h-1.5 rounded-full ${getDotColor(step)}`}
                aria-hidden="true"
              />
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="inline-flex items-center gap-2">
                  <span className="font-medium text-[#d1d5db] text-[11px]">
                    {step.step_index}. {STEP_LABELS[step.step_type]}
                  </span>
                  {step.tool_name && (
                    <code className="text-[10px] font-mono text-teal-300/90 bg-[#171a21] px-1.5 py-0.5 rounded border border-[#232732]">
                      {step.tool_name}
                    </code>
                  )}
                </div>
                <span className="font-mono text-[10px] text-[#6b7280] tabular-nums">
                  {step.duration_ms.toFixed(1)} ms
                </span>
              </div>
              <p className="mt-1 font-mono text-[11px] text-[#9ca3af] break-all leading-snug">
                {step.input_summary}
              </p>
              <p
                className={`mt-0.5 text-[11px] leading-snug ${
                  step.is_error || step.step_type === 'guardrail'
                    ? 'text-amber-300/90'
                    : 'text-[#8b93a1]'
                }`}
              >
                {step.output_summary}
              </p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
