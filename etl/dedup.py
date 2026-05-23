import os
import sys
import time
import argparse
import sqlalchemy
import pandas as pd
from rapidfuzz import fuzz as _rfuzz

# ==============================================================================
# CẤU HÌNH KẾT NỐI
# ==============================================================================
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:[YOUR_PASSWORD]@db.[YOUR_PROJECT_REF].supabase.co:5432/postgres"
)
FACT_TABLE = "fact_jobs_etl"

SOURCE_PRIORITY = {"itviec": 0, "linkedin": 1, "topcv": 2, "vietnamworks": 3}
FUZZY_THRESHOLD = 90

_TECH_IN_TITLE = [
    "java", "python", "golang", " go ", "nodejs", "node.js", "php",
    "react", "angular", "vue", "flutter", "android", "ios", "swift",
    "kotlin", ".net", "c#", "ruby", "scala", "rust",
]


def _title_dedup_key(title_detect, title_clean):
    td = "" if (title_detect is None or isinstance(title_detect, float)) else str(title_detect)
    tc = "" if (title_clean  is None or isinstance(title_clean,  float)) else str(title_clean)
    base = td.strip().lower()
    if not base:
        return tc.strip().lower()
    tech = next((t.strip() for t in _TECH_IN_TITLE if t in tc.lower()), "")
    return f"{base}::{tech}" if tech else base


# ==============================================================================
# LOAD DỮ LIỆU
# ==============================================================================

