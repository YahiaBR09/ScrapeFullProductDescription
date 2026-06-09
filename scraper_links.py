import json
import requests
from bs4 import BeautifulSoup

CATEGORY_NAME = "العناية"
CATEGORY_ID = 25713

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    )
}

products = []
seen_urls = set()

page = 1

while True:
    url = f"https://cvaley.com/load-more/{CATEGORY_ID}?page={page}&price=0"

    print(f"Scraping page {page}...")

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:
        print(f"Error on page {page}: {e}")
        break

    html = data.get("html", "")

    if not html.strip():
        print("No HTML returned. Reached end.")
        break

    soup = BeautifulSoup(html, "html.parser")

    page_products = soup.select("div.product-entry")

    if not page_products:
        print("No products found. Reached end.")
        break

    added_this_page = 0

    for product in page_products:

        product_id = (
            product.get("id", "")
            .replace("product-", "")
        )

        link_tag = product.select_one(
            "h3.product-entry__title a[href]"
        )

        if not link_tag:
            continue

        product_url = link_tag["href"].strip()

        if product_url in seen_urls:
            continue

        seen_urls.add(product_url)

        product_name = link_tag.get_text(
            strip=True
        )

        products.append({
            "id": product_id,
            "name": product_name,
            "url": product_url
        })

        added_this_page += 1

    print(
        f"Added {added_this_page} products | "
        f"Total: {len(products)}"
    )

    page += 1

output = {
    "category": CATEGORY_NAME,
    "category_id": CATEGORY_ID,
    "total_products": len(products),
    "products": products
}

with open(
    "products_links.json",
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        output,
        f,
        ensure_ascii=False,
        indent=4
    )

print("\nFinished!")
print(f"Total products saved: {len(products)}")
print("Output: products_links.json")