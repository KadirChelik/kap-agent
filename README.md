# KAP Agent — BIST KAP Bildirim Analizi ve Self-Healing SQL Ajanı

> **Uyarı & Kapsam Sınırı:** Bu proje yalnızca Kamuyu Aydınlatma Platformu (KAP) bildirimlerini yapılandırılmış veriye dönüştürür ve kaynak bildirim ID'leriyle (`[kap:ID]`) doğrulanabilir analiz sunar. **Yatırım danışmanlığı, hedef fiyat tahmini veya al-sat önerisi vermez.**

---

## Mimari Genel Bakış

KAP Agent, halüsinasyon riskini ve API maliyetini düşürmek için **Çevrimdışı Yapılandırılmış Çıkarım** ile **Çevrimiçi Salt-Okunur Ajan Çalışma Zamanı**nı birbirinden ayırır:

1. **Çevrimdışı Veri & Çıkarım Hattı (`backend/app/pipeline/`)**:
   - **Kibar CLI Çekici (`fetcher.py`):** 85 günlük dönem parçalaması, `byCriteria` ve bildirim istekleri arası gecikme + jitter, yerel disk önbelleği (`data/raw/`), özel `User-Agent` ve V1 banka hariç tutma filtresi uygular.
   - **Pydantic v2 Çıkarıcı (`extractor.py`) ve Çoklu Sağlayıcı Zinciri (`backend/app/llm/`):** 3 bildirim türü (`dividend`, `financial_report`, `material_event`) için **bildirim başına 1 kez** SHA-256 içerik özetiyle önbellekli JSON çıkarımı yapar. `prepare_extraction_text` ile büyük XBRL HTML raporlarını (`150+ KB`) gürültüden arındırarak `25.000` karakter sınırı içinde gelir tablosu/bilanço özetini korur. `FallbackChainProvider` giden hız sınırlaması (`llm_min_interval_sec`) ve `429 Retry-After` beklemesiyle `Gemini -> Groq -> OpenRouter` arasında otomatik geçiş yapar.
   - **SQLite + FTS5 (`schema.sql`, `connection.py`):** Tam metin arama (`unicode61 remove_diacritics 2`) ve ilişkisel tablolar (`disclosures`, `dividend_events`, `financial_reports`, `material_events`) tutar.

2. **Çevrimiçi Ajan Runtime & Güvenlik Katmanı (`backend/app/agent/`)**:
   - **Çift Bağlantı İzolasyonu:** Ajan araçları **yalnızca** `mode=ro` + `PRAGMA query_only = ON` + `1500ms` timeout ile açılan `get_readonly_agent_conn` üzerinden çalışır.
   - **Tek-`SELECT` `sql_guard.py`:** Çoklu ifade (`;`), yorum satırı (`--`, `/* */`), `WITH` CTE, yazma/DDL/PRAGMA ifadeleri ile `review_flags` / `sqlite_master` erişimini engeller; `LIMIT <= 50` zorunlu kılar.
   - **SQL Self-Healing Döngüsü (`orchestrator.py`):** `run_sql` hatası döndüğünde hata mesajı ve şema özeti ajana geri beslenir; ajan sorguyu en fazla **3 kez** (`max_sql_retries=3`) kendi kendine düzeltir.
   - **Al-Sat Guardrail & `citation_gate.py`:** Yatırım tavsiyesi sorularını 0 LLM maliyetiyle reddeder; yanıtlardaki `[kap:ID]` atıflarını araç çıktıları ve veritabanıyla doğrulamadan kullanıcıya dönmez.

3. **Kullanıcı Düzeltme Kuyruğu & Kota Koruması (`backend/app/api/`)**:
   - **`review_flags` + Admin Onayı (`routes_reviews.py`):** Arayüzden gelen düzeltme önerileri HTML/script temizliği ve IP rate-limit sonrası `status = 'pending'` olarak kuyruğa alınır; ana tablolar yalnızca `X-Admin-Token` doğrulamalı admin kararıyla güncellenir.
   - **Kota ve Hız Koruması (`quota_guard.py`, `main.py`):** Sorgu önbelleği, `/api/*` için IP başına global `60/dk` sliding-window rate-limit middleware'i (`429 Retry-After`), günlük limitler ve örnek sorular için sıfır maliyetli **Demo Modu** içerir.

4. **İnce MCP Server Katmanı (`backend/app/mcp/server.py`)**:
   - Aynı salt-okunur `search_disclosures`, `get_disclosure` ve `run_sql` araçlarını stdio JSON-RPC 2.0 MCP sunucusu olarak Claude Code, Antigravity veya Claude Desktop'a açar.

---

## Veri Kaynağı Şeffaflığı ve Canlı Eval Sonuçları

