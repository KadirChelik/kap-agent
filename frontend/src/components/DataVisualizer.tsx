import { useState } from 'react';
import type { ChartSpec, ResultArtifact } from '../types';

interface DataVisualizerProps {
  artifact: ResultArtifact;
  onSelectDisclosure?: (disclosureId: string) => void;
}

function formatCellValue(col: string, val: unknown): string {
  if (val === null || val === undefined || val === '') return '—';
  if (typeof val === 'number') {
    if (col.endsWith('_try') && Math.abs(val) >= 1_000_000_000) {
      return `${new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 2 }).format(val / 1_000_000_000)} Mr TL`;
    }
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
  const [showSql, setShowSql] = useState(false);
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
    <div className="mt-3 space-y-3">
      {chart && chartPoints.length >= 2 && (
        <div className="rounded-lg border border-[#23262f] bg-[#0e1015] p-3">
          <div className="text-[11px] font-medium uppercase tracking-wider text-[#8b93a1] mb-2">
            {chart.title}
          </div>
          <svg
            role="img"
            aria-label={chart.title}
            viewBox={`0 0 440 ${chartPoints.length * 26 + 8}`}
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
                    fill="#d1d5db"
                    fontSize="11"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
                    fontWeight="600"
                  >
                    {pt.label}
                  </text>
                  <line x1="68" y1="12" x2="300" y2="12" stroke="#1d2028" strokeWidth="1" />
                  <rect
                    x="68"
                    y="4"
                    width={barWidth}
                    height="15"
                    rx="2"
                    fill={isNegative ? '#f43f5e' : '#14b8a6'}
                  />
                  <text
                    x={68 + barWidth + 8}
                    y="15"
                    fill="#9ca3af"
                    fontSize="10.5"
                    fontFamily="IBM Plex Mono, ui-monospace, monospace"
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
        <div className="overflow-x-auto rounded-lg border border-[#23262f] bg-[#0e1015]">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-[#23262f] text-[#8b93a1]">
                {artifact.columns.map((col) => (
                  <th
                    key={col}
                    className="px-3 py-2 font-mono text-[10px] font-medium uppercase tracking-wider whitespace-nowrap"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1c1f27]">
              {artifact.rows.slice(0, 15).map((row, rIdx) => (
                <tr key={rIdx} className="hover:bg-[#161920] transition-colors">
                  {artifact.columns.map((col) => {
                    const val = row[col];
                    const isDiscId = col === 'disclosure_id' && typeof val === 'string';
                    return (
                      <td
                        key={col}
                        className="px-3 py-1.5 text-[#e5e7eb] tabular-nums whitespace-nowrap font-mono text-[11px]"
                      >
                        {isDiscId && onSelectDisclosure ? (
                          <button
                            type="button"
                            onClick={() => onSelectDisclosure(val)}
                            className="text-teal-300 hover:text-teal-200 underline decoration-teal-500/40 underline-offset-2 cursor-pointer"
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

      {artifact.sql_query && (
        <div className="text-[11px]">
          <button
            type="button"
            onClick={() => setShowSql((prev) => !prev)}
            className="font-mono text-[10px] uppercase tracking-wider text-[#6b7280] hover:text-[#9ca3af] cursor-pointer"
          >
            {showSql ? 'SQL Sorgusunu Gizle' : 'Çalıştırılan SQL’i Gör'}
          </button>
          {showSql && (
            <pre className="mt-1.5 rounded border border-[#23262f] bg-[#0b0d11] px-2.5 py-2 font-mono text-[11px] text-[#9ca3af] whitespace-pre-wrap break-all">
              {artifact.sql_query}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
