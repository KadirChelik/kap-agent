import { AlertTriangle, ArrowLeft, CheckCircle2, ExternalLink, Flag } from 'lucide-react';
import type { DisclosureDetail, DisclosureSummary } from '../types';

interface SourceDrawerProps {
  disclosure: DisclosureDetail | null;
  loading: boolean;
  lowConfItems: DisclosureSummary[];
  onSelectDisclosure: (disclosureId: string) => void;
  onClearSelection: () => void;
  onFlagField: (fieldName: string, currentValue: unknown) => void;
}

const READONLY_FIELDS = new Set(['evidence_quote', 'consolidation', 'currency', 'presentation_scale']);

const FIELD_LABELS: Record<string, string> = {
  decision_status: 'Karar Durumu',
  fiscal_year: 'Hesap Yılı',
  gross_cash_per_share_try: 'Brüt Pay Başına (TL)',
  net_cash_per_share_try: 'Net Pay Başına (TL)',
  total_gross_cash_dividend_try: 'Toplam Brüt Temettü',
  bonus_share_rate_pct: 'Bedelsiz Oranı (%)',
  payment_type: 'Ödeme Şekli',
  ex_dividend_date: 'Hak Kullanım Tarihi',
  payment_date: 'Ödeme Tarihi',
  period: 'Dönem',
  consolidation: 'Konsolidasyon',
  currency: 'Para Birimi',
  presentation_scale: 'Tablo Ölçeği',
  revenue_try: 'Hasılat',
  gross_profit_try: 'Brüt Kâr',
  ebitda_try: 'FAVÖK',
  operating_profit_try: 'Esas Faaliyet Kârı',
  net_profit_try: 'Dönem Net Kârı',
  total_assets_try: 'Toplam Varlıklar',
  total_equity_try: 'Toplam Özkaynaklar',
  net_debt_try: 'Net Borç',
  event_category: 'Olay Kategorisi',
  counterparty: 'Karşı Taraf',
  contract_amount_original: 'İşlem / Sözleşme Tutarı',
  contract_currency: 'Tutar Birimi',
  contract_amount_try_equiv: 'TL Karşılığı',
  revenue_ratio_pct: 'Hasılata Oranı (%)',
  expected_impact: 'Beklenen Etki',
  evidence_quote: 'Dayanak Alıntı',
};

function formatFieldValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') {
    if (field.endsWith('_try') && Math.abs(value) >= 1_000_000) {
      return `${new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 0 }).format(value)} TL`;
    }
    return new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 4 }).format(value);
  }
  return String(value);
}

