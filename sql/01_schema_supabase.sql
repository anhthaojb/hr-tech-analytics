-- =============================================================================
-- FILE: 01_schema_supabase.sql
-- MÔ TẢ: Tạo toàn bộ bảng cho Supabase (PostgreSQL)
-- CHẠY FILE NÀY TRƯỚC trong Supabase SQL Editor
-- =============================================================================

-- =============================================================================
-- 0. HÀM AUTO-UPDATE updated_at (thay thế ON UPDATE CURRENT_TIMESTAMP của MySQL)
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

-- =============================================================================
-- 1. BẢNG STAGING: jobs
-- =============================================================================
CREATE TABLE IF NOT EXISTS jobs (
    id              SERIAL PRIMARY KEY,
    website         VARCHAR(50),
    job_title       TEXT,
    company_title   VARCHAR(255),
    location        VARCHAR(255),
    experience      VARCHAR(100),
    compensation    VARCHAR(255),
    job_type        VARCHAR(100),
    work_mode       VARCHAR(100),
    level           VARCHAR(100),
    job_url         VARCHAR(500) UNIQUE,
    company_size    VARCHAR(100),
    company_industry VARCHAR(255),
    job_category    VARCHAR(255),
    number_recruit  VARCHAR(50),
    education_level VARCHAR(100),
    job_description TEXT,
    job_requirement TEXT,
    raw_about_job   TEXT,
    job_posted_at   VARCHAR(20),
    job_deadline    VARCHAR(20),
    scraped_at      VARCHAR(30),
    is_valid        BOOLEAN DEFAULT TRUE,
    error_log       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_website   ON jobs(website);
CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs(company_title);
CREATE INDEX IF NOT EXISTS idx_jobs_location  ON jobs(location);
CREATE INDEX IF NOT EXISTS idx_jobs_is_valid  ON jobs(is_valid);
CREATE INDEX IF NOT EXISTS idx_jobs_scraped   ON jobs(scraped_at);

-- =============================================================================
-- 2. BẢNG ETL TRUNG GIAN: fact_jobs_etl
-- =============================================================================
CREATE TABLE IF NOT EXISTS fact_jobs_etl (
    etl_id                  BIGSERIAL PRIMARY KEY,
    src_id                  INT,
    job_url                 VARCHAR(500),
    scraped_at              VARCHAR(30),
    etl_run_id              INT,
    etl_processed_at        TIMESTAMP,
    website                 VARCHAR(50),
    website_clean           VARCHAR(50),
    job_posted_at           VARCHAR(50),
    job_deadline            VARCHAR(50),
    job_posted_at_clean     DATE,
    job_deadline_clean      DATE,
    job_title               TEXT,
    job_title_detect        VARCHAR(255),
    job_title_clean         VARCHAR(500),
    job_category_clean      VARCHAR(100),
    is_it                   BOOLEAN,
    company_title           VARCHAR(255),
    company_title_clean     VARCHAR(255),
    company_name_clean      VARCHAR(255),
    company_type            VARCHAR(50),
    company_canonical_key   VARCHAR(200),
    location                VARCHAR(255),
    location_province       VARCHAR(100),
    location_region         VARCHAR(30),
    is_vn                   BOOLEAN,
    job_type                VARCHAR(100),
    work_mode               VARCHAR(100),
    job_type_clean          VARCHAR(30),
    work_mode_clean         VARCHAR(30),
    compensation            VARCHAR(255),
    salary_min              BIGINT,
    salary_max              BIGINT,
    salary_currency         VARCHAR(10),
    conversion_rate         DOUBLE PRECISION,
    is_negotiable           BOOLEAN DEFAULT TRUE,
    experience              VARCHAR(100),
    exp_min_yr              REAL,
    exp_max_yr              REAL,
    is_exp_required         BOOLEAN,
    level                   VARCHAR(100),
    level_clean             VARCHAR(30),
    job_description         TEXT,
    job_requirement         TEXT,
    raw_about_job           TEXT,
    hard_skills             TEXT,
    soft_skills             TEXT,
    major                   VARCHAR(255),
    certifications          TEXT,
    languages               TEXT,
    education_level         VARCHAR(100),
    education_clean         VARCHAR(30),
    company_size            VARCHAR(100),
    company_size_min        INT,
    company_size_max        INT,
    company_industry        VARCHAR(255),
    industry_level1         VARCHAR(100),
    industry_level2         VARCHAR(150),
    job_category            VARCHAR(255),
    number_recruit          VARCHAR(50),
    number_recruit_clean    SMALLINT,
    is_valid                BOOLEAN DEFAULT TRUE,
    is_duplicate            BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_of_id         BIGINT,
    dedup_method            VARCHAR(20),
    error_log               TEXT,
    UNIQUE (job_url, location_province)
);
CREATE INDEX IF NOT EXISTS idx_etl_run        ON fact_jobs_etl(etl_run_id);
CREATE INDEX IF NOT EXISTS idx_etl_url        ON fact_jobs_etl(job_url);
CREATE INDEX IF NOT EXISTS idx_etl_website    ON fact_jobs_etl(website_clean);
CREATE INDEX IF NOT EXISTS idx_etl_province   ON fact_jobs_etl(location_province);
CREATE INDEX IF NOT EXISTS idx_etl_region     ON fact_jobs_etl(location_region);
CREATE INDEX IF NOT EXISTS idx_etl_posted     ON fact_jobs_etl(job_posted_at_clean);
CREATE INDEX IF NOT EXISTS idx_etl_scraped    ON fact_jobs_etl(scraped_at);
CREATE INDEX IF NOT EXISTS idx_etl_cat_title  ON fact_jobs_etl(job_category_clean, job_title_clean);
CREATE INDEX IF NOT EXISTS idx_etl_dedup      ON fact_jobs_etl(is_duplicate);

-- =============================================================================
-- 3. BẢNG DIM
-- =============================================================================

CREATE TABLE IF NOT EXISTS dim_nguon (
    nguon_id    SERIAL PRIMARY KEY,
    ten_nguon   VARCHAR(50) NOT NULL,
    UNIQUE (ten_nguon)
);

CREATE TABLE IF NOT EXISTS dim_capbac (
    cap_bac_id  SERIAL PRIMARY KEY,
    ten_cap_bac VARCHAR(30) NOT NULL,
    UNIQUE (ten_cap_bac)
);

CREATE TABLE IF NOT EXISTS dim_hinhthuc (
    hinh_thuc_id    SERIAL PRIMARY KEY,
    job_type        VARCHAR(30) NOT NULL DEFAULT 'Full-time',
    work_mode       VARCHAR(30) NOT NULL DEFAULT 'Onsite',
    UNIQUE (job_type, work_mode)
);

CREATE TABLE IF NOT EXISTS dim_diadiem (
    dia_diem_id SERIAL PRIMARY KEY,
    tinh_thanh  VARCHAR(100) NOT NULL,
    vung        VARCHAR(50)  NOT NULL DEFAULT 'Khác',
    is_vn       BOOLEAN,
    UNIQUE (tinh_thanh)
);

CREATE TABLE IF NOT EXISTS dim_nganh (
    nganh_id    SERIAL PRIMARY KEY,
    cap_do_1    VARCHAR(100),
    cap_do_2    VARCHAR(150),
    UNIQUE (cap_do_1, cap_do_2)   -- Thêm constraint để tránh duplicate
);

CREATE TABLE IF NOT EXISTS dim_congty (
    cong_ty_id      SERIAL PRIMARY KEY,
    ten_cong_ty     VARCHAR(255) NOT NULL,
    canonical_key   VARCHAR(200),
    company_type    VARCHAR(50),
    quy_mo_min      INT,
    quy_mo_max      INT,
    UNIQUE (ten_cong_ty)
);
CREATE INDEX IF NOT EXISTS idx_congty_canonical ON dim_congty(canonical_key);

CREATE TABLE IF NOT EXISTS dim_danhmuccongviec (
    danh_muc_id SERIAL PRIMARY KEY,
    ten_danh_muc VARCHAR(100) NOT NULL,
    UNIQUE (ten_danh_muc)
);

CREATE TABLE IF NOT EXISTS dim_require (
    require_id      SERIAL PRIMARY KEY,
    require_type    VARCHAR(20)  NOT NULL,
    require_value   VARCHAR(150) NOT NULL,
    UNIQUE (require_type, require_value)
);

-- =============================================================================
-- 4. BẢNG LOG / MONITORING
-- =============================================================================

CREATE TABLE IF NOT EXISTS fact_pipeline_snapshot (
    run_id          SERIAL PRIMARY KEY,
    website         VARCHAR(50),
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    duration_sec    INT,
    total_scraped   INT DEFAULT 0,
    new_jobs        INT DEFAULT 0,
    updated_jobs    INT DEFAULT 0,
    duplicate_jobs  INT DEFAULT 0,
    invalid_jobs    INT DEFAULT 0,
    error_jobs      INT DEFAULT 0,
    status          VARCHAR(20),
    session_id      VARCHAR(20),
    triggered_by    VARCHAR(20) DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS fact_etl_log (
    run_id          SERIAL PRIMARY KEY,
    run_date        DATE,
    mode            VARCHAR(20),
    target_date     DATE,
    started_at      TIMESTAMP,
    finished_at     TIMESTAMP,
    duration_sec    INT,
    total_input     INT DEFAULT 0,
    total_output    INT DEFAULT 0,
    new_rows        INT DEFAULT 0,
    updated_rows    INT DEFAULT 0,
    error_rows      INT DEFAULT 0,
    status          VARCHAR(20),
    note            TEXT
);

CREATE TABLE IF NOT EXISTS fact_error_detail (
    error_id        SERIAL PRIMARY KEY,
    run_id          INT REFERENCES fact_pipeline_snapshot(run_id),
    row_id          INT,
    column_name     VARCHAR(100),
    bad_value       TEXT,
    error_type      VARCHAR(50),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fact_etl_error (
    error_id        BIGSERIAL PRIMARY KEY,
    run_id          INT,
    src_id          INT,
    job_url         VARCHAR(500),
    field_name      VARCHAR(100),
    raw_value       TEXT,
    error_type      VARCHAR(50),
    error_detail    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_etl_err_run   ON fact_etl_error(run_id);
CREATE INDEX IF NOT EXISTS idx_etl_err_src   ON fact_etl_error(src_id);
CREATE INDEX IF NOT EXISTS idx_etl_err_field ON fact_etl_error(field_name);

-- =============================================================================
-- 5. FACT CHÍNH: fact_jobpostings
-- =============================================================================

CREATE TABLE IF NOT EXISTS fact_jobpostings (
    posting_id          BIGSERIAL PRIMARY KEY,
    etl_id              BIGINT    NOT NULL,
    job_url             VARCHAR(500) NOT NULL,
    dia_diem_id         INT,
    cong_ty_id          INT,
    nganh_id            INT,
    cap_bac_id          INT,
    hinh_thuc_id        INT,
    nguon_id            INT,
    danh_muc_id         INT,
    job_title_clean     VARCHAR(500),
    job_title_detect    VARCHAR(255),
    so_luong_tuyen      SMALLINT DEFAULT 1,
    salary_min          BIGINT,
    salary_max          BIGINT,
    salary_avg          BIGINT,
    salary_currency     VARCHAR(10),
    conversion_rate     DOUBLE PRECISION,
    is_negotiable       BOOLEAN DEFAULT TRUE,
    exp_min_yr          REAL,
    exp_max_yr          REAL,
    is_exp_required     BOOLEAN,
    is_it               BOOLEAN DEFAULT FALSE,
    is_duplicate        BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_of_id     BIGINT,
    dedup_method        VARCHAR(20),
    ngay_dang           DATE,
    etl_run_id          INT,
    ngay_crawl          DATE,
    ngay_deadline       DATE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (etl_id)
);
CREATE INDEX IF NOT EXISTS idx_fp_cong_ty       ON fact_jobpostings(cong_ty_id);
CREATE INDEX IF NOT EXISTS idx_fp_nganh         ON fact_jobpostings(nganh_id);
CREATE INDEX IF NOT EXISTS idx_fp_cap_bac       ON fact_jobpostings(cap_bac_id);
CREATE INDEX IF NOT EXISTS idx_fp_danh_muc      ON fact_jobpostings(danh_muc_id);
CREATE INDEX IF NOT EXISTS idx_fp_nguon         ON fact_jobpostings(nguon_id);
CREATE INDEX IF NOT EXISTS idx_fp_title_clean   ON fact_jobpostings(job_title_clean);

-- Trigger auto-update updated_at
CREATE TRIGGER trg_fp_updated_at
    BEFORE UPDATE ON fact_jobpostings
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- =============================================================================
-- 6. BRIDGE TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bridge_jobrequire (
    bridge_id   BIGSERIAL PRIMARY KEY,
    fact_id     BIGINT NOT NULL,
    require_id  INT    NOT NULL,
    UNIQUE (fact_id, require_id),
    CONSTRAINT fk_bridge_fact
        FOREIGN KEY (fact_id)
        REFERENCES fact_jobpostings(posting_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bridge_fact    ON bridge_jobrequire(fact_id);
CREATE INDEX IF NOT EXISTS idx_bridge_require ON bridge_jobrequire(require_id);

-- =============================================================================
-- 7. VIEWS
-- =============================================================================

CREATE OR REPLACE VIEW vw_jobpostings_unique AS
SELECT * FROM fact_jobpostings WHERE is_duplicate = FALSE;

CREATE OR REPLACE VIEW vw_jobpostings_all AS
SELECT * FROM fact_jobpostings;




ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ai_processed BOOLEAN DEFAULT FALSE;