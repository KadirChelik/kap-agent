import { Fragment, useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  AlertTriangle,
  Bot,
  FileWarning,
  Lock,
  Send,
  ShieldCheck,
  Sparkles,
} from 'lucide-react';
import { AdminReviewDrawer } from './components/AdminReviewDrawer';
import { DataVisualizer } from './components/DataVisualizer';
import { FlagModal } from './components/FlagModal';
import { SourceDrawer } from './components/SourceDrawer';
import { TracePanel } from './components/TracePanel';
import { consumeChatSse } from './lib/sse';
import type {
  ChatMessage,
  DisclosureDetail,
  DisclosureSummary,
  HealthStatus,
} from './types';

const SAMPLE_QUESTIONS: readonly string[] = [
  '2025 yılı için hisse başına en yüksek net nakit temettü dağıtan 3 şirket hangileri?',
  '2025/12 finansal raporlarına göre en yüksek net kâr açıklayan şirketler hangileri?',
  'ASELS ve ENKAI özel durum sözleşme bildirimlerini karşılaştır',
  'THYAO hissesi bu fiyattan alınır mı?',
];

export default function App() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [lowConfItems, setLowConfItems] = useState<DisclosureSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);

  const [selectedDisclosure, setSelectedDisclosure] = useState<DisclosureDetail | null>(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [flagTarget, setFlagTarget] = useState<{
    disclosureId: string;
    fieldName: string;
    currentValue: unknown;
  } | null>(null);
  const [adminOpen, setAdminOpen] = useState(false);

  const loadHealthAndLowConf = useCallback(async () => {
    try {
      const [hResp, lResp] = await Promise.all([
        fetch('/api/health'),
        fetch('/api/disclosures?low_confidence_only=true&limit=10'),
      ]);
      if (hResp.ok) {
        setHealth((await hResp.json()) as HealthStatus);
      }
      if (lResp.ok) {
        const body = (await lResp.json()) as { items: DisclosureSummary[] };
        setLowConfItems(body.items);
      }
    } catch {
      // Backend might still be booting locally
    }
  }, []);

  useEffect(() => {
    void loadHealthAndLowConf();
  }, [loadHealthAndLowConf]);

  const openDisclosure = useCallback(async (disclosureId: string) => {
    setDrawerLoading(true);
    try {
      const resp = await fetch(`/api/disclosures/${encodeURIComponent(disclosureId)}`);
      if (resp.ok) {
        setSelectedDisclosure((await resp.json()) as DisclosureDetail);
      }
    } finally {
      setDrawerLoading(false);
    }
  }, []);

  async function askQuestion(questionText: string) {
    const trimmed = questionText.trim();
    if (!trimmed || streaming) return;

    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      role: 'user',
      text: trimmed,
    };
    const botId = `bot-${Date.now()}`;
    const initialBot: ChatMessage = {
      id: botId,
      role: 'assistant',
      text: '',
      trace: [],
      artifact: null,
      citations: [],
    };

    setMessages((prev) => [...prev, userMsg, initialBot]);
    setInput('');
    setStreaming(true);

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmed, stream: true }),
      });
      if (!resp.ok) {
        throw new Error(`API Hatası (${resp.status})`);
      }

      await consumeChatSse(resp, {
        onTraceStep: (step) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === botId ? { ...m, trace: [...(m.trace ?? []), step] } : m
            )
          );
        },
        onArtifact: (artifact) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === botId ? { ...m, artifact } : m))
          );
        },
        onDelta: (chunk) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === botId ? { ...m, text: m.text + chunk } : m))
          );
        },
        onCitations: (citations) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === botId ? { ...m, citations } : m))
          );
          if (citations.length > 0 && !selectedDisclosure) {
            void openDisclosure(citations[0].disclosure_id);
          }
        },
        onDone: (meta) => {
          setMessages((prev) =>
            prev.map((m) => (m.id === botId ? { ...m, meta } : m))
          );
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Bağlantı hatası';
      setMessages((prev) =>
        prev.map((m) =>
          m.id === botId ? { ...m, text: `Hata: ${msg}` } : m
        )
      );
    } finally {
      setStreaming(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void askQuestion(input);
  }

  function renderTextWithCitationButtons(text: string) {
    const parts = text.split(/(\[kap:[A-Za-z0-9_-]+\])/g);
    return parts.map((part, idx) => {
      const match = /^\[kap:([A-Za-z0-9_-]+)\]$/.exec(part);
      if (!match) return <Fragment key={idx}>{part}</Fragment>;
      const id = match[1];
      return (
        <button
          key={`${id}-${idx}`}
          type="button"
          onClick={() => void openDisclosure(id)}
          className="mx-0.5 inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/35 hover:bg-emerald-500/25 font-mono text-[11px] font-semibold cursor-pointer"
        >
          kap:{id}
        </button>
      );
    });
  }

  const isOfflineMode =
    health?.is_offline_fallback ||
    messages.some((m) => m.meta?.is_offline_fallback);

  const latestDoneMeta = [...messages].reverse().find((m) => m.meta)?.meta;
  const activeQuota = latestDoneMeta?.remaining_quota ?? health?.remaining_quota;
  const isDemoModeActive = Boolean(latestDoneMeta?.quota_exhausted || latestDoneMeta?.cached_hit);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top Bar */}
      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm px-4 sm:px-6 py-3">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-emerald-500/15 border border-emerald-500/35 flex items-center justify-center">
              <Bot className="w-4 h-4 text-emerald-400" aria-hidden="true" />
            </div>
            <div>
              <h1 className="text-base font-bold tracking-tight text-slate-100">
                KAP Agent
              </h1>
              <p className="text-[11px] text-slate-400">
                Yatırım tavsiyesi vermez — yalnızca yapılandırılmış KAP bildirim analizi
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            {health && (
              <span className="px-2.5 py-1 rounded-lg border border-slate-800 bg-slate-950 text-slate-300">
                {health.disclosures_count} Bildirim · {health.low_confidence_count} Düşük Güven
              </span>
            )}
            {activeQuota && (
              <span
                className={`px-2.5 py-1 rounded-lg border font-mono ${
                  isDemoModeActive
                    ? 'border-amber-500/40 bg-amber-950/40 text-amber-300'
                    : 'border-slate-800 bg-slate-950 text-slate-300'
                }`}
              >
                {latestDoneMeta?.quota_exhausted
                  ? 'DEMO MODU (Kota Koruması Aktif)'
                  : `Kota: ${activeQuota.remaining_ip_day} günlük`}
              </span>
            )}
            <button
              type="button"
              onClick={() => setAdminOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800 hover:bg-slate-700 text-slate-200 font-medium cursor-pointer"
            >
              <Lock className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
              <span>Admin Onay ({health?.pending_reviews_count ?? 0})</span>
            </button>
          </div>
        </div>
      </header>

      {/* Prominent Honest Offline / Synthetic Mode Banner */}
      {isOfflineMode && (
        <div
          role="status"
          className="border-b border-amber-500/40 bg-amber-950/50 px-4 sm:px-6 py-2 text-xs text-amber-200"
        >
          <div className="max-w-7xl mx-auto flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" aria-hidden="true" />
            <span>
              <strong>OFFLINE / SAHTE TEST MODU:</strong> Şu an canlı Gemini API anahtarı bağlı değil
              veya çevrimdışı test/sentetik önbellek kullanılıyor. Gerçek LLM ve canlı KAP değerlendirmesi
              için <code>GEMINI_API_KEY</code> ve gerçek veri kümesi bağlanmalıdır.
            </span>
          </div>
        </div>
      )}

      {/* Main Split Workspace */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-5 grid lg:grid-cols-12 gap-5 items-start">
        {/* Left/Center: Chat + Trace + Table/Chart */}
        <section className="lg:col-span-7 flex flex-col gap-4">
          {/* Quick Low-Confidence Chips */}
          {lowConfItems.length > 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-slate-900/70 p-3 space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-amber-300">
                <FileWarning className="w-4 h-4" aria-hidden="true" />
                <span>Düşük Güvenli Çıkarımlar (İncele / Düzelt)</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {lowConfItems.map((item) => (
                  <button
                    key={item.disclosure_id}
                    type="button"
                    onClick={() => void openDisclosure(item.disclosure_id)}
                    className="px-2.5 py-1 rounded-lg border border-amber-500/35 bg-amber-950/35 hover:bg-amber-950/60 text-xs text-amber-200 cursor-pointer"
                  >
                    {item.ticker} · kap:{item.disclosure_id} (%{Math.round(item.extraction_confidence * 100)})
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Chat Container */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 space-y-4">
            {messages.length === 0 ? (
              <div className="space-y-3 py-4">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
                  <Sparkles className="w-4 h-4 text-emerald-400" aria-hidden="true" />
                  <span>Örnek Sorular</span>
                </div>
                <div className="grid sm:grid-cols-2 gap-2">
                  {SAMPLE_QUESTIONS.map((q) => (
                    <button
                      key={q}
                      type="button"
                      onClick={() => void askQuestion(q)}
                      className="text-left p-2.5 rounded-lg border border-slate-800 bg-slate-950/70 hover:border-emerald-500/40 text-xs text-slate-300 transition-colors cursor-pointer"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {messages.map((m) => (
                  <div
                    key={m.id}
                    className={`rounded-xl p-3.5 text-sm ${
                      m.role === 'user'
                        ? 'bg-emerald-950/40 border border-emerald-500/30 ml-8 text-slate-100'
                        : 'bg-slate-950/75 border border-slate-800 text-slate-200'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400 mb-1.5">
                      <span className="font-semibold uppercase tracking-wider">
                        {m.role === 'user' ? 'Soru' : 'KAP Agent'}
                      </span>
                      {m.meta && (
                        <span className="inline-flex items-center gap-1.5 font-mono">
                          <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" aria-hidden="true" />
                          <span>
                            {m.meta.is_offline_fallback ? 'OFFLINE/SAHTE' : m.meta.provider_model} ·{' '}
                            {m.meta.total_duration_ms.toFixed(0)} ms
                          </span>
                        </span>
                      )}
                    </div>

                    <div className="whitespace-pre-line leading-relaxed text-[13px]">
                      {m.role === 'assistant' ? renderTextWithCitationButtons(m.text || 'Yanıt hazırlanıyor...') : m.text}
                    </div>

                    {m.citations && m.citations.length > 0 && (
                      <div className="mt-3 pt-2.5 border-t border-slate-800/80 flex flex-wrap gap-1.5">
                        {m.citations.map((c) => (
                          <button
                            key={c.disclosure_id}
                            type="button"
                            onClick={() => void openDisclosure(c.disclosure_id)}
                            className="px-2 py-1 rounded bg-slate-900 border border-slate-700 hover:border-emerald-500/50 text-[11px] text-slate-200 cursor-pointer"
                          >
                            <strong>{c.ticker}</strong> · kap:{c.disclosure_id}
                          </button>
                        ))}
                      </div>
                    )}

                    {m.artifact && (
                      <DataVisualizer
                        artifact={m.artifact}
                        onSelectDisclosure={(id) => void openDisclosure(id)}
                      />
                    )}

                    {m.trace && m.trace.length > 0 && <TracePanel trace={m.trace} />}
                  </div>
                ))}
              </div>
            )}

            <form onSubmit={handleSubmit} className="flex gap-2 pt-2 border-t border-slate-800">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={streaming}
                maxLength={500}
                placeholder="KAP bildirimleri hakkında soru sorun (ör. FROTO temettüsü nedir?)..."
                className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-xs text-slate-100 focus:outline-none focus:border-emerald-500"
              />
              <button
                type="submit"
                disabled={streaming || input.trim().length < 2}
                className="inline-flex items-center gap-1.5 px-4 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-slate-950 text-xs font-semibold cursor-pointer"
              >
                <Send className="w-3.5 h-3.5" aria-hidden="true" />
                <span>{streaming ? 'Çalışıyor...' : 'Sor'}</span>
              </button>
            </form>
          </div>
        </section>

        {/* Right: Source Disclosure Side Panel */}
        <section className="lg:col-span-5">
          <SourceDrawer
            disclosure={selectedDisclosure}
            loading={drawerLoading}
            onFlagField={(fieldName, currentValue) => {
              if (!selectedDisclosure) return;
              setFlagTarget({
                disclosureId: selectedDisclosure.disclosure_id,
                fieldName,
                currentValue,
              });
            }}
          />
        </section>
      </main>

      {flagTarget && (
        <FlagModal
          disclosureId={flagTarget.disclosureId}
          fieldName={flagTarget.fieldName}
          currentValue={flagTarget.currentValue}
          onClose={() => setFlagTarget(null)}
          onSubmitted={() => {
            void loadHealthAndLowConf();
          }}
        />
      )}

      {adminOpen && (
        <AdminReviewDrawer
          onClose={() => setAdminOpen(false)}
          onDataMutated={(id) => {
            void loadHealthAndLowConf();
            if (selectedDisclosure?.disclosure_id === id) {
              void openDisclosure(id);
            }
          }}
        />
      )}
    </div>
  );
}
