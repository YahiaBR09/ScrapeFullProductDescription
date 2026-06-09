import json

with open('products_full_ten.json') as f:
    data = json.load(f)

for i, p in enumerate(data):
    print(f"{i+1}. {p['name'][:30]}")
    print(f"   price={p['price']}")
    print(f"   regular_price={p.get('regular_price')}")
    print(f"   special_price={p.get('special_price')}")
    print()