export function SourceDrawer({
  disclosure,
  loading,
  lowConfItems,
  onSelectDisclosure,
  onClearSelection,
  onFlagField,
}: SourceDrawerProps) {
  if (loading) {
    return (
      <div className="rounded-lg border border-[#23262f] bg-[#12141a] p-5 text-xs text-[#8b93a1] font-mono">
        Kaynak bildirim yükleniyor...
      </div>
    );
  }

  if (!disclosure) {
    return (
      <aside className="rounded-lg border border-[#23262f] bg-[#12141a] overflow-hidden">
        <div className="px-4 py-3 border-b border-[#23262f] flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[#d1d5db]">
            Kaynak Bildirim İnceleyici
          </h2>
          <span className="font-mono text-[10px] text-[#6b7280]">KAP DOĞRULAMA</span>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-xs text-[#8b93a1] leading-relaxed">
            Yanıtlardaki{' '}
            <code className="text-teal-300 bg-[#0b0d11] px-1 py-0.5 rounded border border-[#23262f]">
              kap:ID
            </code>{' '}
            atıflarına tıklayarak orijinal KAP metnini ve çıkarılan alanları burada inceleyebilirsiniz.
          </p>

          {lowConfItems.length > 0 ? (
            <div className="space-y-2 pt-2 border-t border-[#1e222b]">
              <div className="flex items-center justify-between text-[11px]">
                <span className="font-medium text-amber-300/90">
                  Düşük Güvenli Çıkarımlar (İncele / Düzelt)
                </span>
                <span className="font-mono text-[10px] text-[#6b7280]">
                  {lowConfItems.length} kayıt
                </span>
              </div>
              <p className="text-[11px] text-[#6b7280] leading-snug">
                Sözleşme tutarı, tarih veya kaynak alıntısı eksik kalan kayıtlar aşağıda listelenir; alan bazlı düzeltme önerebilirsiniz.
              </p>
              <div className="divide-y divide-[#1e222b] rounded-md border border-[#23262f] bg-[#0e1015]">
                {lowConfItems.map((item) => (
                  <button
                    key={item.disclosure_id}
                    type="button"
                    onClick={() => onSelectDisclosure(item.disclosure_id)}
                    className="w-full px-3 py-2 flex items-center justify-between gap-2 text-left hover:bg-[#171a22] transition-colors cursor-pointer"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-xs">
                        <span className="font-mono font-semibold text-[#eceff4]">
                          {item.ticker}
                        </span>
                        <span className="font-mono text-[11px] text-[#8b93a1]">
                          kap:{item.disclosure_id}
                        </span>
                      </div>
                      <p className="text-[11px] text-[#6b7280] truncate mt-0.5">{item.summary_tr}</p>
                    </div>
                    <span className="shrink-0 font-mono text-[11px] text-amber-300/90 tabular-nums">
                      %{Math.round(item.extraction_confidence * 100)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="pt-2 border-t border-[#1e222b] text-[11px] text-[#6b7280]">
              Düşük güvenli olarak işaretlenmiş eksik çıkarım bulunmuyor.
            </div>
          )}
        </div>
      </aside>
    );
  }

  const lowConfidence =
    disclosure.extraction_confidence < 0.8 || disclosure.low_confidence_fields.length > 0;
  const lowSet = new Set(disclosure.low_confidence_fields);

  return (
    <aside
      aria-label="Kaynak Bildirim Detayı"
      className="rounded-lg border border-[#23262f] bg-[#12141a] overflow-hidden"
    >
      <div className="px-4 py-3 border-b border-[#23262f] flex items-center justify-between gap-2 bg-[#0e1015]">
        <div className="flex items-center gap-2">
          {lowConfItems.length > 0 && (
            <button
              type="button"
              onClick={onClearSelection}
              aria-label="Düşük güvenli listeye dön"
              className="p-1 rounded hover:bg-[#1d212b] text-[#8b93a1] hover:text-[#eceff4] transition-colors cursor-pointer"
            >
              <ArrowLeft className="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          )}
          <span className="font-mono text-xs font-semibold text-teal-300">
            {disclosure.ticker} · kap:{disclosure.disclosure_id}
          </span>
        </div>

        <span
          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded font-mono text-[11px] border ${
            disclosure.human_verified
              ? 'bg-teal-500/10 text-teal-300 border-teal-500/30'
              : lowConfidence
                ? 'bg-amber-500/10 text-amber-300 border-amber-500/30'
                : 'bg-[#171a21] text-[#9ca3af] border-[#262a35]'
          }`}
        >
          {disclosure.human_verified ? (
            <>
              <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
              <span>Onaylı</span>
            </>
          ) : (
            <span>Güven %{Math.round(disclosure.extraction_confidence * 100)}</span>
          )}
        </span>
      </div>

      <div className="p-4 space-y-4">
        <div className="space-y-1">
          <h3 className="text-sm font-semibold text-[#eceff4] leading-snug">{disclosure.title}</h3>
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-[#8b93a1]">
            <span>
              {disclosure.company_name} ·{' '}
              <span className="tabular-nums">
                {new Date(disclosure.published_at).toLocaleString('tr-TR')}
              </span>
            </span>
            <a
              href={disclosure.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 font-mono text-[11px] text-teal-300 hover:text-teal-200 hover:underline"
            >
              <span>KAP Kaynağı</span>
              <ExternalLink className="w-3 h-3" aria-hidden="true" />
            </a>
          </div>
        </div>

        {lowConfidence && (
          <div className="rounded-md border border-amber-500/30 bg-amber-950/20 px-3 py-2.5 text-xs text-amber-200/90 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" aria-hidden="true" />
            <div>
              <p className="font-semibold text-[11px]">Düşük Güvenli Çıkarım İşareti</p>
              <p className="text-[11px] text-amber-200/75 mt-0.5 leading-snug">
                Belirsiz alanlar: {disclosure.low_confidence_fields.join(', ') || 'Güven skoru < %80'}.
                Alan bazlı düzeltme önerisi admin onayı bekler.
              </p>
            </div>
          </div>
        )}

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-[10px] font-mono font-medium uppercase tracking-wider text-[#8b93a1]">
              Çıkarılan Alanlar
            </h4>
            <button
              type="button"
              onClick={() => onFlagField('summary_tr', disclosure.summary_tr)}
              className="text-[11px] text-[#8b93a1] hover:text-amber-300 transition-colors cursor-pointer"
            >
              Özeti Düzelt
            </button>
          </div>
          <div className="divide-y divide-[#1e222b] rounded-md border border-[#23262f] bg-[#0e1015]">
            {Object.entries(disclosure.extracted_data).map(([field, value]) => {
              const isFlagged = lowSet.has(field);
              const canFlag = !READONLY_FIELDS.has(field);
              return (
                <div
                  key={field}
                  className={`px-3 py-2 flex items-center justify-between gap-2 text-xs ${
                    isFlagged ? 'bg-amber-950/15' : ''
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[11px] text-[#9ca3af] font-medium">
                        {FIELD_LABELS[field] ?? field}
                      </span>
                      <span className="font-mono text-[10px] text-[#5c6472]">{field}</span>
                      {isFlagged && (
                        <span className="px-1 py-0.5 rounded bg-amber-500/15 text-amber-300 font-mono text-[9px] uppercase">
                          Düşük
                        </span>
                      )}
                    </div>
                    <p
                      className={`mt-0.5 break-words tabular-nums ${
                        value === null || value === undefined
                          ? 'text-[#5c6472] font-mono text-[11px]'
                          : 'text-[#eceff4] text-xs'
                      }`}
                    >
                      {formatFieldValue(field, value)}
                    </p>
                  </div>
                  {canFlag && (
                    <button
                      type="button"
                      onClick={() => onFlagField(field, value)}
                      aria-label={`${field} için düzeltme öner`}
                      className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded border border-[#262a35] bg-[#14171f] hover:border-[#3b4252] text-[11px] text-[#9ca3af] hover:text-[#eceff4] transition-colors cursor-pointer"
                    >
                      <Flag className="w-3 h-3 text-amber-400/90" aria-hidden="true" />
                      <span>Öner</span>
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="space-y-1.5">
          <h4 className="text-[10px] font-mono font-medium uppercase tracking-wider text-[#8b93a1]">
            Orijinal KAP Metni
          </h4>
          <pre className="whitespace-pre-wrap text-[11px] font-mono text-[#9ca3af] bg-[#0b0d11] border border-[#23262f] rounded-md p-3 max-h-60 overflow-y-auto leading-relaxed">
            {disclosure.raw_text_excerpt}
          </pre>
        </div>
      </div>
    </aside>
  );
}
