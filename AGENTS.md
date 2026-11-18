# AGENTS.md — KAP Agent Kuralları ve Mimari Sözleşmesi

Bu dosya Antigravity, Claude Code ve OpenAI Codex gibi kodlama ajanları için projenin değişmez mimari sınırlarını tanımlar.

## 1. Kapsam ve Sorumluluk Sınırı
- **Şirket Evreni:** BIST 30 içinden seçili banka dışı 15–20 şirket (`THYAO`, `TUPRS`, `FROTO`, `ASELS`, `BIMAS`, `EREGL`, `SISE`, `TCELL`, `TTKOM`, `KCHOL`, `SAHOL`, `TOASO`, `ARCLK`, `MGROS`, `PGSUS`, `ENKAI`, `ASTOR`, `KONTR`), son 1–2 yıl.
- **Banka Hariç Tutma Kuralı:** `GARAN`, `AKBNK`, `ISCTR`, `YKBNK`, `VAKBN`, `HALKB`, `TSKB`, `ALBRK`, `SKBNK` banka hisseleri ilk sürümde `financial_report` çıkarım şemasından hariçtir.
- **Al-Sat Önerisi Yasağı:** Sistem yatırım tavsiyesi, hedef fiyat tahmini veya al/sat/tut önerisi vermez.
- **Zorunlu Kaynak Atıfı (`Citation Gate`):** Ajan yanıtlarındaki her olgusal/sayısal ifade, o oturumdaki araç çıktılarından gelmiş gerçek bir `[kap:ID]` bildirim referansı içermelidir.

## 2. Veritabanı ve `run_sql` Güvenliği
- **Motor:** SQLite + FTS5 (`unicode61 remove_diacritics 2`).
- **Ayrı Bağlantı Fabrikaları (`backend/app/db/connection.py`):**
  - `get_readonly_agent_conn`: `file:...?mode=ro` URI kipi + `PRAGMA query_only = ON` + `set_progress_handler` (`1500ms` timeout). Ajan araçları **yalnızca** bu bağlantıyı kullanır.
  - `get_write_conn`: Yalnızca çevrimdışı veri yükleme CLI'ı, test kurulumu ve `review_flags` yazma/admin onay akışı için kullanılır.
- **Tek `SELECT` Kilidi (`sql_guard.py`):** `run_sql` yalnızca tek bir `SELECT` ifadesine izin verir; çoklu ifade (`;`), yazma/DDL/PRAGMA komutları (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `ATTACH`, `PRAGMA`, `WITH` dışı mutasyon vb.) ile `review_flags`/`sqlite_master` erişimi engellenir. `LIMIT` yoksa `LIMIT 50` eklenir, `LIMIT > 50` ise `50` ile sınırlandırılır.

## 3. Kullanıcı Düzeltmeleri (`review_flags`) ve Admin Onayı
- Kullanıcı arayüzünden gönderilen düşük güven veya düzeltme bildirimleri **ana veriyi doğrudan değiştirmez**.
- Tüm öneriler girdi temizleme (HTML/kontrol karakteri temizliği + alan beyaz listesi) ve IP rate-limit sonrası `review_flags` tablosuna `status = 'pending'` olarak yazılır.
- Ana tablolar (`disclosures`, `dividend_events`, `financial_reports`, `material_events`) yalnızca admin anahtarı (`ADMIN_TOKEN`) doğrulanan admin onayıyla güncellenir.

## 4. Veri, Önbellek ve Eval Disiplini
- `data/raw/`, `data/extractions/` ve `data/*.db` dosyaları `.gitignore` içindedir (repoya gönderilmez).
- `data/sample_seed/` (20–30 bildirimlik örnek veri seti) repoda tutulur; tüm otomatik testler ve varsayılan demo bu veriyle çalışır.
- Otomatik oluşturulan eval taslakları her zaman `"verified": false` etiketlenir; yalnızca insan doğrulamasından geçenler `"verified": true` işaretlenir.
- Her modül veya aşama değişikliği sonrası `backend` içinde `pytest` ve `evals/` kontrolleri çalıştırılır.
