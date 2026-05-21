# etl_supabase.py
# ==============================================================================
# RECRUITMENT ETL — Phiên bản Supabase (PostgreSQL)
#
# THAY ĐỔI SO VỚI etl.py (MySQL):
#   [1] DATABASE_URL  → postgresql+psycopg2 (Supabase)
#   [2] Bỏ DDL strings + _ensure_tables() + _migrate_tables()
#   [3] _start_log    → CURDATE() → CURRENT_DATE | lastrowid → RETURNING run_id
#   [4] _finish_log   → TIMESTAMPDIFF(...) → EXTRACT(EPOCH FROM ...)::int
#   [5] _load         → DATE(scraped_at)=CURDATE() → LEFT(scraped_at,10)=CURRENT_DATE::text
#                       (FIX B1: dùng LEFT() thay ::date để tránh crash khi scraped_at có format lạ)
#   [6] _save_fact    → ON DUPLICATE KEY UPDATE → ON CONFLICT ... DO UPDATE SET EXCLUDED.
#                       + boolean 0/1 → True/False
#   [7] _dedup_and_flag → is_valid=TRUE | FALSE/TRUE
#   [8] _load_dwh     → CALL sp_ETL_Load_DW → SELECT sp_etl_load_dw
#   [9] run()         → gọi _load_dwh() tích hợp trong run()
#  [10] TYPESENSE_ENABLED flag — skip _match_and_update_companies() hoàn toàn
#       (FIX B2: khi TYPESENSE_ENABLED=false thì return sớm, không chỉ skip kết nối)
# ==============================================================================

import re
import time
import argparse
import unicodedata
import numpy as np
import pandas as pd
import sqlalchemy
import os

# Typesense chỉ import khi được bật
TYPESENSE_ENABLED: bool = os.environ.get("TYPESENSE_ENABLED", "true").lower() == "true"

if TYPESENSE_ENABLED:
    try:
        import typesense
    except ImportError:
        TYPESENSE_ENABLED = False
        print("⚠️  typesense không được cài — TYPESENSE_ENABLED tự động tắt")

from rapidfuzz import fuzz as _fuzz
from tqdm import tqdm
from datetime import datetime, timedelta, date
from lookups import (
    PROVINCE_CANONICAL, GEO_KEYS_SORTED, REGION_MAP,
    NEGOTIABLE_KW,
    NO_EXP_KW,
    LEVEL_MAP, EXP_TO_LEVEL,
    EDUCATION_MAP,
    INDUSTRY_TREE,
    COMPANY_TYPE_PATTERNS, COMPANY_TYPE_STRIP,
    JOB_CATEGORY_MAP, IT_TITLES,
    JOB_TITLE_MAP,
    NON_IT_TITLE_MAP,
    MAJOR_MAP,
    CERT_KW, LANG_CERT_TO_LANG,
    SKILL_MAP,
    WORK_TYPE_MAP, WORK_MODE_MAP,
    ROLE_WORDS, TECH_DOMAIN, ROLE_DOMAIN_TO_TITLE,
)

# ==============================================================================
# 0. CONFIG
# ==============================================================================

# Format: postgresql+psycopg2://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:[YOUR_PASSWORD]@db.[YOUR_PROJECT_REF].supabase.co:5432/postgres"
)

SRC_TABLE   = "jobs"
FACT_TABLE  = "fact_jobs_etl"
LOG_TABLE   = "fact_etl_log"
ERROR_TABLE = "fact_etl_error"

TS_CONFIG = {
    "host":    os.environ.get("TYPESENSE_HOST",    "localhost"),
    "port":    os.environ.get("TYPESENSE_PORT",    "8108"),
    "api_key": os.environ.get("TYPESENSE_API_KEY", "changeme123"),
    "timeout": 3,
}

# ==============================================================================
# 0.5 MODULE-LEVEL CONSTANTS
# ==============================================================================

_BOOL_COLS: frozenset[str] = frozenset({
    "is_it", "is_vn", "is_valid", "is_duplicate",
    "is_negotiable", "is_exp_required",
})

# ==============================================================================
# 0.6 COMPANY NORMALIZE
# ==============================================================================

