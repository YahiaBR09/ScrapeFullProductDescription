"""
scraper_full.py — سحب كامل لجميع المنتجات
============================================
الميزات:
  - 3 متصفحات متوازية → سرعة ~3x
  - استئناف تلقائي: لو توقف البرنامج يكمل من آخر منتج تم سحبه
  - حفظ تلقائي كل 10 منتجات (لا تخسر شيئاً لو انقطع)
  - تدوير User-Agent + تأخير عشوائي لتجنب الحظر
  - إعادة محاولة تلقائية 3 مرات عند الفشل
  - تقرير نهائي بالفاشل والناجح

التثبيت:
  pip install playwright
  playwright install chromium

التشغيل:
  python scraper_full.py
"""

import json
import asyncio
import random
import os
import time
from datetime import datetime
from playwright.async_api import async_playwright

# ============================================================
# CONFIG — عدّل هنا فقط
# ============================================================

INPUT_FILE      = "data/products_links.json"
OUTPUT_FILE     = "results/products_full.json"
PROGRESS_FILE   = "results/scraper_progress.json"   # يتتبع آخر منتج تم سحبه
FAILED_FILE     = "results/scraper_failed.json"     # المنتجات الفاشلة للمراجعة لاحقاً

NUM_BROWSERS    = 3      # عدد المتصفحات المتوازية (لا تزيد عن 4)
SAVE_EVERY      = 10     # احفظ كل N منتج
MAX_RETRIES     = 3      # محاولات إعادة لكل منتج
PAGE_TIMEOUT    = 25000  # ms — مهلة تحميل الصفحة
GALLERY_TIMEOUT = 8000   # ms — مهلة ظهور الصور

# تأخير عشوائي بين كل طلب (ثواني) — يحمي من الحظر
DELAY_MIN = 1.5
DELAY_MAX = 3.5

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ============================================================
# LOAD DATA + RESUME LOGIC
# ============================================================

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    all_products = json.load(f)["products"]

# تحميل النتائج السابقة لو الملف موجود (استئناف)
results = []
done_ids = set()

if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        try:
            results = json.load(f)
            done_ids = {str(r["id"]) for r in results}
            print(f"[RESUME] وُجد {len(done_ids)} منتج مسحوب مسبقاً — سيُكمل من حيث توقف")
        except Exception:
            results = []

# المنتجات المتبقية فقط
products_todo = [p for p in all_products if str(p["id"]) not in done_ids]
total         = len(all_products)
remaining     = len(products_todo)

print(f"[INFO] إجمالي المنتجات : {total}")
print(f"[INFO] تم سحبها مسبقاً : {total - remaining}")
print(f"[INFO] متبقي للسحب    : {remaining}")
print(f"[INFO] المتصفحات المتوازية: {NUM_BROWSERS}")
estimated_min = (remaining * ((DELAY_MIN + DELAY_MAX) / 2)) / NUM_BROWSERS / 60
print(f"[INFO] الوقت المتوقع تقريباً: {estimated_min:.0f} دقيقة\n")

if remaining == 0:
    print("✅ جميع المنتجات مسحوبة مسبقاً!")
    exit(0)

# ============================================================
# SHARED STATE (thread-safe via asyncio.Lock)
# ============================================================

results_lock  = asyncio.Lock()
counter_lock  = asyncio.Lock()
failed_list   = []
completed_count = 0
start_time    = time.time()

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def uid_from_src(src: str) -> str:
    filename = src.split("/")[-1]
    for prefix in ("large-", "small-", "medium-"):
        filename = filename.replace(prefix, "")
    return filename

def to_large_url(src: str) -> str:
    base = "/".join(src.split("/")[:-1])
    return f"{base}/large-{uid_from_src(src)}"

def is_product_image(src: str, product_id: str) -> bool:
    return f"/product/{product_id}/" in src

async def save_results():
    """حفظ النتائج الحالية إلى الملف"""
    async with results_lock:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

def eta_string(done: int, total_todo: int) -> str:
    if done == 0:
        return "..."
    elapsed = time.time() - start_time
    rate    = done / elapsed          # منتجات/ثانية
    left    = (total_todo - done) / rate if rate > 0 else 0
    m, s    = divmod(int(left), 60)
    h, m    = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

# ============================================================
# CORE SCRAPER
# ============================================================

