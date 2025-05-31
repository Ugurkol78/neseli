from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os
from datetime import datetime
import json
import base64
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")  # .env dosyasına ekleyin

MATCHES_FILE = 'match.json'
USERS_FILE = 'users.json'

# Trendyol API bilgileri
seller_id = os.getenv("SELLER_ID")
api_key = os.getenv("API_KEY")
api_secret = os.getenv("API_SECRET")

# Hepsiburada API bilgileri
hb_username = os.getenv("HB_USERNAME")
hb_password = os.getenv("HB_PASSWORD")
hb_merchant_id = os.getenv("HB_MERCHANT_ID")
hb_user_agent = os.getenv("HB_USER_AGENT")

# Acil durum master şifresi
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "emergency123")

# Kullanıcı dosyasını yükle veya oluştur
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        # İlk kez çalıştırılıyorsa varsayılan kullanıcıları oluştur
        default_users = {
            "admin": {
                "password_hash": generate_password_hash("123456"),
                "role": "admin",
                "created_at": datetime.now().isoformat()
            },
            "user": {
                "password_hash": generate_password_hash("password"),
                "role": "user",
                "created_at": datetime.now().isoformat()
            }
        }
        save_users(default_users)
        return default_users

def save_users(users_data):
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users_data, f, ensure_ascii=False, indent=2)

def add_user(username, password, role="user"):
    """Yeni kullanıcı ekleme fonksiyonu"""
    users = load_users()
    if username in users:
        return False, "Kullanıcı zaten mevcut"
    
    users[username] = {
        "password_hash": generate_password_hash(password),
        "role": role,
        "created_at": datetime.now().isoformat()
    }
    save_users(users)
    return True, "Kullanıcı başarıyla eklendi"

def verify_user(username, password):
    """Kullanıcı doğrulama fonksiyonu"""
    users = load_users()
    
    # Master şifre kontrolü (acil durum için)
    if password == MASTER_PASSWORD:
        return True
    
    # Normal şifre kontrolü
    if username in users:
        return check_password_hash(users[username]["password_hash"], password)
    return False

def get_all_products():
    all_products = []
    page = 0
    size = 100

    while True:
        url = f"https://apigw.trendyol.com/integration/product/sellers/{seller_id}/products?page={page}&size={size}"
        response = requests.get(url, auth=HTTPBasicAuth(api_key, api_secret))

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

def get_hepsiburada_products():
    """Hepsiburada'dan tüm ürünleri çek"""
    all_hb_products = []
    offset = 0
    limit = 50
    
    # Token oluştur
    token = base64.b64encode(f"{hb_username}:{hb_password}".encode()).decode()
    
    # Headers
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "User-Agent": hb_user_agent
    }
    
    while True:
        url = f"https://listing-external.hepsiburada.com/listings/merchantid/{hb_merchant_id}?offset={offset}&limit={limit}"
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            print(f"Hepsiburada API Hatası: {response.status_code} - {response.text}")
            break
            
        data = response.json()
        listings = data.get('listings', [])
        
        if not listings:
            break
            
        all_hb_products.extend(listings)
        
        # Daha fazla sayfa var mı kontrol et
        if len(listings) < limit:
            break
            
        offset += limit
    
    return all_hb_products

def get_hepsiburada_stock_by_sku(merchant_sku):
    """Belirli bir HB ürününün stok bilgisini çek"""
    if not merchant_sku:
        return None
        
    token = base64.b64encode(f"{hb_username}:{hb_password}".encode()).decode()
    
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "User-Agent": hb_user_agent
    }
    
    try:
        url = f"https://listing-external.hepsiburada.com/listings/merchantid/{hb_merchant_id}/sku/{merchant_sku}"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            return data.get('availableStock', 0)
        else:
            print(f"HB stok çekme hatası: {response.status_code}")
            return None
    except Exception as e:
        print(f"HB stok çekme exception: {str(e)}")
        return None

def update_hepsiburada_stock(merchant_sku, quantity):
    """Hepsiburada'da stok güncelle"""
    if not merchant_sku:
        return False, "Merchant SKU bulunamadı"
        
    token = base64.b64encode(f"{hb_username}:{hb_password}".encode()).decode()
    
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": hb_user_agent
    }
    
    # API bir array bekliyor, tek obje yerine array gönder
    payload = [
        {
            "merchantSku": merchant_sku,
            "availableStock": quantity
        }
    ]
    
    try:
        url = f"https://listing-external.hepsiburada.com/listings/merchantid/{hb_merchant_id}/stock-uploads"
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            return True, "Stok başarıyla güncellendi"
        else:
            return False, f"HB stok güncelleme hatası: {response.status_code}"
    except Exception as e:
        return False, f"HB stok güncelleme hatası"

import pandas as pd
from openpyxl import load_workbook, Workbook
import os