def _get_run_id_col(engine) -> str:
    """
    Tự động phát hiện cột batch/run trong fact_jobs_etl.
    Ưu tiên: etl_run_id > run_id > batch_id > etl_batch > scraped_at > crawled_date > created_at
    """
    candidates = ["etl_run_id", "run_id", "batch_id", "etl_batch", "scraped_at", "crawled_date", "created_at", "etl_date"]
    with engine.connect() as conn:
        result = conn.execute(sqlalchemy.text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = :tbl
            ORDER BY ordinal_position
        """), {"tbl": FACT_TABLE})
        cols = {row[0] for row in result}
    for c in candidates:
        if c in cols:
            print(f"   ℹ️  Dùng cột '{c}' làm batch key cho daily mode.")
            return c
    raise RuntimeError(
        f"Không tìm thấy cột batch trong {FACT_TABLE}. "
        f"Các cột hiện có: {cols}. "
        f"Hãy set RUN_ID_COL trong config hoặc truyền --run-id-col."
    )


def _load_corpus(engine, run_id_col: str | None = None):
    """Tải toàn bộ kho (is_valid=TRUE) làm nền để so sánh."""
    # Chỉ select run_id_col nếu cần (daily mode)
    extra_col = f", {run_id_col}" if run_id_col else ""
    with engine.connect() as conn:
        df = pd.read_sql(f"""
            SELECT etl_id{extra_col}, website_clean, company_name_clean,
                   job_title_detect, job_title_clean, location_province,
                   salary_min, salary_max
            FROM {FACT_TABLE}
            WHERE is_valid = TRUE
        """, conn)
    return df


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm các cột tính toán phục vụ dedup."""
    df = df.copy()
    df["_co"]       = df["company_name_clean"].fillna("unknown").str.lower().str.strip()
    df["_prov"]     = df["location_province"].fillna("Khác")
    df["_src_rank"] = df["website_clean"].map(lambda x: SOURCE_PRIORITY.get(str(x).lower(), 99))
    df["_info"]     = df["salary_min"].notna().astype(int) + df["salary_max"].notna().astype(int)
    df["_tdk"]      = df.apply(
        lambda r: _title_dedup_key(r["job_title_detect"], r["job_title_clean"] or ""), axis=1
    )
    return df


# ==============================================================================
# THUẬT TOÁN DEDUP (dùng chung cho cả 2 mode)
# ==============================================================================

def _find_duplicates(df_new: pd.DataFrame, df_history: pd.DataFrame) -> list[dict]:
    """
    So sánh df_new với df_history để tìm bản trùng.

    - df_new:     các job cần kiểm tra (mới trong daily, hoặc toàn kho trong full)
    - df_history: toàn bộ kho làm nền (bao gồm cả df_new trong mode full)

    Trả về list[{dup_id, canon_id, method}] — chỉ những dòng trong df_new bị trùng.
    """
    dup_records = []

    # ---- EXACT MATCH ----
    # Xây index tra cứu nhanh từ history (chỉ những dòng có title_detect)
    hist_det = df_history[df_history["job_title_detect"].notna()].copy()
    hist_det["_key"] = hist_det["_co"] + "||" + hist_det["_tdk"] + "||" + hist_det["_prov"]

    # Nhóm history theo key → {key: (etl_id đầu tiên theo ưu tiên)}
    hist_canon: dict[str, int] = {}
    for key, grp in hist_det.groupby("_key", sort=False):
        grp_sorted = grp.sort_values(
            ["etl_id", "_src_rank", "_info"], ascending=[True, True, False]
        )
        hist_canon[key] = int(grp_sorted.iloc[0]["etl_id"])

    new_det = df_new[df_new["job_title_detect"].notna()].copy()
    new_det["_key"] = new_det["_co"] + "||" + new_det["_tdk"] + "||" + new_det["_prov"]

    for _, row in new_det.iterrows():
        canon_id = hist_canon.get(row["_key"])
        if canon_id is not None and canon_id != int(row["etl_id"]):
            dup_records.append({
                "dup_id":   int(row["etl_id"]),
                "canon_id": canon_id,
                "method":   "exact",
            })

    # ---- FUZZY MATCH ----
    # Chỉ áp dụng cho những job không có title_detect
    hist_nod = df_history[df_history["job_title_detect"].isna()].copy()
    new_nod  = df_new[df_new["job_title_detect"].isna()].copy()

    # Đã tìm dup ở bước exact → bỏ qua ở fuzzy
    already_flagged = {r["dup_id"] for r in dup_records}

    for (co, prov), grp_new in new_nod.groupby(["_co", "_prov"], sort=False):
        # Lấy lịch sử cùng nhóm (co + prov)
        grp_hist = hist_nod[
            (hist_nod["_co"] == co) & (hist_nod["_prov"] == prov)
        ].copy()

        # Gộp history + new rồi sắp xếp theo ưu tiên (etl_id nhỏ = cũ hơn = ưu tiên giữ)
        combined = pd.concat([grp_hist, grp_new]).drop_duplicates("etl_id")
        combined = combined.sort_values(
            ["etl_id", "_src_rank", "_info"], ascending=[True, True, False]
        )

        titles = combined["job_title_clean"].fillna("").str.lower().tolist()
        ids    = combined["etl_id"].tolist()
        is_dup = [False] * len(combined)

        for i in range(len(titles)):
            if is_dup[i]:
                continue
            canon_id = int(ids[i])
            for j in range(i + 1, len(titles)):
                if is_dup[j]:
                    continue
                row_id = int(ids[j])
                # Chỉ gắn cờ nếu dòng đó thuộc df_new VÀ chưa bị flagged
                if row_id in df_new["etl_id"].values and row_id not in already_flagged:
                    if _rfuzz.token_sort_ratio(titles[i], titles[j]) >= FUZZY_THRESHOLD:
                        is_dup[j] = True
                        already_flagged.add(row_id)
                        dup_records.append({
                            "dup_id":   row_id,
                            "canon_id": canon_id,
                            "method":   "fuzzy",
                        })

    return dup_records


# ==============================================================================
# BƯỚC 1A — DAILY DEDUP
# Chỉ xét job mới (run_id hôm nay), so sánh với toàn kho
# KHÔNG reset cờ cũ
# ==============================================================================

def run_daily_deduplication(engine, run_id: str, run_id_col: str | None = None):
    # Tự phát hiện cột batch nếu chưa chỉ định
    if run_id_col is None:
        run_id_col = _get_run_id_col(engine)

    print(f"\n📥 [DAILY] Tải kho chung + batch {run_id_col}={run_id}...")
    df_all = _load_corpus(engine, run_id_col=run_id_col)

    if df_all.empty:
        print("🛑 Không có dữ liệu trong kho.")
        return 0

    # Cast về str để tránh lỗi type mismatch (int vs str)
    df_new = df_all[df_all[run_id_col].astype(str) == str(run_id)].copy()
    if df_new.empty:
        print(f"⚠️  Không có job nào với {run_id_col}={run_id}.")
        print(f"   Giá trị mẫu trong cột: {df_all[run_id_col].dropna().unique()[:5].tolist()}")
        return 0

    print(f"📊 Kho: {len(df_all):,} dòng | Mới hôm nay: {len(df_new):,} dòng")

    df_all = _enrich(df_all)
    df_new = df_all[df_all[run_id_col].astype(str) == str(run_id)].copy()  # lấy lại sau enrich

    dup_records = _find_duplicates(df_new=df_new, df_history=df_all)

    n_exact = sum(1 for r in dup_records if r["method"] == "exact")
    n_fuzzy = sum(1 for r in dup_records if r["method"] == "fuzzy")
    print(f"⚡ Phát hiện {len(dup_records):,} bản trùng trong batch hôm nay "
          f"({n_exact} exact | {n_fuzzy} fuzzy).")

    if dup_records:
        print("💾 Ghi cờ trùng lặp (chỉ dòng mới)...")
        with engine.begin() as conn:
            for i in range(0, len(dup_records), 20):
                batch = dup_records[i:i + 20]
                conn.execute(sqlalchemy.text(f"""
                    UPDATE {FACT_TABLE}
                    SET    is_duplicate     = TRUE,
                           duplicate_of_id = :canon_id,
                           dedup_method    = :method
                    WHERE  etl_id = :dup_id
                """), batch)
                time.sleep(0.1)

    return len(dup_records)


# ==============================================================================
# BƯỚC 1B — FULL DEDUP
# Reset toàn bộ kho + quét lại 100%
# ==============================================================================

def run_full_deduplication(engine):
    print("\n[FULL] Tải toàn bộ kho...")
    df_all = _load_corpus(engine)

    if df_all.empty:
        print("Không có dữ liệu trong kho.")
        return 0

    print(f" Đã tải {len(df_all):,} dòng. Tiến hành chuẩn hóa...")
    df_all = _enrich(df_all)

    # Trong full mode: df_new = df_history = toàn kho
    # _find_duplicates sẽ không tự so một dòng với chính nó (canon_id != dup_id)
    dup_records = _find_duplicates(df_new=df_all, df_history=df_all)

    n_exact = sum(1 for r in dup_records if r["method"] == "exact")
    n_fuzzy = sum(1 for r in dup_records if r["method"] == "fuzzy")
    print(f"Phát hiện {len(dup_records):,} bản trùng "
          f"({n_exact} exact | {n_fuzzy} fuzzy).")

    print("Reset trạng thái cũ trên Database...")
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(
            f"UPDATE {FACT_TABLE} "
            f"SET is_duplicate = FALSE, duplicate_of_id = NULL, dedup_method = NULL"
        ))

    if dup_records:
        print("Ghi cờ trùng lặp (toàn kho)...")
        with engine.begin() as conn:
            for i in range(0, len(dup_records), 20):
                batch = dup_records[i:i + 20]
                conn.execute(sqlalchemy.text(f"""
                    UPDATE {FACT_TABLE}
                    SET    is_duplicate     = TRUE,
                           duplicate_of_id = :canon_id,
                           dedup_method    = :method
                    WHERE  etl_id = :dup_id
                """), batch)
                time.sleep(0.1)

    return len(dup_records)




