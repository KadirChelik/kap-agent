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
      className="fixed inset-0 z-50 bg-black/75 backdrop-blur-xs flex items-center justify-center p-4"
    >
      <div className="w-full max-w-md rounded-lg border border-[#262a35] bg-[#12141a] p-5 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between border-b border-[#20242d] pb-3">
          <div className="inline-flex items-center gap-2 text-xs font-semibold text-[#eceff4]">
            <Flag className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />
            <h3 id="flag-modal-title">Alan Düzeltme Önerisi</h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Kapat"
            className="p-1 rounded hover:bg-[#1c2029] text-[#8b93a1] cursor-pointer"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3 text-xs">
          <div className="rounded-md bg-[#0b0d11] border border-[#23262f] p-3 text-[#9ca3af] space-y-1 font-mono text-[11px]">
            <div>
              Bildirim: <strong className="text-teal-300">kap:{disclosureId}</strong>
            </div>
            <div>
              Alan: <strong className="text-[#eceff4]">{fieldName}</strong>
            </div>
            <div>
              Mevcut: <span className="text-[#6b7280]">{String(currentValue ?? 'null')}</span>
            </div>
          </div>

          <label className="block space-y-1">
            <span className="text-[#d1d5db] font-medium text-[11px]">Önerilen Doğru Değer</span>
            <input
              type="text"
              required
              maxLength={120}
              value={suggestedValue}
              onChange={(e) => setSuggestedValue(e.target.value)}
              placeholder="Örn: 2026-06-01 veya 13.68"
              className="w-full rounded-md border border-[#282d39] bg-[#0b0d11] px-3 py-2 text-xs text-[#eceff4] focus:outline-none focus:border-teal-400"
            />
          </label>

          <label className="block space-y-1">
            <span className="text-[#d1d5db] font-medium text-[11px]">Açıklama / Dayanak (Opsiyonel)</span>
            <textarea
              rows={2}
              maxLength={400}
              value={reasonNote}
              onChange={(e) => setReasonNote(e.target.value)}
              placeholder="KAP metnindeki dipnota göre..."
              className="w-full rounded-md border border-[#282d39] bg-[#0b0d11] px-3 py-2 text-xs text-[#eceff4] focus:outline-none focus:border-teal-400"
            />
          </label>

          {errorMsg && (
            <p className="rounded-md bg-rose-950/40 border border-rose-500/35 px-3 py-2 text-rose-200 text-[11px]">
              {errorMsg}
            </p>
          )}
          {successMsg && (
            <p className="rounded-md bg-teal-950/40 border border-teal-500/35 px-3 py-2 text-teal-200 text-[11px]">
              {successMsg}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-[#20242d]">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded-md border border-[#282d39] text-[#9ca3af] hover:bg-[#1a1d26] cursor-pointer"
            >
              Kapat
            </button>
            <button
              type="submit"
              disabled={submitting || Boolean(successMsg)}
              className="px-3.5 py-1.5 rounded-md bg-teal-500 hover:bg-teal-400 disabled:opacity-50 text-[#081014] font-semibold cursor-pointer"
            >
              {submitting ? 'Gönderiliyor...' : 'İncelemeye Gönder'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