def save_products_to_excel(products):
    now = datetime.now()
    formatted_date = now.strftime("%d.%m.%Y %H:%M:%S")
    filename = "stok_raporu.xlsx"
    
    # Her ürün için satır oluştur
    data_rows = []
    for product in products:
        ty_barcode = product.get("barcode", "Barkod yok")
        ty_quantity = product.get("quantity", 0)
        hb_sku = product.get("hb_sku", "-")
        hb_stock = product.get("hb_stock") if product.get("hb_stock") is not None else "-"
        
        data_rows.append({
            'Tarih_Saat': formatted_date,
            'TY_Barkod': ty_barcode,
            'TY_Stok': ty_quantity,
            'HB_SKU': hb_sku,
            'HB_Stok': hb_stock
        })
    
    # DataFrame oluştur
    new_df = pd.DataFrame(data_rows)
    
    try:
        if os.path.exists(filename):
            # Mevcut dosyayı oku
            existing_df = pd.read_excel(filename)
            # Yeni verileri alt alta ekle
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            # İlk kez oluştur
            combined_df = new_df
        
        # Excel'e kaydet
        combined_df.to_excel(filename, index=False, engine='openpyxl')
        print(f"✅ Stok verileri {filename} dosyasına eklendi ({len(new_df)} ürün)")
        
    except Exception as e:
        print(f"❌ Excel kayıt hatası: {str(e)}")
        # Hata durumunda TXT'ye kaydet
        save_products_to_txt_backup(products)
def save_products_to_txt_backup(products):
    """Backup TXT dosyası - Excel çalışmazsa"""
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    formatted_date = now.strftime("%d.%m.%Y %H:%M:%S")
    filename = f"products_stock_backup_{timestamp}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write(f"BACKUP STOK RAPORU\n")
        f.write(f"Oluşturulma Tarihi: {formatted_date}\n")
        f.write("=" * 60 + "\n\n")
        
        for i, product in enumerate(products, start=1):
            ty_barcode = product.get("barcode", "Barkod yok")
            ty_quantity = product.get("quantity", "Stok yok")
            hb_sku = product.get("hb_sku", "-")
            hb_stock = product.get("hb_stock")
            
            if hb_stock is None:
                hb_stock = "-"
            
            f.write(f"{i}\t{ty_barcode}\t\t{ty_quantity}\t{hb_sku}\t\t{hb_stock}\n")

