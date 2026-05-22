# etl/staging_linkedin.py
import os
import sys
import re
import json
import time
import logging
import psycopg2
from groq import Groq
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=BASE_DIR / '.env')

sys.path.insert(0, str(BASE_DIR))
from jobscrapers.pipelines import get_db_connection, _clean_nbsp
logger = logging.getLogger(__name__)

# ===== GROQ CONFIG =====
MODEL           = "llama-3.1-8b-instant"
MAX_RETRIES     = 3
RETRY_DELAY     = 2
MAX_RAW_CHARS   = 6000
MAX_REQ_PER_MIN = 28

SYSTEM_PROMPT = """
You are a job data extraction assistant. Given raw text from a LinkedIn job posting
(may be in English, Vietnamese, or mixed), extract and return ONLY a valid JSON object.
Use empty string "" if information is not present.

{
  "job_description" : "ONLY the responsibilities/duties section (Mô tả công việc / Trách nhiệm). What the candidate will DO. Plain text, no bullet symbols. Separate sentences with newline.",
  "job_requirement" : "ONLY the requirements/qualifications section (Yêu cầu / Kỹ năng). What the candidate MUST HAVE. Include preferred qualifications. Plain text, no bullet symbols. Separate sentences with newline.",
  "compensation"    : "Salary if explicitly mentioned. Keep original format and unit. Examples: '1.47-23 USD/task', '20-30 triệu/tháng', '100000-120000 USD/year', '25-35 USD/hour', '15-20 triệu VND/tháng'. Empty if not found.",
  "salary_type"     : "One of: hourly / monthly / yearly / per_task / negotiable. Vietnamese hints: 'triệu/tháng' → monthly, 'nghìn/giờ' → hourly, 'thỏa thuận' or 'thoả thuận' → negotiable, 'theo dự án' or 'per task' → per_task. Empty if unclear.",
  "level"           : "Seniority level. English: Intern / Fresher / Junior / Mid / Senior / Manager / Director. Vietnamese hints: 'thực tập' → Intern, 'mới ra trường' → Fresher, 'có kinh nghiệm' → Junior or above. Empty if unclear.",
  "job_type"        : "Employment type: Full-time / Part-time / Contract / Freelance. Vietnamese: 'toàn thời gian' → Full-time, 'bán thời gian' → Part-time, 'hợp đồng' → Contract, 'cộng tác viên' → Freelance. 'Contractor' → Contract. Empty if not mentioned.",
  "work_mode"       : "Work arrangement: On-site / Remote / Hybrid. Vietnamese: 'tại văn phòng' → On-site, 'làm từ xa' or 'làm việc từ nhà' → Remote, 'kết hợp' → Hybrid. Empty if not mentioned.",
  "education_level" : "Minimum education. Examples: 'Bachelor', 'Master', 'Cao đẳng', 'Đại học', 'Thạc sĩ'. Empty if not mentioned.",
  "experience"      : "Required years. Examples: '2+ years', '3-5 years', '6 months', '2 năm kinh nghiệm', '6 tháng'. For intern or no experience: '0'. Empty if not mentioned."
}

CRITICAL RULES:
1. Return ONLY raw JSON. No markdown fences, no explanation, no preamble.
2. job_description = responsibilities/duties/what you will do / mô tả công việc / trách nhiệm ONLY. Do NOT include requirements.
3. job_requirement = requirements/qualifications / yêu cầu / kỹ năng / bằng cấp ONLY. Include preferred qualifications / ưu tiên. Do NOT include duties.
4. If sections are mixed, use section headers or context clues to separate them.
5. Do not invent information not present in the source.
6. Keep Vietnamese text in Vietnamese. Keep English text in English. Do NOT translate.
7. Remove ALL bullet symbols (-, •, *, ▪, ·, –) — plain sentences separated by newlines only.
8. For experience with multiple levels (intern/junior/senior), extract the most junior requirement.
9. salary_type: /hour or per hour or /giờ → hourly; /month or /tháng → monthly; /year or /năm → yearly; per task or theo dự án → per_task; thỏa thuận or negotiable or competitive → negotiable.
10. job_type: Never use 'Internship' as job_type — use level field for intern detection instead.
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
               job_description, work_mode, job_type,
               compensation, level
        FROM jobs
        WHERE website = 'linkedin'
          AND ai_processed = FALSE
          AND job_description IS NOT NULL
          AND job_description != ''
        ORDER BY scraped_at DESC
        LIMIT 100
    """)

    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    items = [dict(zip(cols, row)) for row in rows]

    print(f"🤖 Cần xử lý AI: {len(items)} jobs")

    for i, item in enumerate(items, 1):
        print(f"  [{i}/{len(items)}] {item['job_title']}")
        try:
            ai = _call_groq(item["job_description"])

            def _clean_field(field):
                ai_val = (ai.get(field) or "").strip()
                if ai_val:
                    return ai_val
                return (item.get(field) or "").strip()


            job_desc = (ai.get("job_description") or "").strip()
            if not job_desc:
                job_desc = (item.get("job_description") or "").strip()

            job_req = (ai.get("job_requirement") or "").strip()

            # Chuẩn hóa lương mặc định
            compensation_val = _clean_field("compensation")
            if not compensation_val or compensation_val.lower() in ["", "none", "null"]:
                compensation_val = "Thỏa thuận"

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
                _clean_nbsp(job_desc),
                _clean_nbsp(job_req),
                compensation_val,
                _clean_field("level"),
                _clean_field("work_mode"),
                _clean_field("job_type"),
                (ai.get("experience") or "").strip(),
                (ai.get("education_level") or "").strip(),
                item["id"],
            ))
            conn.commit()
        except Exception as e:
            print(f"    Lỗi tại Job ID {item['id']}: {e}")
            conn.rollback()

    cur.close()
    conn.close()
    print("AI processing xong")
if __name__ == "__main__":
    main()