import { BarChart3, Database } from 'lucide-react';
import type { ChartSpec, ResultArtifact } from '../types';

interface DataVisualizerProps {
  artifact: ResultArtifact;
  onSelectDisclosure?: (disclosureId: string) => void;
}

function formatCellValue(col: string, val: unknown): string {
  if (val === null || val === undefined || val === '') return '—';
  if (typeof val === 'number') {
    if (col.endsWith('_try') && Math.abs(val) >= 1_000_000) {
      return `${new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 1 }).format(val / 1_000_000)} Mn TL`;
    }
    return new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 2 }).format(val);
  }
  return String(val);
}

function inferChartSpec(artifact: ResultArtifact): ChartSpec | null {
  if (artifact.chart_spec) return artifact.chart_spec;
  if (artifact.rows.length < 2 || !artifact.columns.includes('ticker')) return null;

  const numericCol = artifact.columns.find((col) => {
    if (col === 'disclosure_id' || col === 'fiscal_year') return false;
    return artifact.rows.some((r) => typeof r[col] === 'number');
  });
  if (!numericCol) return null;

  return {
    type: 'bar',
    title: `${numericCol} Karşılaştırması`,
    x_key: 'ticker',
    y_key: numericCol,
  };
}

export function DataVisualizer({ artifact, onSelectDisclosure }: DataVisualizerProps) {
  const chart = inferChartSpec(artifact);
  const chartPoints = chart
    ? artifact.rows
        .slice(0, 8)
        .map((r) => ({
          label: String(r[chart.x_key] ?? ''),
          value: typeof r[chart.y_key] === 'number' ? (r[chart.y_key] as number) : 0,
        }))
        .filter((p) => p.label)
    : [];

  const maxAbs = chartPoints.reduce((acc, p) => Math.max(acc, Math.abs(p.value)), 0) || 1;

  return (
    <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/75 p-3 space-y-3">
      {artifact.sql_query && (
        <div className="rounded bg-slate-950 border border-slate-800 px-2.5 py-2 text-[11px]">
          <div className="flex items-center gap-1.5 text-emerald-400 font-semibold mb-1">
            <Database className="w-3.5 h-3.5" aria-hidden="true" />
            <span>Çalıştırılan Salt-Okunur SQL</span>
          </div>
          <code className="font-mono text-slate-300 break-all">{artifact.sql_query}</code>
        </div>
      )}

      {chart && chartPoints.length >= 2 && (
        <div className="rounded bg-slate-950/80 border border-slate-800 p-3">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-200 mb-2">
            <BarChart3 className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
            <span>{chart.title}</span>
          </div>
          <svg
            role="img"
            aria-label={chart.title}
            viewBox={`0 0 420 ${chartPoints.length * 26 + 8}`}
            className="w-full max-w-lg h-auto"
          >
            {chartPoints.map((pt, idx) => {
              const barWidth = Math.max(4, Math.round((Math.abs(pt.value) / maxAbs) * 230));
              const y = idx * 26 + 4;
              const isNegative = pt.value < 0;
              return (
                <g key={`${pt.label}-${idx}`} transform={`translate(0, ${y})`}>
                  <text
                    x="0"
                    y="15"
                    fill="#cbd5e1"
                    fontSize="11"
                    fontFamily="ui-monospace, monospace"
                    fontWeight="600"
                  >
                    {pt.label}
                  </text>
                  <rect
                    x="68"
                    y="4"
                    width={barWidth}
                    height="15"
                    rx="3"
                    fill={isNegative ? '#fb7185' : '#10b981'}
                  />
                  <text
                    x={68 + barWidth + 6}
                    y="15"
                    fill="#94a3b8"
                    fontSize="10"
                    fontFamily="ui-sans-serif, sans-serif"
                  >
                    {formatCellValue(chart.y_key, pt.value)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      )}

      {artifact.rows.length > 0 && (
        <div className="overflow-x-auto rounded border border-slate-800">
          <table className="w-full text-left text-xs border-collapse">
            <thead className="bg-slate-950 text-slate-300">
              <tr>
                {artifact.columns.map((col) => (
                  <th key={col} className="px-2.5 py-2 font-semibold border-b border-slate-800 whitespace-nowrap">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {artifact.rows.slice(0, 15).map((row, rIdx) => (
                <tr key={rIdx} className="hover:bg-slate-800/40">
                  {artifact.columns.map((col) => {
                    const val = row[col];
                    const isDiscId = col === 'disclosure_id' && typeof val === 'string';
                    return (
                      <td key={col} className="px-2.5 py-1.5 text-slate-200 tabular-nums whitespace-nowrap">
                        {isDiscId && onSelectDisclosure ? (
                          <button
                            type="button"
                            onClick={() => onSelectDisclosure(val)}
                            className="font-mono text-emerald-400 hover:underline cursor-pointer"
                          >
                            {val}
                          </button>
                        ) : (
                          formatCellValue(col, val)
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