def run_load_dw(engine, run_id: str | None = None):
    scope = f"run_id={run_id}" if run_id else "all"
    print(f"\nKích hoạt Stored Procedure đồng bộ DW (scope={scope})...")
    with engine.begin() as conn:
        result = conn.execute(
            sqlalchemy.text("SELECT sp_etl_load_dw(:scope, :run_id)"),
            {"scope": "daily" if run_id else "all", "run_id": run_id},
        )
        msg = result.scalar()
        print(f"   [SP Message]: {msg}")
    print(" Data Warehouse đã được đồng bộ.")




def main():
    parser = argparse.ArgumentParser(description="Dedup + Load DW")
    parser.add_argument(
        "--mode",
        choices=["daily", "full"],
        default="full",
        help="daily: chỉ dedup batch mới (cần --run-id) | full: reset + quét lại 100%%",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="run_id của batch hôm nay (bắt buộc với --mode daily)",
    )
    parser.add_argument(
        "--run-id-col",
        type=str,
        default=None,
        help="Tên cột batch trong DB (mặc định: tự phát hiện). VD: crawled_date, created_at",
    )
    parser.add_argument(
        "--skip-dw",
        action="store_true",
        help="Bỏ qua bước Load Data Warehouse",
    )
    args = parser.parse_args()

    if args.mode == "daily" and not args.run_id:
        parser.error("--mode daily yêu cầu --run-id <run_id>")

    engine = sqlalchemy.create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
    )

    print(f"\n{'=' * 62}")
    print(f"  DEDUP START [{time.strftime('%Y-%m-%d %H:%M:%S')}]  mode={args.mode}")
    print(f"{'=' * 62}")
    start_time = time.time()

    if args.mode == "daily":
        total_dups = run_daily_deduplication(engine, run_id=args.run_id, run_id_col=args.run_id_col)
    else:
        total_dups = run_full_deduplication(engine)

    if args.skip_dw:
        print("\n⏭️  Bỏ qua bước Load DW (--skip-dw được bật).")
    else:
        run_load_dw(engine, run_id=args.run_id if args.mode == "daily" else None)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 62}")
    print(f"  DEDUP DONE — {total_dups:,} dups flagged | {elapsed:.2f}s")
    print(f"{'=' * 62}\n")


if __name__ == "__main__":
    main()