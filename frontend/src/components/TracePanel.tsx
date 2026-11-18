import { AlertTriangle, CheckCircle2, Cpu, RefreshCw, ShieldAlert, Terminal } from 'lucide-react';
import { useState } from 'react';
import type { TraceStep } from '../types';

interface TracePanelProps {
  trace: TraceStep[];
}

const STEP_LABELS: Record<TraceStep['step_type'], string> = {
  tool_call: 'Araç Çağrısı',
  tool_result: 'Araç Çıktısı',
  sql_retry: 'SQL Self-Healing',
  guardrail: 'Guardrail',
  citation_gate: 'Kaynak Doğrulama',
};

function getStepStyle(step: TraceStep): string {
  if (step.step_type === 'sql_retry' || step.is_error) {
    return 'border-amber-500/40 bg-amber-950/30 text-amber-200';
  }
  if (step.step_type === 'guardrail') {
    return 'border-rose-500/40 bg-rose-950/30 text-rose-200';
  }
  if (step.step_type === 'citation_gate') {
    return 'border-indigo-500/40 bg-indigo-950/25 text-indigo-200';
  }
  if (step.step_type === 'tool_result') {
    return 'border-emerald-500/35 bg-emerald-950/25 text-emerald-200';
  }
  return 'border-slate-700 bg-slate-900/70 text-slate-200';
}

function StepIcon({ step }: { step: TraceStep }) {
  if (step.step_type === 'sql_retry') return <RefreshCw className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />;
  if (step.step_type === 'guardrail') return <ShieldAlert className="w-3.5 h-3.5 text-rose-400" aria-hidden="true" />;
  if (step.is_error) return <AlertTriangle className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />;
  if (step.step_type === 'tool_result' || step.step_type === 'citation_gate') {
    return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />;
  }
  return <Terminal className="w-3.5 h-3.5 text-sky-400" aria-hidden="true" />;
}

export function TracePanel({ trace }: TracePanelProps) {
  const [expanded, setExpanded] = useState(true);
  if (trace.length === 0) return null;

  const retryCount = trace.filter((s) => s.step_type === 'sql_retry').length;

  return (
    <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/70 overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full px-3 py-2 flex items-center justify-between text-xs font-medium text-slate-300 hover:bg-slate-900/80 transition-colors cursor-pointer"
      >
        <span className="inline-flex items-center gap-2">
          <Cpu className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
          <span>Agent Trace ({trace.length} adım)</span>
          {retryCount > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 font-semibold">
              {retryCount} SQL Self-Healing
            </span>
          )}
        </span>
        <span className="text-slate-400">{expanded ? 'Gizle' : 'Göster'}</span>
      </button>

      {expanded && (
        <ol className="px-3 pb-3 space-y-2 border-t border-slate-800/80 pt-2">
          {trace.map((step) => (
            <li
              key={`${step.step_index}-${step.step_type}`}
              className={`rounded-md border px-2.5 py-2 text-xs ${getStepStyle(step)}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
                <div className="inline-flex items-center gap-1.5 font-semibold">
                  <StepIcon step={step} />
                  <span>
                    #{step.step_index} · {STEP_LABELS[step.step_type]}
                  </span>
                  {step.tool_name && (
                    <code className="px-1.5 py-0.5 rounded bg-slate-900/90 text-sky-300 font-mono text-[11px]">
                      {step.tool_name}
                    </code>
                  )}
                </div>
                <span className="font-mono text-[11px] opacity-75 tabular-nums">
                  {step.duration_ms.toFixed(1)} ms
                </span>
              </div>
              <p className="font-mono text-[11px] opacity-80 break-all">{step.input_summary}</p>
              <p className="mt-1 text-[11px] opacity-95">{step.output_summary}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
