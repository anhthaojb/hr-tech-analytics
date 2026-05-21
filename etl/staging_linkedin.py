# etl/staging_linkedin.py
import os
import re
import json
import time
import logging
import psycopg2
from groq import Groq
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobscrapers.pipelines import get_db_connection, _clean_nbsp

logger = logging.getLogger(__name__)

# ===== GROQ CONFIG =====
MODEL           = "llama-3.1-8b-instant"
MAX_RETRIES     = 3
RETRY_DELAY     = 2
MAX_RAW_CHARS   = 6000
MAX_REQ_PER_MIN = 28

SYSTEM_PROMPT = """
You are a job data extraction assistant. Given raw text from a LinkedIn job posting,
extract and return ONLY a valid JSON object with these exact fields.
Use empty string "" if information is not present.

{
  "job_description" : "ONLY the responsibilities/duties section. Plain text, no bullet symbols.",
  "job_requirement" : "ONLY the requirements/qualifications section. Plain text, no bullet symbols.",
  "compensation"    : "Salary/pay range if explicitly mentioned. Include /year or /month. Empty if not found.",
  "level"           : "Seniority: Junior / Mid / Senior / Manager / Director / Intern. Empty if unclear.",
  "job_type"        : "Employment type: Full-time / Part-time / Contract / Freelance / Internship. Empty if not mentioned.",
  "work_mode"       : "Work arrangement: On-site / Remote / Hybrid. Empty if not mentioned.",
  "education_level" : "Minimum education degree e.g. 'Bachelor', 'Master'. Empty if not mentioned.",
  "experience"      : "Required years e.g. '2+ years', '3-5 years'. Empty if not mentioned."
}

CRITICAL RULES:
1. Return ONLY raw JSON. No markdown fences, no explanation.
2. Do not invent information not present in the source.
3. Keep Vietnamese text in Vietnamese. Keep English text in English.
4. Remove bullet symbols (-, •, *, ▪) — plain sentences only.
""".strip()

client    = Groq(api_key=os.getenv("GROQ_API_KEY"))
_req_count  = 0
_req_window = time.time()


def _rate_limit_wait():
    global _req_count, _req_window
    now     = time.time()
    elapsed = now - _req_window
    if elapsed >= 60:
        _req_count  = 0
        _req_window = now
        return
    if _req_count >= MAX_REQ_PER_MIN:
        wait = 61 - elapsed
        print(f"    ⏳ Rate limit Groq — chờ {wait:.1f}s...")
        time.sleep(wait)
        _req_count  = 0
        _req_window = time.time()


def _call_groq(raw_text: str) -> dict:
    global _req_count
    if len(raw_text) > MAX_RAW_CHARS:
        raw_text = raw_text[:MAX_RAW_CHARS] + "\n[truncated]"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _rate_limit_wait()
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": raw_text},
                ],
                temperature=0.1,
                max_tokens=1024,
            )
            _req_count += 1
            text = response.choices[0].message.content.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                text = m.group(0)
            result = json.loads(text)
            for k, v in result.items():
                if v is None:
                    result[k] = ""
            return result

        except json.JSONDecodeError:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                print(f"    ⏳ Groq 429 — chờ 62s...")
                time.sleep(62)
                _req_count  = 0
                _req_window = time.time()
            elif attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    return {}


def main():
    conn, cur = get_db_connection()

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
            ai = _call_groq(item["raw_about_job"])

            def _pick(field):
                dom = (item.get(field) or "").strip()
                return dom if dom else (ai.get(field) or "").strip()

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
                _clean_nbsp((ai.get("job_description") or "").strip() or item["raw_about_job"]),
                _clean_nbsp((ai.get("job_requirement") or "").strip()),
                _pick("compensation") or "Thỏa thuận",
                _pick("level"),
                _pick("work_mode"),
                _pick("job_type"),
                ai.get("experience", ""),
                ai.get("education_level", ""),
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