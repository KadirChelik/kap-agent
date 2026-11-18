import { useState, type FormEvent } from 'react';
import { Check, Lock, X } from 'lucide-react';
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
      const resp = await fetch('/api/admin/reviews', {
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
      const resp = await fetch(`/api/admin/reviews/${flag.flag_id}/decide`, {
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
      className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-xs flex items-center justify-center p-4"
    >
      <div className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="inline-flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Lock className="w-4 h-4 text-emerald-400" aria-hidden="true" />
            <h3 id="admin-review-title">Admin Düzeltme Onay Paneli (`review_flags`)</h3>
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

        <form onSubmit={loadReviews} className="flex gap-2">
          <input
            type="password"
            required
            value={adminToken}
            onChange={(e) => setAdminToken(e.target.value)}
            placeholder="ADMIN_TOKEN girin (yerel: dev-admin-token)"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-emerald-500"
          />
          <button
            type="submit"
            disabled={loading}
            className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-slate-950 text-xs font-semibold cursor-pointer"
          >
            {loading ? 'Yükleniyor...' : 'Kuyruğu Aç'}
          </button>
        </form>

        {errorMsg && (
          <p className="rounded bg-rose-950/60 border border-rose-500/40 px-3 py-2 text-xs text-rose-200">
            {errorMsg}
          </p>
        )}

        <div className="flex-1 overflow-y-auto space-y-2.5 pr-1">
          {loaded && items.length === 0 && (
            <p className="text-xs text-slate-400 py-6 text-center">
              Henüz düzeltme önerisi kaydı yok.
            </p>
          )}
          {items.map((flag) => (
            <div
              key={flag.flag_id}
              className="rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-xs space-y-2"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono font-semibold text-emerald-400">
                  #{flag.flag_id} · {flag.ticker} · kap:{flag.disclosure_id} ({flag.field_name})
                </span>
                <span
                  className={`px-2 py-0.5 rounded font-semibold ${
                    flag.status === 'pending'
                      ? 'bg-amber-500/20 text-amber-300'
                      : flag.status === 'approved'
                        ? 'bg-emerald-500/20 text-emerald-300'
                        : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  {flag.status}
                </span>
              </div>
              <div className="grid sm:grid-cols-2 gap-2">
                <div className="rounded bg-slate-900 p-2">
                  <span className="text-slate-400">Mevcut:</span>{' '}
                  <span className="text-slate-200">{flag.current_value ?? 'null'}</span>
                </div>
                <div className="rounded bg-slate-900 p-2 border border-emerald-500/30">
                  <span className="text-slate-400">Önerilen:</span>{' '}
                  <span className="text-emerald-300 font-semibold">{flag.suggested_value}</span>
                </div>
              </div>
              {flag.reason_note && <p className="text-slate-400">Not: {flag.reason_note}</p>}
              {flag.status === 'pending' && (
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => handleDecision(flag, 'reject')}
                    className="px-2.5 py-1 rounded border border-slate-700 text-slate-300 hover:bg-slate-800 cursor-pointer"
                  >
                    Reddet
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDecision(flag, 'approve')}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-semibold cursor-pointer"
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
