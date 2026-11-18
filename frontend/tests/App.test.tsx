import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';

function buildSseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const payload = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join('');

  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(payload));
      controller.close();
    },
  });

  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

describe('KAP Agent UI', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows OFFLINE/SAHTE banner, streams SSE trace/chart/citations, opens SourceDrawer and submits FlagModal', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();

      if (url === '/api/health') {
        return Response.json({
          status: 'ok',
          disclosures_count: 24,
          low_confidence_count: 3,
          pending_reviews_count: 0,
          has_live_gemini: false,
          is_offline_fallback: true,
          provider_model: 'OFFLINE/SAHTE (Regex Fallback - Gercek LLM Degil)',
          execution_mode: 'OFFLINE/SAHTE',
        });
      }

      if (url.startsWith('/api/disclosures?low_confidence_only=true')) {
        return Response.json({
          count: 1,
          items: [
            {
              disclosure_id: '1401007',
              ticker: 'EREGL',
              company_name: 'Ereğli Demir ve Çelik',
              disclosure_type: 'dividend',
              published_at: '2026-03-14T18:40:00+03:00',
              source_url: 'https://www.kap.org.tr/tr/Bildirim/1401007',
              title: 'Kar Payı Dağıtımı',
              summary_tr: 'EREGL 2025 temettü teklifi',
              extraction_confidence: 0.72,
              low_confidence_fields: ['ex_dividend_date'],
              human_verified: false,
            },
          ],
        });
      }

      if (url === '/api/chat' && init?.method === 'POST') {
        return buildSseResponse([
          {
            event: 'trace_step',
            data: {
              step_index: 1,
              step_type: 'sql_retry',
              tool_name: 'run_sql',
              input_summary: 'SELECT net_dividend FROM dividend_events',
              output_summary: 'OperationalError: no such column',
              is_error: true,
              duration_ms: 4.2,
            },
          },
          {
            event: 'trace_step',
            data: {
              step_index: 2,
              step_type: 'tool_result',
              tool_name: 'run_sql',
              input_summary: 'SELECT disclosure_id, ticker, net_cash_per_share_try FROM dividend_events',
              output_summary: '2 satır döndü.',
              is_error: false,
              duration_ms: 5.8,
            },
          },
          {
            event: 'artifact',
            data: {
              columns: ['disclosure_id', 'ticker', 'net_cash_per_share_try'],
              rows: [
                { disclosure_id: '1401002', ticker: 'FROTO', net_cash_per_share_try: 39.15 },
                { disclosure_id: '1401007', ticker: 'EREGL', net_cash_per_share_try: 0.45 },
              ],
              sql_query: 'SELECT disclosure_id, ticker, net_cash_per_share_try FROM dividend_events LIMIT 50',
              chart_spec: {
                type: 'bar',
                title: 'Net Temettü Karşılaştırması',
                x_key: 'ticker',
                y_key: 'net_cash_per_share_try',
              },
            },
          },
          {
            event: 'delta',
            data: {
              text: 'FROTO net 39.15 TL açıklamıştır [kap:1401002], EREGL ise 0.45 TL teklif etmiştir [kap:1401007].',
            },
          },
          {
            event: 'citations',
            data: {
              citations: [
                {
                  disclosure_id: '1401007',
                  ticker: 'EREGL',
                  company_name: 'Ereğli Demir ve Çelik',
                  disclosure_type: 'dividend',
                  published_at: '2026-03-14T18:40:00+03:00',
                  title: 'Kar Payı Dağıtımı',
                  source_url: 'https://www.kap.org.tr/tr/Bildirim/1401007',
                  extraction_confidence: 0.72,
                  low_confidence_fields: ['ex_dividend_date'],
                },
              ],
            },
          },
          {
            event: 'done',
            data: {
              mode: 'offline_fallback',
              provider_model: 'OFFLINE/SAHTE (Regex Fallback - Gercek LLM Degil)',
              is_offline_fallback: true,
              sql_attempts: 2,
              sql_retries: 1,
              first_try_sql_success: false,
              refused_as_advice: false,
              no_evidence_found: false,
              total_duration_ms: 21.4,
            },
          },
        ]);
      }

      if (url === '/api/disclosures/1401007') {
        return Response.json({
          disclosure_id: '1401007',
          ticker: 'EREGL',
          company_name: 'Ereğli Demir ve Çelik Fabrikaları T.A.Ş.',
          disclosure_type: 'dividend',
          published_at: '2026-03-14T18:40:00+03:00',
          source_url: 'https://www.kap.org.tr/tr/Bildirim/1401007',
          title: 'Kar Payı Dağıtım İşlemlerine İlişkin Bildirim',
          summary_tr: 'EREGL 2025 teklif net 0.45 TL',
          extraction_confidence: 0.72,
          low_confidence_fields: ['ex_dividend_date'],
          human_verified: false,
          extracted_data: {
            decision_status: 'proposed',
            net_cash_per_share_try: 0.45,
            ex_dividend_date: null,
          },
          raw_text_excerpt: 'Hak kullanım tarihleri Genel Kurulda belirlenecektir.',
        });
      }

      if (url === '/api/reviews' && init?.method === 'POST') {
        return Response.json(
          {
            flag_id: 1,
            disclosure_id: '1401007',
            field_name: 'ex_dividend_date',
            suggested_value: '2026-06-01',
            status: 'pending',
          },
          { status: 201 }
        );
      }

      return Response.json({}, { status: 404 });
    });

    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    // Verify OFFLINE/SAHTE banner and non-advice disclaimer
    expect(await screen.findByText(/OFFLINE \/ SAHTE TEST MODU/i)).toBeInTheDocument();
    expect(screen.getByText(/Yatırım tavsiyesi vermez/i)).toBeInTheDocument();

    // Ask question via input
    const input = screen.getByPlaceholderText(/KAP bildirimleri hakkında soru sorun/i);
    fireEvent.change(input, { target: { value: 'Temettü sıralaması nedir?' } });
    fireEvent.click(screen.getByRole('button', { name: /Sor/i }));

    // Trace (including SQL Self-Healing), Chart SVG, and Table render
    expect(await screen.findByText(/1 SQL Self-Healing/i)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Net Temettü Karşılaştırması/i })).toBeInTheDocument();

    // Side SourceDrawer auto-loaded low-confidence disclosure 1401007
    expect(await screen.findByText(/Düşük Güvenli Çıkarım İşareti/i)).toBeInTheDocument();

    // Open FlagModal on ex_dividend_date
    const flagBtn = screen.getByRole('button', { name: /ex_dividend_date için düzeltme öner/i });
    fireEvent.click(flagBtn);

    const dialogInput = screen.getByPlaceholderText(/Örn: 2026-06-01/i);
    fireEvent.change(dialogInput, { target: { value: '2026-06-01' } });
    fireEvent.click(screen.getByRole('button', { name: /İncelemeye Gönder/i }));

    await waitFor(() => {
      expect(screen.getByText(/review_flags kuyruğuna kaydedildi/i)).toBeInTheDocument();
    });
  });
});
