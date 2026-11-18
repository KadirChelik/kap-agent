import { AlertTriangle, CheckCircle2, ExternalLink, FileText, Flag } from 'lucide-react';
import type { DisclosureDetail } from '../types';

interface SourceDrawerProps {
  disclosure: DisclosureDetail | null;
  loading: boolean;
  onFlagField: (fieldName: string, currentValue: unknown) => void;
}

const READONLY_FIELDS = new Set(['evidence_quote', 'consolidation', 'currency', 'presentation_scale']);

export function SourceDrawer({ disclosure, loading, onFlagField }: SourceDrawerProps) {
  if (loading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 text-sm text-slate-400">
        Kaynak bildirim yükleniyor...
      </div>
    );
  }

  if (!disclosure) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-2">
        <div className="flex items-center gap-2 text-slate-200 font-semibold text-sm">
          <FileText className="w-4 h-4 text-emerald-400" aria-hidden="true" />
          <span>Kaynak Bildirim İnceleyici</span>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">
          Sohbetteki herhangi bir <code className="text-emerald-400">[kap:ID]</code> rozetine veya düşük
          güvenli kayıt listesine tıklayarak orijinal KAP metnini ve yapılandırılmış çıkarım alanlarını
          burada doğrulayabilirsiniz.
        </p>
      </div>
    );
  }

  const lowConfidence = disclosure.extraction_confidence < 0.8 || disclosure.low_confidence_fields.length > 0;
  const lowSet = new Set(disclosure.low_confidence_fields);

  return (
    <aside
      aria-label="Kaynak Bildirim Detayı"
      className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-4"
    >
      <div className="space-y-1.5 border-b border-slate-800 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 font-mono text-xs font-semibold">
            {disclosure.ticker} · kap:{disclosure.disclosure_id}
          </span>
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
              disclosure.human_verified
                ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                : lowConfidence
                  ? 'bg-amber-500/20 text-amber-300 border border-amber-500/35'
                  : 'bg-slate-800 text-slate-300'
            }`}
          >
            {disclosure.human_verified ? (
              <>
                <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
                <span>Admin Onaylı</span>
              </>
            ) : (
              <span>Güven: %{Math.round(disclosure.extraction_confidence * 100)}</span>
            )}
          </span>
        </div>

        <h3 className="text-sm font-semibold text-slate-100">{disclosure.title}</h3>
        <p className="text-xs text-slate-400">
          {disclosure.company_name} · {new Date(disclosure.published_at).toLocaleString('tr-TR')}
        </p>
        <a
          href={disclosure.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-sky-400 hover:underline"
        >
          <span>Orijinal KAP Sayfası</span>
          <ExternalLink className="w-3 h-3" aria-hidden="true" />
        </a>
      </div>

      {lowConfidence && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-950/35 p-2.5 text-xs text-amber-200 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <p className="font-semibold">Düşük Güvenli Çıkarım İşareti</p>
            <p className="opacity-90 mt-0.5">
              Belirsiz alanlar: {disclosure.low_confidence_fields.join(', ') || 'Genel güven eşiği < 0.80'}.
              Hatalı gördüğünüz alanı işaretleyebilirsiniz (admin onayına kadar ana veriyi değiştirmez).
            </p>
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Yapılandırılmış Çıkarım Alanları
          </h4>
          <button
            type="button"
            onClick={() => onFlagField('summary_tr', disclosure.summary_tr)}
            className="text-[11px] text-amber-300 hover:underline cursor-pointer"
          >
            Özeti Düzelt
          </button>
        </div>
        <div className="divide-y divide-slate-800/80 rounded-lg border border-slate-800 bg-slate-950/60">
          {Object.entries(disclosure.extracted_data).map(([field, value]) => {
            const isFlagged = lowSet.has(field);
            const canFlag = !READONLY_FIELDS.has(field);
            return (
              <div
                key={field}
                className={`px-2.5 py-2 flex items-center justify-between gap-2 text-xs ${
                  isFlagged ? 'bg-amber-950/25' : ''
                }`}
              >
                <div className="min-w-0">
                  <span className="font-mono text-[11px] text-slate-400">{field}</span>
                  {isFlagged && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 text-[10px] font-semibold">
                      Düşük Güven
                    </span>
                  )}
                  <p className="text-slate-100 break-words mt-0.5">
                    {value === null || value === undefined ? 'null' : String(value)}
                  </p>
                </div>
                {canFlag && (
                  <button
                    type="button"
                    onClick={() => onFlagField(field, value)}
                    aria-label={`${field} için düzeltme öner`}
                    className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded border border-slate-700 bg-slate-900 hover:bg-slate-800 text-[11px] text-slate-300 cursor-pointer"
                  >
                    <Flag className="w-3 h-3 text-amber-400" aria-hidden="true" />
                    <span>Düzeltme Öner</span>
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="space-y-1.5">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Orijinal KAP Metni
        </h4>
        <pre className="whitespace-pre-wrap text-xs text-slate-300 bg-slate-950 border border-slate-800 rounded-lg p-3 max-h-56 overflow-y-auto leading-relaxed">
          {disclosure.raw_text_excerpt}
        </pre>
      </div>
    </aside>
  );
}
