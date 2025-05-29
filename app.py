from flask import Flask, render_template, request, jsonify
from requests.auth import HTTPBasicAuth
import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Trendyol API bilgileri
SELLER_ID = os.getenv("SELLER_ID")
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")

MATCH_FILE = "match.json"

# Tüm ürünleri çek
def get_all_products():
    all_products = []
    page = 0
    size = 100

    while True:
        url = f"https://apigw.trendyol.com/integration/product/sellers/{SELLER_ID}/products?page={page}&size={size}"
        response = requests.get(url, auth=HTTPBasicAuth(API_KEY, API_SECRET))

        if response.status_code != 200:
            print(f"Hata: {response.status_code} - {response.text}")
            break

        data = response.json()
        products = data.get("content", [])
        if not products:
            break

        all_products.extend(products)
        page += 1

    return all_products

# Mevcut eşleşmeleri oku
def load_match_data():
    if os.path.exists(MATCH_FILE):
        with open(MATCH_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

# Eşleşmeleri kaydet
def save_match_data(data):
    with open(MATCH_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Anasayfa: stok güncelleme
@app.route('/')
def index():
    products = get_all_products()
    return render_template('index.html', products=products)

# Ürün eşleştirme sayfası
@app.route('/match')
def match():
    trendyol_products = get_all_products()
    match_data = load_match_data()  # {'trendyol_barcode': 'hb_barcode', ...}

    # Her ürünün matched_hb_barcode bilgisini ekle
    for p in trendyol_products:
        p['matched_hb_barcode'] = match_data.get(p.get('barcode'), '')

    return render_template('match.html', trendyol_products=trendyol_products)

# Eşleştirme kaydetme
@app.route('/save_match', methods=['POST'])
def save_match():
    try:
        data = request.get_json()
        if not data or 'matches' not in data:
            return jsonify({'error': 'Geçersiz veri gönderimi'}), 400

        new_matches = data['matches']
        current_matches = load_match_data()
        current_matches.update(new_matches)
        save_match_data(current_matches)

        return jsonify({'message': 'Eşleştirmeler başarıyla kaydedildi.'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Örnek stok güncelleme route'u
@app.route('/update_stock', methods=['POST'])
def update_stock():
    data = request.get_json()
    updates = data.get("updates", [])

    for item in updates:
        barcode = item.get("barcode")
        quantity = item.get("quantity")
        url = f"https://apigw.trendyol.com/sapigw/suppliers/{SELLER_ID}/products/price-and-inventory"
        payload = [{
            "barcode": barcode,
            "quantity": quantity,
            "stockCode": "default",
            "deliveryDuration": 2
        }]
        headers = {'Content-Type': 'application/json'}

        response = requests.post(url,
                                 auth=HTTPBasicAuth(API_KEY, API_SECRET),
                                 json=payload,
                                 headers=headers)

        if response.status_code != 200:
            return jsonify({"error": f"Stok güncellenemedi: {barcode}"}), 500

    return jsonify({"message": "Stoklar başarıyla güncellendi."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