async def scrape_one(page, item: dict) -> dict:
    """سحب بيانات منتج واحد — مع إعادة المحاولة"""
    url        = item["url"]
    product_id = str(item["id"])

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

            # انتظر ظهور gallery — لا تنتظر networkidle (أبطأ)
            try:
                await page.wait_for_selector(".product-gallery-slider", timeout=GALLERY_TIMEOUT)
            except Exception:
                pass  # نكمل حتى لو لم تظهر

            # ── الاسم ──
            name = (await page.text_content("h1") or item.get("name", "")).strip()

            # ── السعر ──
            # حالتان: 1) منتج مع تخفيض (regular + special) 2) منتج بدون تخفيض (price عادي)
            regular_price = None
            special_price = None
            price = None
            
            # محاولة استخراج السعر مع تخفيض
            special_el = await page.query_selector(".special-price")
            if special_el:
                special_price = await special_el.text_content()
                if special_price:
                    special_price = ''.join(c for c in special_price if c.isdigit() or c == '.')
                
                regular_el = await page.query_selector(".regular-price")
                if regular_el:
                    regular_price = await regular_el.text_content()
                    if regular_price:
                        regular_price = ''.join(c for c in regular_price if c.isdigit() or c == '.')
            
            # إذا لم نجد special-price، جرّب السعر العادي
            if not special_price:
                price_el = await page.query_selector(".price.d-flex, .price")
                if price_el:
                    price = await price_el.text_content()
                    if price:
                        price = ''.join(c for c in price if c.isdigit() or c == '.')
            
            # استخدم special_price إذا توفر، وإلا price
            final_price = special_price or price

            # ── الوصف ──
            description = ""
            desc_block  = await page.query_selector("#more-details")
            if desc_block:
                ps    = await desc_block.query_selector_all("p")
                texts = [(await p.text_content() or "").strip() for p in ps]
                description = " ".join(t for t in texts if t)

            # ── SKU ──
            sku_el = await page.query_selector(".sku .details__action span")
            sku    = (await sku_el.text_content()).strip() if sku_el else None

            # ── المبيعات ──
            sales_el = await page.query_selector(".sold .details__action span")
            sales    = (await sales_el.text_content()).strip() if sales_el else "0"

            # ── الصور ──
            images    = []
            seen_uids = set()

            # الصورة الرئيسية
            main_el = await page.query_selector("#pro-img")
            if main_el:
                src = await main_el.get_attribute("data-image") or await main_el.get_attribute("src") or ""
                if "cdn.twsaa.com" in src and is_product_image(src, product_id):
                    uid = uid_from_src(src)
                    if uid not in seen_uids:
                        seen_uids.add(uid)
                        images.append(to_large_url(src))

            # الصور المصغرة — داخل gallery فقط + تخص هذا المنتج
            thumbs = await page.query_selector_all(
                ".product-gallery-slider .thumb-slider img,"
                ".product-gallery-slider .swiper-slide img"
            )
            for img in thumbs:
                src = await img.get_attribute("src") or await img.get_attribute("data-src") or ""
                if not src or "cdn.twsaa.com" not in src:
                    continue
                if not is_product_image(src, product_id):
                    continue
                uid = uid_from_src(src)
                if uid not in seen_uids:
                    seen_uids.add(uid)
                    images.append(to_large_url(src))

            return {
                "id":          item["id"],
                "name":        name,
                "price":       final_price,
                "regular_price": regular_price,
                "special_price": special_price,
                "url":         url,
                "sku":         sku,
                "sales":       sales,
                "description": description,
                "images":      images,
            }

        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = random.uniform(3, 6) * attempt   # تأخير متصاعد
                await asyncio.sleep(wait)
            else:
                return {"error": url, "id": item["id"], "reason": str(e)}

    return {"error": url, "id": item["id"], "reason": "max retries"}

# ============================================================
# WORKER — كل متصفح يشغّل worker مستقل
# ============================================================

async def worker(worker_id: int, queue: asyncio.Queue, browser_context):
    global completed_count

    page = await browser_context.new_page()

    # إخفاء بصمة Playwright
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
        window.chrome = { runtime: {} };
    """)

    while True:
        try:
            idx, item = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        result = await scrape_one(page, item)

        async with results_lock:
            if "error" in result:
                failed_list.append(result)
                status = f"❌ FAIL  [{result.get('reason','')[:40]}]"
            else:
                results.append(result)
                img_n  = len(result.get("images", []))
                status = f"✅ OK    images={img_n}"

        async with counter_lock:
            completed_count += 1
            done_now = completed_count

        # طباعة التقدم
        eta = eta_string(done_now, remaining)
        pct = (done_now / remaining * 100)
        print(
            f"[W{worker_id}] [{done_now:4d}/{remaining}] {pct:5.1f}%  ETA={eta}  "
            f"{status}  {item.get('name','')[:35]}"
        )

        # حفظ دوري
        if done_now % SAVE_EVERY == 0:
            await save_results()
            print(f"  💾 حُفظ {len(results)} منتج")

        queue.task_done()

    await page.close()

# ============================================================
# MAIN
# ============================================================

async def main():
    # بناء قائمة الانتظار
    queue = asyncio.Queue()
    for idx, item in enumerate(products_todo):
        await queue.put((idx, item))

    async with async_playwright() as pw:
        # إنشاء N متصفح — كل واحد بـ user-agent مختلف
        browsers  = []
        contexts  = []
        for i in range(NUM_BROWSERS):
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                    "--no-first-run",
                ]
            )
            context = await browser.new_context(
                user_agent=USER_AGENTS[i % len(USER_AGENTS)],
                locale="ar-SA",
                viewport={"width": 1280 + i * 13, "height": 800 + i * 7},
                extra_http_headers={"Accept-Language": "ar,en-US;q=0.9,en;q=0.8"},
            )
            browsers.append(browser)
            contexts.append(context)

        print(f"🚀 بدأ السحب بـ {NUM_BROWSERS} متصفحات متوازية...\n")

        # تشغيل الـ workers معاً
        tasks = [
            asyncio.create_task(worker(i + 1, queue, contexts[i]))
            for i in range(NUM_BROWSERS)
        ]
        await asyncio.gather(*tasks)

        # إغلاق المتصفحات
        for browser in browsers:
            await browser.close()

    # ── الحفظ النهائي ──
    await save_results()

    if failed_list:
        with open(FAILED_FILE, "w", encoding="utf-8") as f:
            json.dump(failed_list, f, ensure_ascii=False, indent=2)

    elapsed = time.time() - start_time
    m, s    = divmod(int(elapsed), 60)
    h, m    = divmod(m, 60)

    print("\n" + "=" * 50)
    print(f"✅ انتهى في {h:02d}:{m:02d}:{s:02d}")
    print(f"   نجح  : {len(results)}")
    print(f"   فشل  : {len(failed_list)}")
    print(f"   الملف: {OUTPUT_FILE}")
    if failed_list:
        print(f"   الفاشل: {FAILED_FILE}  ← شغّل البرنامج مجدداً لإعادة المحاولة")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())