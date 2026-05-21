"""
ai_processor.py
===============
Dùng Groq (Llama) để xử lý raw_about_job từ LinkedIn.

Setup:
    pip install groq
    # Thêm vào .env:
    GROQ_API_KEY=gsk_...
"""

import os
import json
import re
import time
import logging
from groq import Groq
from dotenv import load_dotenv
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from jobscrapers.pipelines import _clean_nbsp

load_dotenv()
logger = logging.getLogger(__name__)

# ===== CONFIG =====
MODEL           = "llama-3.1-8b-instant"   # Free, nhanh, 14400 req/ngày
MAX_RETRIES     = 3
RETRY_DELAY     = 2
MAX_RAW_CHARS   = 6000
MAX_REQ_PER_MIN = 28   # buffer dưới giới hạn 30 req/phút của Groq

# ─── Prompt: full extraction (description + requirement + metadata) ────────────
SYSTEM_PROMPT_FULL = """
You are a job data extraction assistant. Given raw text from a LinkedIn job posting,
extract and return ONLY a valid JSON object with these exact fields.
Use empty string "" if information is not present.

{
  "job_description" : "ONLY the responsibilities/duties section. What the candidate will DO. Plain text, no bullet symbols.",
  "job_requirement" : "ONLY the requirements/qualifications section. What the candidate MUST HAVE (skills, experience, education). Plain text, no bullet symbols.",
  "compensation"    : "Salary/pay range if explicitly mentioned, INCLUDING the time period. Examples: '100000-120000 USD/year', '20-30 triệu/tháng'. ALWAYS include /year or /month. Empty string if not found.",
  "level"           : "Seniority: Junior / Mid / Senior / Manager / Director / Intern. Empty if unclear.",
  "job_type"        : "Employment type: Full-time / Part-time / Contract / Freelance / Internship. Empty if not mentioned.",
  "work_mode"       : "Work arrangement: On-site / Remote / Hybrid. Empty if not mentioned.",
  "education_level" : "Minimum education degree e.g. 'Bachelor', 'Master'. Empty if not mentioned.",
  "experience"      : "Required years e.g. '2+ years', '3-5 years'. Empty if not mentioned."
}

CRITICAL RULES:
1. Return ONLY raw JSON. No markdown fences, no explanation, no preamble.
2. job_description = responsibilities / duties / what you will do. Do NOT include requirements here.
3. job_requirement = requirements / qualifications / must-have. Do NOT include duties here.
4. If text mixes both sections, use context to separate them.
5. Do not invent information not present in the source.
6. Keep Vietnamese text in Vietnamese. Keep English text in English.
7. Remove bullet symbols (-, •, *, ▪) — plain sentences only.
8. For compensation: /year for USD > 10000, /month for smaller amounts.
""".strip()

# ─── Prompt: metadata only (khi đã có job_description + job_requirement từ regex) ─
SYSTEM_PROMPT_META = """
You are a job metadata extraction assistant. Given raw text from a LinkedIn job posting,
extract ONLY the following fields and return a valid JSON object.
Use empty string "" if information is not present.

{
  "compensation"    : "Salary/pay range if explicitly mentioned. Include /year or /month. Empty if not found.",
  "level"           : "Seniority: Junior / Mid / Senior / Manager / Director / Intern. Empty if unclear.",
  "job_type"        : "Employment type: Full-time / Part-time / Contract / Freelance / Internship. Empty if not mentioned.",
  "work_mode"       : "Work arrangement: On-site / Remote / Hybrid. Empty if not mentioned.",
  "education_level" : "Minimum education degree e.g. 'Bachelor', 'Master'. Empty if not mentioned.",
  "experience"      : "Required years e.g. '2+ years', '3-5 years'. Empty if not mentioned."
}

CRITICAL RULES:
1. Return ONLY raw JSON. No markdown fences, no explanation.
2. Do not invent information.
3. Keep Vietnamese in Vietnamese, English in English.
""".strip()


# ===== KHỞI TẠO GROQ CLIENT =====
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ─── Rate limit state ──────────────────────────────────────────────────────────
_req_count  = 0
_req_window = time.time()


def _rate_limit_wait():
    """Đảm bảo không vượt quá MAX_REQ_PER_MIN request/phút."""
    global _req_count, _req_window

    now     = time.time()
    elapsed = now - _req_window

    if elapsed >= 60:
        _req_count  = 0
        _req_window = now
        return

    if _req_count >= MAX_REQ_PER_MIN:
        wait = 61 - elapsed  # +1s buffer
        logger.info(f"[AI] Rate limit — chờ {wait:.1f}s")
        print(f"    ⏳ Rate limit Groq — chờ {wait:.1f}s...")
        time.sleep(wait)
        _req_count  = 0
        _req_window = time.time()


# ===== LOW-LEVEL API CALL =====

