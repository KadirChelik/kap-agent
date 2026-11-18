-- KAP Agent SQLite + FTS5 Schema (V1: BIST 30 non-bank disclosures)

CREATE TABLE IF NOT EXISTS disclosures (
    disclosure_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    disclosure_type TEXT NOT NULL CHECK(disclosure_type IN ('dividend', 'financial_report', 'material_event')),
    published_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary_tr TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    extracted_json TEXT NOT NULL,
    extraction_confidence REAL NOT NULL CHECK(extraction_confidence >= 0.0 AND extraction_confidence <= 1.0),
    low_confidence_fields TEXT NOT NULL DEFAULT '[]',
    human_verified INTEGER NOT NULL DEFAULT 0 CHECK(human_verified IN (0, 1)),
    is_banking_entity INTEGER NOT NULL DEFAULT 0 CHECK(is_banking_entity = 0),
    content_sha256 TEXT NOT NULL,
    extractor_model TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_disclosures_ticker ON disclosures(ticker);
CREATE INDEX IF NOT EXISTS idx_disclosures_type ON disclosures(disclosure_type);
CREATE INDEX IF NOT EXISTS idx_disclosures_published_at ON disclosures(published_at DESC);

-- Full-text search index over disclosure headlines, summaries, and body text
CREATE VIRTUAL TABLE IF NOT EXISTS disclosures_fts USING fts5(
    disclosure_id UNINDEXED,
    ticker,
    title,
    summary_tr,
    raw_text,
    content='disclosures',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS disclosures_ai AFTER INSERT ON disclosures BEGIN
    INSERT INTO disclosures_fts(rowid, disclosure_id, ticker, title, summary_tr, raw_text)
    VALUES (new.rowid, new.disclosure_id, new.ticker, new.title, new.summary_tr, new.raw_text);
END;

CREATE TRIGGER IF NOT EXISTS disclosures_ad AFTER DELETE ON disclosures BEGIN
    INSERT INTO disclosures_fts(disclosures_fts, rowid, disclosure_id, ticker, title, summary_tr, raw_text)
    VALUES ('delete', old.rowid, old.disclosure_id, old.ticker, old.title, old.summary_tr, old.raw_text);
END;

CREATE TRIGGER IF NOT EXISTS disclosures_au AFTER UPDATE ON disclosures BEGIN
    INSERT INTO disclosures_fts(disclosures_fts, rowid, disclosure_id, ticker, title, summary_tr, raw_text)
    VALUES ('delete', old.rowid, old.disclosure_id, old.ticker, old.title, old.summary_tr, old.raw_text);
    INSERT INTO disclosures_fts(rowid, disclosure_id, ticker, title, summary_tr, raw_text)
    VALUES (new.rowid, new.disclosure_id, new.ticker, new.title, new.summary_tr, new.raw_text);
END;

-- Structured Dividend / Kar Payı Table
CREATE TABLE IF NOT EXISTS dividend_events (
    disclosure_id TEXT PRIMARY KEY REFERENCES disclosures(disclosure_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    decision_status TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    gross_cash_per_share_try REAL,
    net_cash_per_share_try REAL,
    total_gross_cash_dividend_try REAL,
    bonus_share_rate_pct REAL,
    payment_type TEXT NOT NULL,
    ex_dividend_date TEXT,
    payment_date TEXT,
    evidence_quote TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dividend_ticker_year ON dividend_events(ticker, fiscal_year);

-- Structured Non-Bank Financial Reports Table
CREATE TABLE IF NOT EXISTS financial_reports (
    disclosure_id TEXT PRIMARY KEY REFERENCES disclosures(disclosure_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    period TEXT NOT NULL,
    consolidation TEXT NOT NULL,
    currency TEXT NOT NULL,
    presentation_scale TEXT NOT NULL,
    revenue_try REAL,
    gross_profit_try REAL,
    ebitda_try REAL,
    operating_profit_try REAL,
    net_profit_try REAL,
    total_assets_try REAL,
    total_equity_try REAL,
    net_debt_try REAL,
    evidence_quote TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_financial_ticker_period ON financial_reports(ticker, period);

-- Structured Material Events Table
CREATE TABLE IF NOT EXISTS material_events (
    disclosure_id TEXT PRIMARY KEY REFERENCES disclosures(disclosure_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    event_category TEXT NOT NULL,
    counterparty TEXT,
    contract_amount_original REAL,
    contract_currency TEXT,
    contract_amount_try_equiv REAL,
    revenue_ratio_pct REAL,
    expected_impact TEXT NOT NULL,
    evidence_quote TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_material_ticker_cat ON material_events(ticker, event_category);

-- Public Review Flags Queue (isolated from agent run_sql)
CREATE TABLE IF NOT EXISTS review_flags (
    flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    disclosure_id TEXT NOT NULL REFERENCES disclosures(disclosure_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    current_value TEXT,
    suggested_value TEXT NOT NULL,
    reason_note TEXT,
    reporter_ip_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    reviewer_note TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_flags_status ON review_flags(status, created_at DESC);
