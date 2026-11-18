# KAP Agent — BIST KAP Bildirim Analizi ve Self-Healing SQL Ajanı

> **Uyarı & Kapsam Sınırı:** Bu proje yalnızca Kamuyu Aydınlatma Platformu (KAP) bildirimlerini yapılandırılmış veriye dönüştürür ve kaynak bildirim ID'leriyle (`[kap:ID]`) doğrulanabilir analiz sunar. **Yatırım danışmanlığı, hedef fiyat tahmini veya al-sat önerisi vermez.**

---

## Mimari Genel Bakış

KAP Agent, halüsinasyon riskini ve API maliyetini düşürmek için **Çevrimdışı Yapılandırılmış Çıkarım** ile **Çevrimiçi Salt-Okunur Ajan Çalışma Zamanı**nı birbirinden ayırır:

1. **Çevrimdışı Veri & Çıkarım Hattı (`backend/app/pipeline/`)**:
   - **Kibar CLI Çekici (`fetcher.py`):** İstekler arası gecikme (`--delay-sec 2.5` + jitter), yerel disk önbelleği (`data/raw/`), özel `User-Agent` ve V1 banka hariç tutma filtresi uygular.
   - **Pydantic v2 Çıkarıcı (`extractor.py`):** 3 bildirim türü (`dividend`, `financial_report`, `material_event`) için **bildirim başına 1 kez** SHA-256 içerik özetiyle önbellekli JSON çıkarımı yapar; `LLMProvider` arayüzü sayesinde model kolayca değiştirilebilir.
   - **SQLite + FTS5 (`schema.sql`, `connection.py`):** Tam metin arama (`unicode61 remove_diacritics 2`) ve ilişkisel tablolar (`disclosures`, `dividend_events`, `financial_reports`, `material_events`) tutar.

2. **Çevrimiçi Ajan Runtime & Güvenlik Katmanı (`backend/app/agent/`)**:
   - **Çift Bağlantı İzolasyonu:** Ajan araçları **yalnızca** `mode=ro` + `PRAGMA query_only = ON` + `1500ms` timeout ile açılan `get_readonly_agent_conn` üzerinden çalışır.
   - **Tek-`SELECT` `sql_guard.py`:** Çoklu ifade (`;`), yorum satırı (`--`, `/* */`), `WITH` CTE, yazma/DDL/PRAGMA ifadeleri ile `review_flags` / `sqlite_master` erişimini engeller; `LIMIT <= 50` zorunlu kılar.
   - **SQL Self-Healing Döngüsü (`orchestrator.py`):** `run_sql` hatası döndüğünde hata mesajı ve şema özeti ajana geri beslenir; ajan sorguyu en fazla **3 kez** (`max_sql_retries=3`) kendi kendine düzeltir.
   - **Al-Sat Guardrail & `citation_gate.py`:** Yatırım tavsiyesi sorularını 0 LLM maliyetiyle reddeder; yanıtlardaki `[kap:ID]` atıflarını araç çıktıları ve veritabanıyla doğrulamadan kullanıcıya dönmez.

3. **Kullanıcı Düzeltme Kuyruğu & Kota Koruması (`backend/app/api/`)**:
   - **`review_flags` + Admin Onayı (`routes_reviews.py`):** Arayüzden gelen düzeltme önerileri HTML/script temizliği ve IP rate-limit sonrası `status = 'pending'` olarak kuyruğa alınır; ana tablolar yalnızca `X-Admin-Token` doğrulamalı admin kararıyla güncellenir.
   - **Kota Koruması (`quota_guard.py`):** Sorgu önbelleği, IP/günlük limitler ve limit dolduğunda örnek sorular için sıfır maliyetli **Demo Modu** içerir.

4. **İnce MCP Server Katmanı (`backend/app/mcp/server.py`)**:
   - Aynı salt-okunur `search_disclosures`, `get_disclosure` ve `run_sql` araçlarını stdio JSON-RPC 2.0 MCP sunucusu olarak Claude Code, Antigravity veya Claude Desktop'a açar.

---

## Veri Kaynağı Şeffaflığı ve Eval Sonuçları

- Repodaki `data/sample_seed/` (24 bildirim: 8 temettü, 8 finansal rapor, 8 özel durum) kurulumsuz test ve CI doğrulaması için üretilmiş **sentetik test verisidir** (`SYNTHETIC_SAMPLE_SEED`).
- `evals/extraction_gold.jsonl` (50 vaka) ve `evals/agent_gold.jsonl` (30 soru) dosyalarındaki tüm taslak kayıtlar `"verified": false` olarak başlar.
- Değerlendirme betikleri **yalnızca `"verified": true` işaretli kayıtlar üzerinden** doğruluk yüzdesi hesaplar; doğrulanmamış kayıtlarda **`"ölçülmedi"`**, API anahtarsız koşullarda ise açıkça **`OFFLINE/SAHTE`** yazar.

| Eval Paketi | Doğrulanmış (`verified: true`) | Ölçüm Modu | Sonuç (İlk Kurulum / Taslak) |
| --- | --- | --- | --- |
| Çıkarım Eval (`evals/run_extraction_eval.py`, 50 vaka) | 0 / 50 | `OFFLINE/SAHTE` | `ölçülmedi` (50/50 şema geçerli) |
| Agent Eval (`evals/run_agent_eval.py`, 30 soru) | 0 / 30 | `OFFLINE/SAHTE` | `ölçülmedi` (30/30 çökmeden çalıştı) |

*(Yerelde gerçek KAP verisi ve `GEMINI_API_KEY` ile `verified: true` doğrulaması yapıldıktan sonra gerçek Before / After değerleri `--live` çıktısıyla güncellenir.)*

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
# .env içine Google AI Studio GEMINI_API_KEY değerinizi ekleyin

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
