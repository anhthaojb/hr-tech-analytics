"""
itviec.py — Scrapy + Playwright version
========================================
Viết lại từ itviec_selenium.py (undetected-chromedriver) sang Scrapy spider.

THAY ĐỔI CHÍNH:
  - Không cần login / cookie (itviec cho search ẩn danh)
  - Không cần undetected-chromedriver
  - Chạy được trên GitHub Actions CI (headless Playwright)
  - Tích hợp vào pipeline Scrapy bình thường qua run_spiders.py
  - Giữ nguyên KEYWORDS_BY_CATEGORY, _is_old(), _get_section() từ bản gốc
"""

import re
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import quote_plus

import scrapy
from scrapy_playwright.page import PageMethod

from jobscrapers.items import JobItem


class ItviecSpider(scrapy.Spider):
    name = "itviec"
    allowed_domains = ["itviec.com"]

    MAX_JOBS_PER_KEYWORD = 10

    KEYWORDS_BY_CATEGORY = {
        "software_dev": [
            ".NET Developer", "Back End Developer", "Front End Developer",
            "Full Stack Developer", "Java Developer", "NodeJS Developer",
            "PHP Developer", "Python Developer", "Senior Back End Developer",
            "Senior Front End Developer", "Senior Full Stack Developer",
            "C++ Developer", "Embedded Engineer",
        ],
        "mobile": [
            "Android Developer", "iOS Developer", "Mobile Apps Developer",
        ],
        "architecture": [
            "Solution Architect", "System Engineer", "System Administrator",
        ],
        "management": [
            "Business Analysis", "Product Management", "Product Owner",
            "Project Management", "Bridge Project Management",
        ],
        "design_qa": [
            "UX UI Designer", "Tester",
        ],
        "data": [
            "Data Analyst", "Data Scientist", "Data Engineer",
            "Business Intelligence", "Database Administrator",
            "ETL Developer", "Analytics Engineer",
        ],
        "ai_ml": [
            "AI Engineer", "Machine Learning Engineer", "NLP Engineer",
            "Computer Vision Engineer", "MLOps Engineer",
            "Generative AI Engineer", "LLM Engineer",
        ],
    }

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._kw_counts:  dict[str, int] = {}   # keyword → jobs yielded
        self._kw_stopped: set[str]       = set() # keywords đã gặp job cũ

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_mode(self) -> str:
        return self.crawler.settings.get("CRAWL_MODE", "daily")

    @staticmethod
    def _is_old(posted_text: str, max_days: int = 3) -> bool:
        m = re.search(
            r"(\d+)\s+(day|week|month|year)s?\s+ago",
            posted_text or "", re.IGNORECASE,
        )
        if not m:
            return False
        n, unit = int(m.group(1)), m.group(2).lower()
        age = {"day": n, "week": n * 7, "month": n * 30, "year": n * 365}.get(unit, 0)
        return age > max_days

    @staticmethod
    def _get_section(html: str, heading_keywords: list[str]) -> str:
        """
        Dùng BeautifulSoup tìm heading khớp, rồi lấy nội dung block cha.
        Giữ nguyên logic từ get_paragraph_by_heading() trong bản Selenium.
        """
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["h2", "h3"]):
            heading_text = tag.get_text(strip=True).lower()
            if any(kw in heading_text for kw in heading_keywords):
                parent = tag.find_parent(["div", "section"])
                while parent:
                    text = parent.get_text(separator="\n", strip=True)
                    if 50 < len(text) < 8000:
                        return text
                    parent = parent.find_parent(["div", "section"])
        return ""

    # ------------------------------------------------------------------
    # start — một request cho mỗi keyword
    # ------------------------------------------------------------------

    async def start(self):
        for category, keywords in self.KEYWORDS_BY_CATEGORY.items():
            for keyword in keywords:
                self._kw_counts[keyword] = 0
                yield scrapy.Request(
                    url=f"https://itviec.com/it-jobs?query={quote_plus(keyword)}",
                    callback=self.parse_listing,
                    cb_kwargs={"keyword": keyword, "category": category},
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod(
                                "wait_for_selector",
                                "div.job-card",
                                timeout=15000,
                            ),
                        ],
                    },
                )

    # ------------------------------------------------------------------
    # parse_listing — trang danh sách
    # ------------------------------------------------------------------

    def parse_listing(self, response, keyword: str, category: str):
        if keyword in self._kw_stopped:
            return

        mode = self._get_mode()

        for card in response.css(
            "div.job-card[data-search--job-selection-job-slug-value]"
        ):
            # Kiểm tra đã đủ quota chưa
            if self._kw_counts.get(keyword, 0) >= self.MAX_JOBS_PER_KEYWORD:
                self._kw_stopped.add(keyword)
                self.logger.info(
                    f"[itviec] {keyword!r} đủ {self.MAX_JOBS_PER_KEYWORD} jobs — dừng"
                )
                break

            slug = card.attrib.get(
                "data-search--job-selection-job-slug-value", ""
            )
            if not slug:
                continue

            posted_text = card.css(
                "span.small-text.text-dark-grey::text"
            ).get("").strip()

            # Daily mode: dừng keyword nếu gặp job cũ
            if mode == "daily" and self._is_old(posted_text):
                self.logger.info(
                    f"[itviec][daily] Gặp job cũ ({posted_text!r})"
                    f" — dừng keyword {keyword!r}"
                )
                self._kw_stopped.add(keyword)
                break

            # Meta từ card để bổ sung cho detail page
            card_meta = {
                "keyword"      : keyword,
                "category"     : category,
                "job_posted_at": posted_text,
                "salary"       : (
                    card.css("div.salary span.fw-500::text").get("").strip()
                    or card.css("div.salary::text").get("").strip()
                ),
                "work_mode"    : card.css(
                    "div.text-rich-grey.flex-shrink-0::text"
                ).get("").strip(),
                "location"     : (
                    card.attrib.get("title", "")
                    or card.css(
                        "div.text-rich-grey.text-truncate.text-nowrap::text"
                    ).get("").strip()
                ),
                "job_expertise": (
                    card.css(
                        "a.text-decoration-dot-underline.small-text::attr(title)"
                    ).get("")
                    or card.css(
                        "a.text-decoration-dot-underline.small-text::text"
                    ).get("").strip()
                ),
                "skills"       : card.css(
                    'a[data-responsive-tag-list-target="tag"]::text'
                ).getall(),
            }

            yield scrapy.Request(
                url=f"https://itviec.com/it-jobs/{slug}",
                callback=self.parse_job,
                meta={
                    "playwright": True,
                    "playwright_page_methods": [
                        PageMethod(
                            "wait_for_selector",
                            "h1.text-it-black",
                            timeout=10000,
                        ),
                    ],
                    "card_meta": card_meta,
                    "keyword"  : keyword,
                },
            )

        # Phân trang — chỉ tiếp tục nếu keyword chưa bị dừng
        if keyword not in self._kw_stopped:
            next_href = response.css("div.page.next a::attr(href)").get()
            if next_href:
                yield scrapy.Request(
                    url=response.urljoin(next_href),
                    callback=self.parse_listing,
                    cb_kwargs={"keyword": keyword, "category": category},
                    meta={
                        "playwright": True,
                        "playwright_page_methods": [
                            PageMethod(
                                "wait_for_selector",
                                "div.job-card",
                                timeout=15000,
                            ),
                        ],
                    },
                )

    # ------------------------------------------------------------------
    # parse_job — trang chi tiết
    # ------------------------------------------------------------------

    def parse_job(self, response):
        meta      = response.meta.get("card_meta", {})
        keyword   = response.meta.get("keyword", "")

        job_title = response.css("h1.text-it-black::text").get("").strip()
        if not job_title:
            self.logger.warning(f"[itviec] Không tìm thấy job_title — bỏ qua {response.url}")
            return

        # ── Company ───────────────────────────────────────────────────
        company_title = (
            response.css("div.employer-name::text").get("").strip()
            or response.css(
                "div.job-header-info div.employer-name::text"
            ).get("").strip()
        )

        # ── Compensation ──────────────────────────────────────────────
        compensation = (
            meta.get("salary", "")
            or response.css("div.salary span.fw-500::text").get("").strip()
            or response.css("div.salary::text").get("").strip()
        ) or None

        # ── Location ──────────────────────────────────────────────────
        location = (
            meta.get("location", "")
            or response.css(
                "div.d-flex.flex-column.gap-2 > div:first-child span.normal-text::text"
            ).get("").strip()
            or response.css(
                "div.job-show-info span.normal-text.text-rich-grey::text"
            ).get("").strip()
        ) or None

        # ── Work mode & posted_at từ preview-header-item ──────────────
        work_mode     = meta.get("work_mode", "")
        job_posted_at = meta.get("job_posted_at", "")

        preview_items = response.css("div.preview-header-item")
        if not work_mode and preview_items:
            work_mode = preview_items[0].css(
                "span.normal-text::text"
            ).get("").strip()
        if not job_posted_at and len(preview_items) >= 2:
            job_posted_at = preview_items[1].css(
                "span.normal-text::text"
            ).get("").strip()

        # ── Skills & category ─────────────────────────────────────────
        skills = response.css("a.itag.itag-light.itag-sm::text").getall()
        domain = response.css("div.itag.bg-light-grey.itag-sm::text").getall()

        expertise    = meta.get("job_expertise", "")
        job_category = (
            [expertise] if expertise
            else skills or meta.get("skills", [])
        ) or None

        # ── Level từ job title ────────────────────────────────────────
        level       = None
        title_lower = job_title.lower()
        for kw in [
            "fresher", "junior", "mid", "senior", "lead",
            "manager", "director", "principal", "intern", "staff", "associate",
        ]:
            if kw in title_lower:
                level = kw.capitalize()
                break

        # ── Company info từ employer section ─────────────────────────
        company_size     = ""
        company_industry = ", ".join(domain) if domain else ""

        for row in response.css("section.job-show-employer-info div.row"):
            cols = row.css("div.col")
            if len(cols) < 2:
                continue
            label = cols[0].css("::text").get("").lower()
            value = " ".join(cols[1].css("::text").getall()).strip()
            if "company size" in label:
                company_size = value
            elif not company_industry and "industry" in label:
                company_industry = value

        # ── Job description & requirement (dùng BS4 như bản gốc) ─────
        html            = response.text
        job_description = self._get_section(html, [
            "job description", "mô tả công việc",
        ])
        job_requirement = self._get_section(html, [
            "your skills and experience",
            "skills and experience",
            "yêu cầu công việc",
        ])

        # Fallback nếu không tìm được section
        if not job_description:
            job_description = response.css(
                "section.job-content::text, div.job-content::text"
            ).get("").strip()

        # ── Experience từ requirement text ────────────────────────────
        experience = None
        if job_requirement:
            m = re.search(
                r"(\d+)\+?\s*(?:năm|year)s?\s*(?:of\s*)?(?:kinh nghiệm|experience)",
                job_requirement, re.IGNORECASE,
            )
            if m:
                experience = m.group(0)

        # ── Cập nhật đếm keyword ──────────────────────────────────────
        self._kw_counts[keyword] = self._kw_counts.get(keyword, 0) + 1

        item = JobItem()
        item["website"]          = "itviec"
        item["job_url"]          = response.url
        item["job_title"]        = job_title
        item["company_title"]    = company_title or None
        item["location"]         = location
        item["experience"]       = experience
        item["compensation"]     = compensation
        item["job_type"]         = "Full-time"
        item["work_mode"]        = work_mode or None
        item["level"]            = level
        item["company_size"]     = company_size or None
        item["company_industry"] = company_industry or None
        item["job_category"]     = job_category
        item["number_recruit"]   = None
        item["education_level"]  = None
        item["job_description"]  = job_description or None
        item["job_requirement"]  = job_requirement or None
        item["job_posted_at"]    = job_posted_at or None
        item["job_deadline"]     = None
        item["scraped_at"]       = datetime.now()
        item["skills"]           = skills

        yield item