def load_matches():
    if os.path.exists(MATCHES_FILE):
        with open(MATCHES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_matches_to_file(data):
    with open(MATCHES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Login gerekli decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Admin gerekli decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or session.get('role') != 'admin':
            flash('Bu sayfaya erişim yetkiniz yok!', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/debug-session')
@login_required
def debug_session():
    """SECRET_KEY test sayfası"""
    import hashlib
    
    # Session içeriği
    session_data = dict(session)
    
    # SECRET_KEY hash'i (güvenlik için sadece ilk 10 karakter)
    secret_key = app.secret_key
    secret_hash = hashlib.md5(secret_key.encode()).hexdigest()[:10] if secret_key else "YOK"
    
    # Cookie bilgisi
    cookie_info = request.cookies.get('session', 'Cookie bulunamadı')
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><title>SECRET_KEY Test</title></head>
    <body style="font-family: Arial; margin: 40px;">
        <h1>🔐 SECRET_KEY Test Sayfası</h1>
        
        <h3>✅ SECRET_KEY Durumu:</h3>
        <p><strong>SECRET_KEY Hash:</strong> {secret_hash}</p>
        <p><strong>Durum:</strong> {'✅ ÇALIŞIYOR' if secret_key else '❌ YOK'}</p>
        
        <h3>📱 Session Verileri:</h3>
        <pre>{session_data}</pre>
        
        <h3>🍪 Session Cookie:</h3>
        <p style="word-break: break-all; font-size: 12px;">{cookie_info}</p>
        
        <h3>🧪 Test Sonucu:</h3>
        <p style="color: green; font-weight: bold;">
            Eğer bu sayfayı görüyorsanız SECRET_KEY çalışıyor! 🎉
        </p>
        
        <a href="{url_for('index')}">← Ana Sayfaya Dön</a>
    </body>
    </html>
    """
    return html 

from datetime import datetime, timedelta

# Rate limiting için basit cache
login_attempts = {}

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        client_ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
        
        # Rate limiting kontrolü
        now = datetime.now()
        if client_ip in login_attempts:
            last_attempt, count = login_attempts[client_ip]
            if now - last_attempt < timedelta(minutes=15) and count >= 5:
                flash('Çok fazla deneme! 15 dakika bekleyin.', 'error')
                return render_template('login.html')
        
        username = request.form['username']
        password = request.form['password']
        
        if verify_user(username, password):
            # Başarılı giriş - rate limit temizle
            if client_ip in login_attempts:
                del login_attempts[client_ip]
                
            session['logged_in'] = True
            session['username'] = username
            
            # Kullanıcı rolünü de session'a ekle
            users = load_users()
            session['role'] = users[username].get('role', 'user')
            
            flash('Başarıyla giriş yaptınız!', 'success')
            return redirect(url_for('index'))
        else:
            # Başarısız giriş - rate limit artır
            if client_ip in login_attempts:
                last_attempt, count = login_attempts[client_ip]
                login_attempts[client_ip] = (now, count + 1)
            else:
                login_attempts[client_ip] = (now, 1)
                
            flash('Kullanıcı adı veya şifre hatalı!', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash('Başarıyla çıkış yaptınız!', 'success')
    return redirect(url_for('login'))

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        # Validasyon kontrolleri
        if not current_password:
            flash('Mevcut şifrenizi girmelisiniz!', 'error')
            return render_template('profile.html')
        
        if not new_password:
            flash('Yeni şifre boş olamaz!', 'error')
            return render_template('profile.html')
        
        if len(new_password) < 4:
            flash('Yeni şifre en az 4 karakter olmalıdır!', 'error')
            return render_template('profile.html')
        
        if new_password != confirm_password:
            flash('Yeni şifreler eşleşmiyor!', 'error')
            return render_template('profile.html')
        
        # Mevcut şifre kontrolü
        if not verify_user(session['username'], current_password):
            flash('Mevcut şifreniz yanlış!', 'error')
            return render_template('profile.html')
        
        # Şifre güncelleme
        try:
            users = load_users()
            users[session['username']]['password_hash'] = generate_password_hash(new_password)
            users[session['username']]['password_changed_at'] = datetime.now().isoformat()
            save_users(users)
            flash('Şifreniz başarıyla değiştirildi!', 'success')
        except Exception as e:
            flash(f'Şifre değiştirilirken hata oluştu: {str(e)}', 'error')
    
    return render_template('profile.html')

@app.route('/')
@login_required
def index():
    products = get_all_products()
    
    # Eşleşmeleri yükle
    saved_matches = load_matches()
    
    # Hepsiburada ürünlerini çek ve SKU'ya göre dict oluştur
    hb_products = get_hepsiburada_products()
    hb_stock_dict = {}
    for hb_product in hb_products:
        merchant_sku = hb_product.get('merchantSku', '')
        if merchant_sku:
            stock = hb_product.get('availableStock')
            hb_stock_dict[merchant_sku] = stock
    
    # Her ürün için HB bilgilerini ekle
    for product in products:
        ty_barcode = product.get('barcode', '')
        hb_sku = saved_matches.get(ty_barcode, '')
        
        product['hb_sku'] = hb_sku
        product['hb_stock'] = None
        
        # Eğer HB SKU varsa, önce dict'ten bak, yoksa API'den çek
        if hb_sku:
            if hb_sku in hb_stock_dict:
                hb_stock = hb_stock_dict[hb_sku]
            else:
                hb_stock = get_hepsiburada_stock_by_sku(hb_sku)
                
            product['hb_stock'] = hb_stock
    
    # HB bilgileri eklendikten SONRA Excel dosyasına kaydet
    save_products_to_excel(products)
    
    return render_template('index.html', products=products)

@app.route('/match')
@login_required
def match():
    trendyol_products = get_all_products()
    hepsiburada_products = get_hepsiburada_products()
    saved_matches = load_matches()

    # Trendyol ürünlerine eşleşmiş HB SKU'larını ekle
    for product in trendyol_products:
        product['matched_hb_sku'] = saved_matches.get(product['barcode'], '')

    return render_template('match.html', 
                         trendyol_products=trendyol_products,
                         hepsiburada_products=hepsiburada_products)

@app.route('/users')
@admin_required
def users():
    users_data = load_users()
    return render_template('users.html', users=users_data)

@app.route('/add_user', methods=['POST'])
@admin_required
def add_user_route():
    try:
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'user').strip()
        
        # Validasyon kontrolleri
        if not username:
            flash('Kullanıcı adı boş olamaz!', 'error')
            return redirect(url_for('users'))
        
        if not password:
            flash('Şifre boş olamaz!', 'error')
            return redirect(url_for('users'))
        
        if len(password) < 4:
            flash('Şifre en az 4 karakter olmalıdır!', 'error')
            return redirect(url_for('users'))
        
        if role not in ['admin', 'user']:
            flash('Geçersiz rol seçimi!', 'error')
            return redirect(url_for('users'))
        
        # Kullanıcı ekleme
        success, message = add_user(username, password, role)
        if success:
            flash(message, 'success')
        else:
            flash(message, 'error')
        
        return redirect(url_for('users'))
        
    except Exception as e:
        flash(f'Kullanıcı eklenirken hata oluştu: {str(e)}', 'error')
        return redirect(url_for('users'))

@app.route('/reset_password/<username>', methods=['POST'])
@admin_required
def reset_password(username):
    try:
        # Kullanıcı adı doğrulama
        if not username or len(username) > 50 or not username.isalnum():
            flash('Geçersiz kullanıcı adı!', 'error')
            return redirect(url_for('users'))
        
        new_password = request.form.get('new_password', '').strip()
        
        if not new_password:
            flash('Yeni şifre boş olamaz!', 'error')
            return redirect(url_for('users'))
        
        if len(new_password) < 4:
            flash('Şifre en az 4 karakter olmalıdır!', 'error')
            return redirect(url_for('users'))
        
        users = load_users()
        if username in users:
            users[username]['password_hash'] = generate_password_hash(new_password)
            users[username]['password_reset_at'] = datetime.now().isoformat()
            users[username]['reset_by'] = session['username']
            save_users(users)
            flash(f'{username} kullanıcısının şifresi başarıyla sıfırlandı! Yeni şifre: {new_password}', 'success')
        else:
            flash('Kullanıcı bulunamadı!', 'error')
        
        return redirect(url_for('users'))
        
    except Exception as e:
        flash('Şifre sıfırlanırken hata oluştu!', 'error')
        return redirect(url_for('users'))

@app.route('/delete_user/<username>')
@admin_required
def delete_user(username):
    try:
        # Kullanıcı adı doğrulama
        if not username or len(username) > 50 or not username.isalnum():
            flash('Geçersiz kullanıcı adı!', 'error')
            return redirect(url_for('users'))
        
        if username == session['username']:
            flash('Kendi hesabınızı silemezsiniz!', 'error')
            return redirect(url_for('users'))
        
        users = load_users()
        if username in users:
            del users[username]
            save_users(users)
            flash(f'{username} kullanıcısı başarıyla silindi!', 'success')
        else:
            flash('Kullanıcı bulunamadı!', 'error')
        
        return redirect(url_for('users'))
        
    except Exception as e:
        flash('Kullanıcı silinirken hata oluştu!', 'error')
        return redirect(url_for('users'))

@app.route('/save_match', methods=['POST'])
@login_required
def save_match():
    try:
        data = request.get_json()
        if not data or 'matches' not in data:
            return jsonify({'error': 'Geçersiz istek verisi'}), 400

        new_matches = data['matches']
        
        saved_matches = load_matches()

        for trendyol_barcode, matched_sku in new_matches.items():
            saved_matches[trendyol_barcode] = matched_sku.strip()

        save_matches_to_file(saved_matches)

        return jsonify({'message': 'Eşleştirme kaydedildi'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/update_stock', methods=['POST'])
@login_required
def update_stock():
    try:
        data = request.get_json()
        if not data or 'items' not in data:
            return jsonify({'error': 'Geçersiz istek verisi'}), 400

        url = f"https://apigw.trendyol.com/integration/inventory/sellers/{seller_id}/products/price-and-inventory"
        headers = {'Content-Type': 'application/json'}
        
        response = requests.post(
            url,
            auth=HTTPBasicAuth(api_key, api_secret),
            json=data,
            headers=headers
        )

        if response.status_code == 200:
            return jsonify({'message': 'Stok başarıyla güncellendi'})
        else:
            return jsonify({'error': f'Stock update failed: {response.status_code} - {response.text}'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/update_hb_stock', methods=['POST'])
@login_required
def update_hb_stock():
    try:
        data = request.get_json()
        if not data or 'merchant_sku' not in data or 'quantity' not in data:
            return jsonify({'error': 'Geçersiz istek verisi'}), 400

        merchant_sku = data['merchant_sku']
        quantity = data['quantity']
        
        success, message = update_hepsiburada_stock(merchant_sku, quantity)
        
        if success:
            return jsonify({'message': message})
        else:
            return jsonify({'error': message}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

def emergency_reset_admin_password():
    """Acil durum admin şifre sıfırlama - sadece sunucu erişimi olan kişiler için"""
    new_password = input("Admin için yeni şifre girin: ")
    if len(new_password) < 4:
        print("Şifre en az 4 karakter olmalıdır!")
        return
    
    users = load_users()
    if 'admin' in users:
        users['admin']['password_hash'] = generate_password_hash(new_password)
        users['admin']['emergency_reset_at'] = datetime.now().isoformat()
        save_users(users)
        print(f"Admin şifresi başarıyla '{new_password}' olarak değiştirildi!")
    else:
        print("Admin kullanıcısı bulunamadı!")

# Acil durum kullanımı için
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--reset-admin":
        emergency_reset_admin_password()
    else:
        # Production ayarları
        debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
        app.run(debug=debug_mode, host='0.0.0.0')