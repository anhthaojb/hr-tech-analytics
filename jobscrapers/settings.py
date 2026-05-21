# Scrapy settings for jobscrapers project — Supabase version
# THAY ĐỔI DUY NHẤT: SaveToMySQLPipeline → SaveToPostgresPipeline
# (SaveToMySQLPipeline vẫn là alias trong pipelines.py nên cả 2 đều work)

BOT_NAME = "jobscrapers"
SPIDER_MODULES = ["jobscrapers.spiders"]
NEWSPIDER_MODULE = "jobscrapers.spiders"

ADDONS = {}

# =========================================================
#  ScrapeOps — fake user agent
# =========================================================
import os
SCRAPEOPS_API_KEY                    = os.environ.get("SCRAPEOPS_API_KEY", "")
SCRAPEOPS_FAKE_USER_AGENT_ENDPOINT   = True
SCRAPEOPS_NUM_RESULTS                = 50

# =========================================================
#  Crawl mode
# =========================================================
CRAWL_MODE = "daily"

# =========================================================
#  Giới hạn tự động dừng
# =========================================================
CLOSESPIDER_ITEMCOUNT  = 0
CLOSESPIDER_PAGECOUNT  = 0
CLOSESPIDER_TIMEOUT    = 0

# =========================================================
#  Tốc độ & concurrency
# =========================================================
ROBOTSTXT_OBEY               = False
CONCURRENT_REQUESTS          = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY               = 4
RANDOMIZE_DOWNLOAD_DELAY     = True

# =========================================================
#  Retry
# =========================================================
RETRY_ENABLED    = True
RETRY_TIMES      = 3
RETRY_HTTP_CODES = [403, 429, 500, 502, 503, 504]
RETRY_BACKOFF_BASE = 2
DOWNLOAD_TIMEOUT = 30

# =========================================================
#  Headers mặc định
# =========================================================
DEFAULT_REQUEST_HEADERS = {
    "Accept"                   : "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language"          : "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding"          : "gzip, deflate",
    "Connection"               : "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# =========================================================
#  Middlewares
# =========================================================
DOWNLOADER_MIDDLEWARES = {
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    "jobscrapers.middlewares.RotateUserAgentMiddleware"         : 400,
    "jobscrapers.middlewares.JobscrapersDownloaderMiddleware"   : 543,
}

# =========================================================
#  Playwright
# =========================================================
DOWNLOAD_HANDLERS = {
    "http" : "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}
TWISTED_REACTOR          = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
PLAYWRIGHT_BROWSER_TYPE  = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True}

# =========================================================
#  Pipelines  [THAY ĐỔI: SaveToMySQLPipeline → SaveToPostgresPipeline]
#  (SaveToMySQLPipeline vẫn là alias — cả 2 tên đều hoạt động)
# =========================================================
ITEM_PIPELINES = {
    "jobscrapers.pipelines.CleaningPipeline"       : 300,
    "jobscrapers.pipelines.SaveToPostgresPipeline" : 400,
}

# =========================================================
#  Encoding
# =========================================================
FEED_EXPORT_ENCODING = "utf-8"