- Repodaki `data/sample_seed/` (24 bildirim: 8 temettü, 8 finansal rapor, 8 özel durum) kurulumsuz unit test ve CI doğrulaması için deterministik test verisi olarak korunur (`SYNTHETIC_SAMPLE_SEED`).
- Yerel üretim veritabanı (`data/kap_agent.db`, `.gitignore` altında) BIST 30'daki 18 banka dışı şirketten `fetcher.py` ile çekilmiş **102 gerçek KAP bildirimi** içerir (`7` temettü/avans, `19` finansal/faaliyet/sorumluluk bildirimi, `76` özel durum açıklaması).
- `evals/extraction_gold.jsonl` (50 gerçek bildirim) ve `evals/agent_gold.jsonl` (30 gerçek soru) kayıtları canlı `https://www.kap.org.tr/tr/Bildirim/<ID>` sayfalarıyla (`51/51 HTTP 200 OK`) karşılaştırılıp doğrulanarak **`"verified": true`** işaretlenmiştir.
- Değerlendirme betikleri **yalnızca `"verified": true` kayıtlar üzerinden** metrik hesaplar; API anahtarı olmayan/offline koşullarda çalıştırıldığında **`OFFLINE/SAHTE`** uyarısı basar.

| Eval Paketi | Doğrulanmış (`verified: true`) | Ölçüm Modu (`--live`) | Ölçülen Gerçek Sonuçlar |
| --- | --- | --- | --- |
| Çıkarım Eval (`evals/run_extraction_eval.py`) | 50 / 50 | `LIVE_LLM (gemini-3-flash-preview)` | Şema Geçerliliği `%100.0` · Alan Eşleşmesi `%93.84` (`financial_report` `%99.12`, `dividend` `%92.86`, `material_event` `%89.17`) · Sayısal Doğruluk `%92.31` *(XBRL ön-işleme öncesi `%82.05`)* |
| Agent Eval (`evals/run_agent_eval.py`) | 30 / 30 | `LIVE_LLM (gemini-3-flash-preview)` | İlk Denemede SQL `%95.83` (ort. tekrar `0.03`) · Citation Precision `%100.0` / Recall `%100.0` · Tavsiye Reddi `%100.0` · Cevap Eşleşmesi `%90.0` (`p50: 24,095.89 ms`) |

---

## Kapsam ve Bilinen Sınırlar

- **Şirket Evreni:** BIST 30 içinden seçili 18 reel sektör şirketi (`THYAO`, `TUPRS`, `FROTO`, `ASELS`, `BIMAS`, `EREGL`, `SISE`, `TCELL`, `TTKOM`, `KCHOL`, `SAHOL`, `TOASO`, `ARCLK`, `MGROS`, `PGSUS`, `ENKAI`, `ASTOR`, `KONTR`).
- **Bankalar Hariç:** `GARAN`, `AKBNK`, `ISCTR`, `YKBNK`, `VAKBN`, `HALKB`, `TSKB`, `ALBRK`, `SKBNK` banka hisseleri BDDK bilanço formatı farkı nedeniyle V1 `financial_report` şemasından hariçtir.
- **Kapsam Dışı:** Al-sat önerisi, kullanıcı girişi, vektör veritabanı, fine-tuning ve çoklu ajan (multi-agent swarm) mimarisi.

---

## Hızlı Başlangıç

### 1. Ortam Değişkenleri ve Backend (FastAPI)
```powershell
Copy-Item .env.example .env
# .env içine GEMINI_API_KEY (ve isteğe bağlı yedek GROQ_API_KEY / OPENROUTER_API_KEY) ekleyin

cd backend
python -m pip install -e . pytest
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend (React + TypeScript + Vite)
```powershell
cd frontend
npm install
npm run dev
```

### 3. Yerelde Gerçek KAP Verisi Çekme ve Canlı Eval (`--live`)
```powershell
cd backend
python -m app.pipeline.fetcher --limit 25 --delay-sec 2.5 --start-date 2025-01-01
python -m app.pipeline.extractor
python -m app.pipeline.fetcher --skip-fetch --promote-to-sample 24 --build-gold-draft ..\evals\extraction_gold.jsonl
python -m app.db.seed_loader --dataset full

# evals/*.jsonl içinde kontrol ettiğiniz kayıtlara "verified": true yazdıktan sonra:
cd ..
python evals/run_extraction_eval.py --live
python evals/run_agent_eval.py --live
```

### 4. Testler
```powershell
# Backend (pytest)
cd backend && python -m pytest

# Frontend (vitest + build)
cd frontend && npm test && npm run build
```

### 5. MCP Server Entegrasyonu (`mcp_config.json`)
```json
{
  "mcpServers": {
    "kap-agent": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "C:/Users/Windows/Desktop/kap-agent/backend"
    }
  }
}
```