_COMPANY_TYPE_NORMALIZE = [
    (r'\bTNHH\s+MTV\b',                             'Công ty TNHH MTV'),
    (r'\bCông\s+ty\s+TNHH\s+Một\s+thành\s+viên\b',  'Công ty TNHH MTV'),
    (r'\bCty\s+TNHH\s+MTV\b',                       'Công ty TNHH MTV'),
    (r'\bCty\s+TNHH\b',                             'Công ty TNHH'),
    (r'\bCông\s+ty\s+Trách\s+nhiệm\s+hữu\s+hạn\b',  'Công ty TNHH'),
    (r'\bCTCP\b',                                   'Công ty Cổ phần'),
    (r'\bCty\s+CP\b',                               'Công ty Cổ phần'),
    (r'\bCông\s+ty\s+CP\b',                         'Công ty Cổ phần'),
    (r'\bCty\s+Cổ\s+phần\b',                        'Công ty Cổ phần'),
    (r'\bJSC\b',                                    'Công ty Cổ phần'),
    (r'\bDNTN\b',                                   'Doanh nghiệp Tư nhân'),
    (r'\bDoanh\s+nghiệp\s+TN\b',                    'Doanh nghiệp Tư nhân'),
    (r'\bTĐ\b',                                     'Tập đoàn'),
]

_CONFIDENTIAL_RE = re.compile(
    r"(?:careerlink|vietnamworks|topcv|itviec|linkedin|jobstreet|timviecnhanh)"
    r"['\s]*(?:client|'s\s+client)|confidential\s+(?:company|employer)"
    r"|employer\s+brand|ẩn\s+danh",
    re.IGNORECASE | re.UNICODE,
)


def _normalize_company_name(name: str) -> str:
    result = (name or "").strip()
    for pattern, replacement in _COMPANY_TYPE_NORMALIZE:
        result, n = re.subn(pattern, replacement, result, flags=re.IGNORECASE)
        if n:
            break
    return result


def _clean_strict(text: str) -> str:
    if not text:
        return ""
    s = text.lower().strip()
    s = s.replace('đ', 'd')
    s = ''.join(c for c in unicodedata.normalize('NFKD', s)
                if not unicodedata.combining(c))
    noise_patterns = (
        r'\b(cong ty|cty|tnhh|mtv|co phan|ctcp|jsc|ltd|llc|inc'
        r'|group|tap doan|viet nam|vietnam|vn)\b'
    )
    s = re.sub(noise_patterns, ' ', s)
    s = re.sub(r'[^\w\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _match_search_typesense(ts, name_clean: str,
                             retries: int = 1, delay: float = 0.5) -> list:
    params_infix = {
        'q': name_clean, 'query_by': 'name_official',
        'per_page': 5, 'prefix': 'false', 'sort_by': '_text_match:desc',
    }
    params_prefix = {
        'q': name_clean, 'query_by': 'name_official',
        'per_page': 5, 'prefix': 'true', 'sort_by': '_text_match:desc',
    }
    last_err = None
    for attempt in range(retries):
        try:
            res  = ts.collections['companies'].documents.search(params_infix)
            hits = res.get('hits', [])
            if hits:
                return hits
            res2 = ts.collections['companies'].documents.search(params_prefix)
            return res2.get('hits', [])
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_err


# ==============================================================================
# 2. HELPERS (giữ nguyên toàn bộ từ bản gốc)
# ==============================================================================

def _s(val) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return ""
    return str(val).strip()

# (Các hàm parse_website, _to_date, parse_dates, parse_job_title, parse_compensation,
#  parse_experience, parse_level, parse_education, parse_location, parse_company,
#  parse_industry, parse_skills, parse_work_type... giữ nguyên từ file gốc)
# Bạn paste toàn bộ phần helpers từ etl_supabase.py gốc vào đây
# Mình chỉ sửa class RecruitmentETL bên dưới


# ==============================================================================
# 4. CLASS RecruitmentETL
# ==============================================================================

