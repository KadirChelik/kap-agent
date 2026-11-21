import { useState, type FormEvent } from 'react';
import { Check, Lock, X } from 'lucide-react';
import { apiUrl } from '../lib/api';
import type { ReviewFlagItem } from '../types';

interface AdminReviewDrawerProps {
  onClose: () => void;
  onDataMutated: (disclosureId: string) => void;
}

export function AdminReviewDrawer({ onClose, onDataMutated }: AdminReviewDrawerProps) {
  const [adminToken, setAdminToken] = useState('');
  const [items, setItems] = useState<ReviewFlagItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  async function loadReviews(e?: FormEvent) {
    if (e) e.preventDefault();
    setLoading(true);
    setErrorMsg('');
    try {
      const resp = await fetch(apiUrl('/api/admin/reviews'), {
        headers: { 'X-Admin-Token': adminToken.trim() },
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail || 'Admin doğrulaması başarısız.');
      }
      const data = (await resp.json()) as { items: ReviewFlagItem[] };
      setItems(data.items);
      setLoaded(true);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Yükleme hatası');
    } finally {
      setLoading(false);
    }
  }

  async function handleDecision(flag: ReviewFlagItem, action: 'approve' | 'reject') {
    setErrorMsg('');
    try {
      const resp = await fetch(apiUrl(`/api/admin/reviews/${flag.flag_id}/decide`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Admin-Token': adminToken.trim(),
        },
        body: JSON.stringify({ action }),
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail || 'İşlem başarısız.');
      }
      await loadReviews();
      if (action === 'approve') {
        onDataMutated(flag.disclosure_id);
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Karar hatası');
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="admin-review-title"
      className="fixed inset-0 z-50 bg-black/75 backdrop-blur-xs flex items-center justify-center p-4"
    >
      <div className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-lg border border-[#262a35] bg-[#12141a] p-5 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between border-b border-[#20242d] pb-3">
          <div className="inline-flex items-center gap-2 text-xs font-semibold text-[#eceff4]">
            <Lock className="w-3.5 h-3.5 text-teal-400" aria-hidden="true" />
            <h3 id="admin-review-title">Admin Düzeltme Onay Kuyruğu</h3>
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

        <form onSubmit={loadReviews} className="flex gap-2">
          <input
            type="password"
            required
            value={adminToken}
            onChange={(e) => setAdminToken(e.target.value)}
            placeholder="ADMIN_TOKEN girin"
            className="flex-1 rounded-md border border-[#282d39] bg-[#0b0d11] px-3 py-2 text-xs text-[#eceff4] focus:outline-none focus:border-teal-400"
          />
          <button
            type="submit"
            disabled={loading}
            className="px-4 py-2 rounded-md bg-teal-500 hover:bg-teal-400 text-[#081014] text-xs font-semibold cursor-pointer"
          >
            {loading ? 'Yükleniyor...' : 'Kuyruğu Aç'}
          </button>
        </form>

        {errorMsg && (
          <p className="rounded-md bg-rose-950/40 border border-rose-500/35 px-3 py-2 text-xs text-rose-200">
            {errorMsg}
          </p>
        )}

        <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
          {loaded && items.length === 0 && (
            <p className="text-xs text-[#6b7280] py-6 text-center">
              Bekleyen düzeltme önerisi kaydı yok.
            </p>
          )}
          {items.map((flag) => (
            <div
              key={flag.flag_id}
              className="rounded-md border border-[#23262f] bg-[#0e1015] p-3 text-xs space-y-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono font-semibold text-teal-300 text-[11px]">
                  #{flag.flag_id} · {flag.ticker} · kap:{flag.disclosure_id} ({flag.field_name})
                </span>
                <span
                  className={`px-2 py-0.5 rounded font-mono text-[10px] uppercase ${
                    flag.status === 'pending'
                      ? 'bg-amber-500/15 text-amber-300'
                      : flag.status === 'approved'
                        ? 'bg-teal-500/15 text-teal-300'
                        : 'bg-[#1a1d26] text-[#8b93a1]'
                  }`}
                >
                  {flag.status}
                </span>
              </div>
              <div className="grid sm:grid-cols-2 gap-2 font-mono text-[11px]">
                <div className="rounded bg-[#0b0d11] border border-[#1e222b] p-2">
                  <span className="text-[#6b7280]">Mevcut:</span>{' '}
                  <span className="text-[#d1d5db]">{flag.current_value ?? 'null'}</span>
                </div>
                <div className="rounded bg-[#0b0d11] border border-teal-500/30 p-2">
                  <span className="text-[#6b7280]">Önerilen:</span>{' '}
                  <span className="text-teal-300 font-semibold">{flag.suggested_value}</span>
                </div>
              </div>
              {flag.reason_note && <p className="text-[#8b93a1] text-[11px]">Not: {flag.reason_note}</p>}
              {flag.status === 'pending' && (
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => handleDecision(flag, 'reject')}
                    className="px-2.5 py-1 rounded border border-[#282d39] text-[#9ca3af] hover:bg-[#1a1d26] cursor-pointer"
                  >
                    Reddet
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDecision(flag, 'approve')}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-teal-500 hover:bg-teal-400 text-[#081014] font-semibold cursor-pointer"
                  >
                    <Check className="w-3.5 h-3.5" aria-hidden="true" />
                    <span>Onayla & Güncelle</span>
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
