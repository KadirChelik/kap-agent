import { useEffect, useState, type FormEvent } from 'react';
import { Flag, X } from 'lucide-react';

interface FlagModalProps {
  disclosureId: string;
  fieldName: string;
  currentValue: unknown;
  onClose: () => void;
  onSubmitted: () => void;
}

export function FlagModal({
  disclosureId,
  fieldName,
  currentValue,
  onClose,
  onSubmitted,
}: FlagModalProps) {
  const [suggestedValue, setSuggestedValue] = useState(
    currentValue === null || currentValue === undefined ? '' : String(currentValue)
  );
  const [reasonNote, setReasonNote] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [successMsg, setSuccessMsg] = useState('');

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setErrorMsg('');
    try {
      const resp = await fetch('/api/reviews', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          disclosure_id: disclosureId,
          field_name: fieldName,
          suggested_value: suggestedValue.trim(),
          reason_note: reasonNote.trim() || null,
        }),
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail || `İstek başarısız (${resp.status})`);
      }
      setSuccessMsg('Öneriniz review_flags kuyruğuna kaydedildi. Admin onayı sonrası ana veriye yansır.');
      onSubmitted();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gönderim hatası');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="flag-modal-title"
      className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-xs flex items-center justify-center p-4"
    >
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="inline-flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Flag className="w-4 h-4 text-amber-400" aria-hidden="true" />
            <h3 id="flag-modal-title">Düşük Güven / Hata Düzeltme Öner</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Kapat"
            className="p-1 rounded hover:bg-slate-800 text-slate-400 cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3 text-xs">
          <div className="rounded bg-slate-950 border border-slate-800 p-2.5 text-slate-300 space-y-1">
            <div>
              Bildirim ID: <strong className="font-mono text-emerald-400">{disclosureId}</strong>
            </div>
            <div>
              Alan: <strong className="font-mono text-sky-300">{fieldName}</strong>
            </div>
            <div>
              Mevcut Değer: <code className="text-slate-400">{String(currentValue ?? 'null')}</code>
            </div>
          </div>

          <label className="block space-y-1">
            <span className="text-slate-300 font-medium">Önerilen Doğru Değer</span>
            <input
              type="text"
              required
              maxLength={120}
              value={suggestedValue}
              onChange={(e) => setSuggestedValue(e.target.value)}
              placeholder="Örn: 2026-06-01 veya 13.68"
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-emerald-500"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-slate-300 font-medium">Açıklama / Dayanak (Opsiyonel)</span>
            <textarea
              rows={2}
              maxLength={400}
              value={reasonNote}
              onChange={(e) => setReasonNote(e.target.value)}
              placeholder="KAP metnindeki dipnota göre..."
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-emerald-500"
            />
          </label>

          {errorMsg && (
            <p className="rounded bg-rose-950/60 border border-rose-500/40 px-2.5 py-2 text-rose-200">
              {errorMsg}
            </p>
          )}
          {successMsg && (
            <p className="rounded bg-emerald-950/60 border border-emerald-500/40 px-2.5 py-2 text-emerald-200">
              {successMsg}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800 cursor-pointer"
            >
              Kapat
            </button>
            <button
              type="submit"
              disabled={submitting || Boolean(successMsg)}
              className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-slate-950 font-semibold cursor-pointer"
            >
              {submitting ? 'Gönderiliyor...' : 'İncelemeye Gönder'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