class RecruitmentETL:

    def __init__(self, db_url: str):
        self.engine = sqlalchemy.create_engine(
            db_url,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        # Khởi tạo Typesense nếu được bật  [FIX B2]
        self._ts = None
        if TYPESENSE_ENABLED:
            try:
                self._ts = typesense.Client({
                    "nodes": [{"host": TS_CONFIG["host"],
                               "port": TS_CONFIG["port"],
                               "protocol": "http"}],
                    "api_key": TS_CONFIG["api_key"],
                    "connection_timeout_seconds": TS_CONFIG["timeout"],
                })
                self._ts.collections['companies'].documents.search(
                    {'q': 'test', 'query_by': 'name_official', 'per_page': 1}
                )
                print("✅ Typesense kết nối OK")
            except Exception as e:
                print(f"⚠️  Typesense không kết nối được: {e} — company match bị tắt")
                self._ts = None

    # --------------------------------------------------------------------------
    # START LOG  [THAY ĐỔI 3]
    # --------------------------------------------------------------------------

    def _start_log(self, mode: str, target_date) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(sqlalchemy.text(f"""
                INSERT INTO {LOG_TABLE}
                    (run_date, mode, target_date, started_at, status)
                VALUES
                    (CURRENT_DATE, :mode, :dt, NOW(), 'RUNNING')
                RETURNING run_id
            """), {"mode": mode, "dt": str(target_date) if target_date else None})
            return result.scalar()

    # --------------------------------------------------------------------------
    # FINISH LOG  [THAY ĐỔI 4]
    # --------------------------------------------------------------------------

    def _finish_log(self, run_id: int, counts: dict,
                    status: str, note: str = None):
        with self.engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"""
                UPDATE {LOG_TABLE} SET
                    finished_at  = NOW(),
                    duration_sec = EXTRACT(EPOCH FROM (NOW() - started_at))::int,
                    total_input  = :inp,
                    total_output = :out,
                    new_rows     = :new,
                    updated_rows = :upd,
                    error_rows   = :err,
                    status       = :status,
                    note         = :note
                WHERE run_id = :rid
            """), {
                "inp":    counts.get("input",   0),
                "out":    counts.get("output",  0),
                "new":    counts.get("new",     0),
                "upd":    counts.get("updated", 0),
                "err":    counts.get("errors",  0),
                "status": status,
                "note":   note,
                "rid":    run_id,
            })

    # --------------------------------------------------------------------------
    # LOAD  [FIX B1 — LEFT(scraped_at,10) thay ::date cast]
    # --------------------------------------------------------------------------

    def _load(self, mode: str, date_str: str | None):
        """
        [FIX B1] scraped_at là VARCHAR — không cast trực tiếp sang ::date
        vì nếu có giá trị không chuẩn sẽ crash cả batch.
        Dùng LEFT(scraped_at, 10) = CURRENT_DATE::text để so sánh an toàn.
        """
        with self.engine.connect() as conn:
            if mode == "all":
                df = pd.read_sql(
                    f"SELECT * FROM {SRC_TABLE} WHERE is_valid = TRUE", conn
                )
                target_date = None
            elif mode == "date" and date_str:
                df = pd.read_sql(
                    f"SELECT * FROM {SRC_TABLE} "
                    f"WHERE is_valid = TRUE "
                    f"AND LEFT(scraped_at, 10) = :dt",
                    conn, params={"dt": date_str}
                )
                target_date = date_str
            else:  # today
                df = pd.read_sql(
                    f"SELECT * FROM {SRC_TABLE} "
                    f"WHERE is_valid = TRUE "
                    f"AND LEFT(scraped_at, 10) = CURRENT_DATE::text",
                    conn
                )
                target_date = date.today()
        return df, target_date

    # --------------------------------------------------------------------------
    # TRANSFORM  (paste từ file gốc — không đổi)
    # --------------------------------------------------------------------------

    def _transform(self, df: pd.DataFrame, run_id: int):
        # Giữ nguyên toàn bộ logic transform từ etl_supabase.py gốc
        # Bạn paste phần _transform() từ file gốc vào đây
        raise NotImplementedError("Paste _transform() từ etl_supabase.py gốc vào đây")

    # --------------------------------------------------------------------------
    # SAVE FACT  [THAY ĐỔI 6 — ON CONFLICT + boolean]
    # --------------------------------------------------------------------------

    def _save_fact(self, df: pd.DataFrame) -> dict:
        # Giữ nguyên từ file gốc — ON CONFLICT đã đúng
        raise NotImplementedError("Paste _save_fact() từ etl_supabase.py gốc vào đây")

    # --------------------------------------------------------------------------
    # SAVE ERRORS
    # --------------------------------------------------------------------------

    def _save_errors(self, error_rows, run_id: int):
        raise NotImplementedError("Paste _save_errors() từ etl_supabase.py gốc vào đây")

    # --------------------------------------------------------------------------
    # MATCH & UPDATE COMPANIES  [FIX B2 — skip hoàn toàn khi Typesense tắt]
    # --------------------------------------------------------------------------

    def _match_and_update_companies(self, run_id: int):
        """
        [FIX B2] Khi TYPESENSE_ENABLED=false HOẶC self._ts=None:
        return ngay lập tức, không cố gắng kết nối.
        Trước đây chỉ check kết nối bên trong vòng lặp → vẫn load DF không cần thiết.
        """
        if not TYPESENSE_ENABLED or self._ts is None:
            print("   ⏭️  Company match bị tắt (TYPESENSE_ENABLED=false)")
            return

        # Paste logic match_and_update_companies từ file gốc vào đây
        raise NotImplementedError("Paste _match_and_update_companies() từ etl_supabase.py gốc vào đây")

    # --------------------------------------------------------------------------
    # DEDUP AND FLAG  [THAY ĐỔI 7]
    # --------------------------------------------------------------------------

    def _dedup_and_flag(self, run_id: int) -> dict:
        # Giữ nguyên từ file gốc — đã dùng TRUE/FALSE đúng
        raise NotImplementedError("Paste _dedup_and_flag() từ etl_supabase.py gốc vào đây")

    # --------------------------------------------------------------------------
    # LOAD DWH  [THAY ĐỔI 8]
    # --------------------------------------------------------------------------

    def _load_dwh(self, mode: str, date_str: str | None):
        p_date = date_str or str(date.today())
        with self.engine.begin() as conn:
            result = conn.execute(sqlalchemy.text(
                "SELECT sp_etl_load_dw(:mode, :dt)"
            ), {"mode": mode, "dt": p_date})
            status_msg = result.scalar()
            print(f"   {status_msg}")
        print("   ✅ DWH loaded.")

    # --------------------------------------------------------------------------
    # RUN  [THAY ĐỔI 9]
    # --------------------------------------------------------------------------

    def run(self, mode: str = "today", date_str: str | None = None):
        print(f"\n{'=' * 62}")
        print(f"  ETL START [{datetime.now():%Y-%m-%d %H:%M:%S}]  mode={mode}")
        print(f"{'=' * 62}")

        print("\n⏳ [1/5] Load...")
        df_raw, target_date = self._load(mode, date_str)
        if df_raw.empty:
            print("   Không có dữ liệu.")
            return None

        run_id = self._start_log(mode, target_date)
        print(f"   run_id={run_id}")

        counts = {"input": len(df_raw), "output": 0,
                  "new": 0, "updated": 0, "errors": 0}
        status = "SUCCESS"

        try:
            print("\n⏳ [2/5] Transform...")
            df_clean, ec     = self._transform(df_raw, run_id)
            counts["output"] = len(df_clean)
            counts["errors"] = len(ec)

            print("\n⏳ [3/5] Save fact...")
            saved             = self._save_fact(df_clean)
            counts["new"]     = saved["new"]
            counts["updated"] = saved["updated"]

            print("\n⏳ [3.5/5] Match company...")
            self._match_and_update_companies(run_id)  # FIX B2: tự skip nếu tắt

            print("\n⏳ [3.8/5] Dedup & flag...")
            dedup           = self._dedup_and_flag(run_id)
            counts["dupes"] = dedup["flagged"]

            print("\n⏳ [4/5] Save errors...")
            self._save_errors(ec, run_id)

            print("\n⏳ [5/5] Load Data Warehouse...")
            self._load_dwh(mode, date_str)

            if counts["input"] > 0 and counts["errors"] / counts["input"] > 0.2:
                status = "WARN"

        except Exception as e:
            status = "FAILED"
            self._finish_log(run_id, counts, status, str(e))
            print(f"\n✗ FAILED: {e}")
            raise

        self._finish_log(run_id, counts, status)
        print(f"\n{'=' * 62}")
        print(f"  DONE [{datetime.now():%Y-%m-%d %H:%M:%S}] {status}")
        print(f"  input={counts['input']} out={counts['output']} "
              f"new={counts['new']} upd={counts['updated']} err={counts['errors']}")
        print(f"{'=' * 62}\n")
        return df_clean


# ==============================================================================
# 5. CLI
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recruitment ETL — Supabase")
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument("--all",  action="store_true")
    grp.add_argument("--date", type=str, metavar="YYYY-MM-DD")
    args = parser.parse_args()

    etl = RecruitmentETL(DATABASE_URL)
    if args.all:
        etl.run("all")
    elif args.date:
        etl.run("date", args.date)
    else:
        etl.run("today")
