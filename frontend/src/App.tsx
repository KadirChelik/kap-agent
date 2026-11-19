import { Fragment, useCallback, useEffect, useState, type FormEvent } from 'react';
import { AlertTriangle, ArrowUpRight, Lock, RefreshCw, Send } from 'lucide-react';
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

const SAMPLE_QUESTIONS: readonly { tag: string; prompt: string }[] = [
  {
    tag: 'TEMETTÜ',
    prompt: 'Hisse başına en yüksek net nakit temettü dağıtan 3 şirket hangileridir?',
  },
  {
    tag: 'FİNANSAL',
    prompt: "ASELS ve FROTO'nun 2025/09 hasılat ve net kârını karşılaştır",
  },
  {
    tag: 'SÖZLEŞME',
    prompt: 'ENKAI ve PGSUS yeni iş ilişkisi sözleşmelerini listele',
  },
  {
    tag: 'GUARDRAIL',
    prompt: 'THYAO hissesi bu fiyattan alınır mı?',
  },
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
  const [syncing, setSyncing] = useState(false);

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
    const timer = window.setInterval(() => {
      void loadHealthAndLowConf();
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [loadHealthAndLowConf]);

  async function handleManualSync() {
    if (syncing) return;
    setSyncing(true);
    try {
      await fetch('/api/sync', { method: 'POST' });
      await loadHealthAndLowConf();
    } finally {
      setSyncing(false);
    }
  }

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
          className="mx-0.5 inline-flex items-center px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-300 border border-teal-500/30 hover:bg-teal-500/20 font-mono text-[11px] font-medium cursor-pointer transition-colors"
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
    <div className="min-h-screen bg-[#0b0d11] text-[#eceff4] flex flex-col">
      {/* Slim Editorial Top Bar */}
      <header className="border-b border-[#1f232c] bg-[#0e1015]/95 backdrop-blur-sm px-4 sm:px-6 py-2.5 sticky top-0 z-30">
        <div className="max-w-[1360px] mx-auto flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex items-baseline gap-2">
              <h1 className="font-mono text-sm font-semibold tracking-tight text-[#eceff4]">
                KAP<span className="text-teal-400">//</span>AGENT
              </h1>
              <span className="hidden sm:inline text-[#383f4f]" aria-hidden="true">
                |
              </span>
              <p className="hidden sm:inline text-[11px] text-[#8b93a1]">
                BIST 30 Kurumsal Bildirim Analizi · Yatırım tavsiyesi vermez
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            {health && (
              <span className="px-2.5 py-1 rounded border border-[#23262f] bg-[#12141a] text-[#9ca3af] font-mono tabular-nums">
                {health.disclosures_count} bildirim
                {health.latest_disclosure_at
                  ? ` · Son: ${new Date(health.latest_disclosure_at).toLocaleDateString('tr-TR')}`
                  : ''}
                {` · ${health.low_confidence_count} düşük güven`}
              </span>
            )}
            <button
              type="button"
              onClick={() => void handleManualSync()}
              disabled={syncing || health?.sync?.running}
              title={
                health?.sync?.auto_sync_enabled
                  ? `Otomatik KAP senkronizasyonu aktif (${Math.round((health.sync.interval_sec || 900) / 60)} dk)`
                  : 'Yeni KAP bildirimlerini tara'
              }
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-[#282d39] bg-[#14171f] hover:border-teal-500/45 disabled:opacity-50 text-[#d1d5db] font-medium cursor-pointer transition-colors"
            >
              <RefreshCw
                className={`w-3 h-3 text-teal-400 ${syncing || health?.sync?.running ? 'animate-spin' : ''}`}
                aria-hidden="true"
              />
              <span>{syncing || health?.sync?.running ? 'Taranıyor...' : 'Güncelle'}</span>
            </button>
            {activeQuota && (
              <span
                className={`px-2.5 py-1 rounded border font-mono tabular-nums ${
                  isDemoModeActive
                    ? 'border-amber-500/35 bg-amber-950/30 text-amber-300'
                    : 'border-[#23262f] bg-[#12141a] text-[#9ca3af]'
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
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-[#282d39] bg-[#14171f] hover:border-[#3b4252] text-[#d1d5db] font-medium cursor-pointer transition-colors"
            >
              <Lock className="w-3 h-3 text-teal-400" aria-hidden="true" />
              <span>Admin Onay ({health?.pending_reviews_count ?? 0})</span>
            </button>
          </div>
        </div>
      </header>

      {/* Honest Offline / Fallback Notice */}
      {isOfflineMode && (
        <div
          role="status"
          className="border-b border-amber-500/35 bg-amber-950/35 px-4 sm:px-6 py-2 text-xs text-amber-200"
        >
          <div className="max-w-[1360px] mx-auto flex items-center gap-2">
            <AlertTriangle className="w-3.5 h-3.5 text-amber-400 shrink-0" aria-hidden="true" />
            <span>
              <strong>OFFLINE / SAHTE TEST MODU:</strong> Şu an canlı Gemini API anahtarı bağlı değil
              veya çevrimdışı test/sentetik önbellek kullanılıyor. Gerçek LLM ve canlı KAP değerlendirmesi
              için <code>GEMINI_API_KEY</code> ve gerçek veri kümesi bağlanmalıdır.
            </span>
          </div>
        </div>
      )}

      {/* Main Split Terminal Workspace */}
      <main className="flex-1 max-w-[1360px] w-full mx-auto px-4 sm:px-6 py-5 grid lg:grid-cols-12 gap-5 items-start">
        {/* Left/Center: Conversation Thread + Table/Chart + Input */}
        <section className="lg:col-span-7 flex flex-col">
          <div className="rounded-lg border border-[#23262f] bg-[#12141a] overflow-hidden flex flex-col">
            <div className="px-4 py-2.5 border-b border-[#1f232c] bg-[#0e1015] flex items-center justify-between text-[11px]">
              <span className="font-mono uppercase tracking-wider text-[#8b93a1] font-medium">
                Sorgu & Doğrulama Akışı
              </span>
              <span className="font-mono text-[10px] text-[#6b7280]">
                Salt-Okunur SQL · [kap:ID] Zorunlu
              </span>
            </div>

            <div className="p-4 sm:p-5 space-y-5">
              {messages.length === 0 ? (
                <div className="space-y-4 py-2">
                  <div className="space-y-1">
                    <h2 className="text-sm font-semibold text-[#eceff4]">
                      Doğrulanabilir KAP Bildirim Sorgulama
                    </h2>
                    <p className="text-xs text-[#8b93a1] leading-relaxed">
                      Temettü, finansal rapor ve özel durum açıklamalarını salt-okunur SQL ile
                      sorgulayın. Her sayısal yanıt kaynak bildirim ID&apos;siyle doğrulanır.
                    </p>
                  </div>

                  <div className="grid sm:grid-cols-2 gap-2.5">
                    {SAMPLE_QUESTIONS.map((item) => (
                      <button
                        key={item.prompt}
                        type="button"
                        onClick={() => void askQuestion(item.prompt)}
                        className="group text-left p-3 rounded-md border border-[#23262f] bg-[#0e1015] hover:border-teal-500/40 hover:bg-[#141820] transition-colors cursor-pointer flex flex-col justify-between gap-2"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-[10px] tracking-wider text-teal-400/80">
                            {item.tag}
                          </span>
                          <ArrowUpRight
                            className="w-3.5 h-3.5 text-[#5c6472] group-hover:text-teal-300 transition-colors"
                            aria-hidden="true"
                          />
                        </div>
                        <span className="text-xs text-[#d1d5db] leading-snug">{item.prompt}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="divide-y divide-[#1f232c]">
                  {messages.map((m, idx) => (
                    <div
                      key={m.id}
                      className={idx === 0 ? 'pb-4' : 'py-4 first:pt-0 last:pb-1'}
                    >
                      {m.role === 'user' ? (
                        <div className="border-l-2 border-teal-400/70 pl-3 py-0.5">
                          <div className="font-mono text-[10px] uppercase tracking-wider text-[#6b7280] mb-1">
                            Soru
                          </div>
                          <p className="text-sm font-medium text-[#eceff4] leading-snug">{m.text}</p>
                        </div>
                      ) : (
                        <div>
                          <div className="flex items-center justify-between gap-2 text-[10px] text-[#6b7280] mb-2 font-mono">
                            <span className="uppercase tracking-wider text-teal-400/90 font-semibold">
                              KAP Agent Yanıtı
                            </span>
                            {m.meta && (
                              <span className="tabular-nums">
                                {m.meta.is_offline_fallback ? 'OFFLINE/SAHTE' : m.meta.provider_model}{' '}
                                · {m.meta.total_duration_ms.toFixed(0)} ms
                              </span>
                            )}
                          </div>

                          <div className="whitespace-pre-line leading-relaxed text-[13px] text-[#e5e7eb]">
                            {renderTextWithCitationButtons(m.text || 'Sorgu yürütülüyor...')}
                          </div>

                          {m.citations && m.citations.length > 0 && (
                            <div className="mt-3 flex flex-wrap items-center gap-1.5">
                              <span className="font-mono text-[10px] uppercase tracking-wider text-[#6b7280] mr-1">
                                Kaynaklar:
                              </span>
                              {m.citations.map((c) => (
                                <button
                                  key={c.disclosure_id}
                                  type="button"
                                  onClick={() => void openDisclosure(c.disclosure_id)}
                                  className="px-2 py-0.5 rounded bg-[#0e1015] border border-[#262a35] hover:border-teal-500/45 font-mono text-[11px] text-[#d1d5db] cursor-pointer transition-colors"
                                >
                                  <strong className="text-teal-300">{c.ticker}</strong> · kap:{c.disclosure_id}
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
                      )}
                    </div>
                  ))}
                </div>
              )}

              <form
                onSubmit={handleSubmit}
                className="flex gap-2 pt-3 border-t border-[#1f232c]"
              >
                <input
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  disabled={streaming}
                  maxLength={500}
                  placeholder="KAP bildirimleri hakkında soru sorun (ör. FROTO temettüsü nedir?)..."
                  className="flex-1 rounded-md border border-[#282d39] bg-[#0b0d11] px-3 py-2 text-xs text-[#eceff4] placeholder:text-[#5c6472] focus:outline-none focus:border-teal-400"
                />
                <button
                  type="submit"
                  disabled={streaming || input.trim().length < 2}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-md bg-teal-500 hover:bg-teal-400 disabled:opacity-45 text-[#081014] text-xs font-semibold cursor-pointer transition-colors"
                >
                  <Send className="w-3.5 h-3.5" aria-hidden="true" />
                  <span>{streaming ? 'Çalışıyor...' : 'Sor'}</span>
                </button>
              </form>
            </div>
          </div>
        </section>

        {/* Right: Source Disclosure & Low-Confidence Inspector */}
        <section className="lg:col-span-5 lg:sticky lg:top-16">
          <SourceDrawer
            disclosure={selectedDisclosure}
            loading={drawerLoading}
            lowConfItems={lowConfItems}
            onSelectDisclosure={(id) => void openDisclosure(id)}
            onClearSelection={() => setSelectedDisclosure(null)}
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
