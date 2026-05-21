# run_ai_linkedin.py
import os
import psycopg2
from ai_processor import process_linkedin_item
from jobscrapers.pipelines import get_db_connection, clean_dict

def main():
    conn, cur = get_db_connection()

    # Lấy tất cả LinkedIn job chưa AI xử lý
    cur.execute("""
        SELECT id, job_title, company_title, job_url,
               raw_about_job, work_mode, job_type,
               compensation, level
        FROM jobs
        WHERE website = 'linkedin'
          AND ai_processed = FALSE
          AND raw_about_job IS NOT NULL
          AND raw_about_job != ''
        ORDER BY scraped_at DESC
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    items = [dict(zip(cols, row)) for row in rows]

    print(f"🤖 Cần xử lý AI: {len(items)} jobs")

    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] {item['job_title']}")
        try:
            enriched = process_linkedin_item(item)
            cur.execute("""
                UPDATE jobs SET
                    job_description = %s,
                    job_requirement = %s,
                    compensation    = %s,
                    level           = %s,
                    work_mode       = %s,
                    job_type        = %s,
                    experience      = %s,
                    education_level = %s,
                    ai_processed    = TRUE
                WHERE id = %s
            """, (
                enriched.get("job_description"),
                enriched.get("job_requirement"),
                enriched.get("compensation"),
                enriched.get("level"),
                enriched.get("work_mode"),
                enriched.get("job_type"),
                enriched.get("experience"),
                enriched.get("education_level"),
                item["id"],
            ))
            conn.commit()
        except Exception as e:
            print(f"    ❌ Lỗi: {e}")
            conn.rollback()

    cur.close()
    conn.close()
    print("✅ AI processing xong")

if __name__ == "__main__":
    main()