def _call_groq(system_prompt: str, user_text: str) -> dict:
    """
    Gọi Groq API với system prompt cho sẵn.
    Trả về dict parsed từ JSON, hoặc {} nếu thất bại.
    Xử lý rate limit, retry, và JSON parsing tự động.
    """
    global _req_count

    if len(user_text) > MAX_RAW_CHARS:
        user_text = user_text[:MAX_RAW_CHARS] + "\n[truncated]"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _rate_limit_wait()

            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_text},
                ],
                temperature=0.1,
                max_tokens=1024,
            )
            _req_count += 1

            text = response.choices[0].message.content.strip()

            # Strip markdown fences nếu AI vẫn thêm
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)

            # Tìm JSON block trong trường hợp AI thêm text xung quanh
            json_match = re.search(r"\{[\s\S]*\}", text)
            if json_match:
                text = json_match.group(0)

            result = json.loads(text)

            # Đảm bảo tất cả field là string, không phải None
            for k, v in result.items():
                if v is None:
                    result[k] = ""

            logger.debug(f"[AI] OK — fields: {list(result.keys())}")
            return result

        except json.JSONDecodeError as e:
            text_preview = repr(text[:200])
            logger.warning(f"[AI] JSON parse error attempt {attempt}: {e} | text={text_preview}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        except Exception as e:
            err = str(e)
            logger.warning(f"[AI] API error attempt {attempt}: {err}")

            if "429" in err or "rate_limit" in err.lower():
                wait = 62
                logger.info(f"[AI] 429 received — chờ {wait}s")
                print(f"    ⏳ Groq 429 — chờ {wait}s...")
                time.sleep(wait)
                _req_count  = 0
                _req_window = time.time()
            elif "503" in err or "502" in err:
                wait = RETRY_DELAY * attempt * 2
                logger.info(f"[AI] Server error — chờ {wait}s")
                time.sleep(wait)
            elif attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    logger.error("[AI] Tất cả retry thất bại — trả về {}")
    return {}


# ===== PUBLIC API =====

def process_linkedin_item(item: dict) -> dict:
    """
    Full extraction: gọi AI để tách job_description, job_requirement
    và tất cả metadata từ raw_about_job.

    Caller (linkedin_selenium) quyết định có xóa raw_about_job sau khi lưu DB.
    Hàm này KHÔNG tự xóa raw_about_job.
    """
    raw_text = (item.get("raw_about_job") or item.get("job_description") or "").strip()

    enriched = dict(item)

    if not raw_text:
        logger.warning(f"[AI] raw_about_job trống — {item.get('job_title')!r}")
        enriched["job_description"] = ""
        enriched["job_requirement"] = ""
        _fill_meta_defaults(enriched, item, {})
        return enriched

    logger.info(f"[AI] Full extract: {item.get('job_title')!r}")
    ai = _call_groq(SYSTEM_PROMPT_FULL, raw_text)

    if ai:
        logger.info(f"[AI] Full extract OK — {item.get('job_title')!r}")
    else:
        logger.warning(f"[AI] Full extract FAIL — fallback raw_about_job: {item.get('job_title')!r}")

    # job_description / job_requirement: AI trước, fallback raw_text
    enriched["job_description"] = _clean_nbsp(
        (ai.get("job_description") or "").strip() or raw_text
    )
    enriched["job_requirement"] = _clean_nbsp(
        (ai.get("job_requirement") or "").strip()
    )

    _fill_meta_defaults(enriched, item, ai)

    # raw_about_job: giữ nguyên để caller quyết định
    enriched["raw_about_job"] = item.get("raw_about_job")

    return enriched


def extract_metadata_only(raw_text: str) -> dict:
    """
    Chỉ extract metadata (level, experience, compensation, education_level,
    job_type, work_mode) — KHÔNG extract job_description / job_requirement.

    Dùng khi đã có description/requirement từ regex parser.
    Prompt nhỏ hơn → nhanh hơn, tiết kiệm token.

    Trả về dict với 6 field, giá trị là string (rỗng nếu không tìm thấy).
    """
    if not raw_text or not raw_text.strip():
        return {
            "compensation": "", "level": "", "job_type": "",
            "work_mode": "", "education_level": "", "experience": "",
        }

    logger.info("[AI] Metadata-only extract")
    ai = _call_groq(SYSTEM_PROMPT_META, raw_text)

    return {
        "compensation"   : (ai.get("compensation")    or "").strip(),
        "level"          : (ai.get("level")           or "").strip(),
        "job_type"       : (ai.get("job_type")        or "").strip(),
        "work_mode"      : (ai.get("work_mode")       or "").strip(),
        "education_level": (ai.get("education_level") or "").strip(),
        "experience"     : (ai.get("experience")      or "").strip(),
    }


# ===== INTERNAL HELPERS =====

def _fill_meta_defaults(enriched: dict, item: dict, ai: dict) -> None:
    """
    Fill các metadata field theo thứ tự ưu tiên:
      1. Giá trị đã có từ DOM scrape (item gốc) — tin cậy nhất
      2. AI output
      3. Default cứng (compensation → "Thoa thuan")

    Không ghi đè giá trị đã có từ DOM.
    Áp dụng in-place lên enriched.
    """
    def _pick(field: str, default: str = "") -> str:
        dom_val = (item.get(field) or "").strip()
        if dom_val:
            return dom_val                          # DOM win
        ai_val  = (ai.get(field)  or "").strip()
        return ai_val or default

    enriched["compensation"]    = _pick("compensation", default="Thoa thuan")
    enriched["level"]           = _pick("level")
    enriched["job_type"]        = _pick("job_type")
    enriched["work_mode"]       = _pick("work_mode")
    enriched["education_level"] = _pick("education_level")
    enriched["experience"]      = _pick("experience")

    # Các field không liên quan AI — chỉ fill nếu trống
    for f in ("number_recruit", "job_category", "company_size",
              "company_industry", "job_deadline"):
        if not enriched.get(f):
            enriched[f] = ""


# ===== BATCH =====

def process_linkedin_batch(items: list) -> list:
    """
    Xử lý danh sách items tuần tự.
    Rate limiting được handle tự động bên trong _call_groq().
    """
    results = []
    total   = len(items)
    for i, item in enumerate(items, 1):
        logger.info(f"[AI] Batch {i}/{total}: {item.get('job_title')!r}")
        print(f"  🤖 AI [{i}/{total}] {item.get('job_title')!r}")
        results.append(process_linkedin_item(item))
    return results