from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import json
import base64
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from openpyxl import load_workbook, Workbook
import shutil
import logging
import pytz

load_dotenv()

logging.basicConfig(level=logging.INFO)

@app.route('/favicon.ico')
def favicon():
    svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
        <text y=".9em" font-size="90">🌸</text>
    </svg>'''
    
    from flask import Response
    response = Response(svg_content, mimetype='image/svg+xml')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

MATCHES_FILE = 'match.json'
USERS_FILE = 'users.json'
PRODUCTS_CACHE_FILE = 'products_cache.json'

# Excel yönetimi için sabitler
MAX_ROWS_PER_FILE = 500000
MAX_FILE_AGE_DAYS = 60
ARCHIVE_FOLDER = "archives"

# API bilgileri
seller_id = os.getenv("SELLER_ID")
api_key = os.getenv("API_KEY")
api_secret = os.getenv("API_SECRET")

hb_username = os.getenv("HB_USERNAME")
hb_password = os.getenv("HB_PASSWORD")
hb_merchant_id = os.getenv("HB_MERCHANT_ID")
hb_user_agent = os.getenv("HB_USER_AGENT")

MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "emergency123")

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
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

def get_current_turkey_time():
    turkey_tz = pytz.timezone('Europe/Istanbul')
    now = datetime.now(turkey_tz)
    return now.strftime("%d.%m.%Y %H:%M:%S")

def check_hb_batch_status(batch_id):
    """Hepsiburada batch durumunu sorgula"""
    try:
        token = base64.b64encode(f"{hb_username}:{hb_password}".encode()).decode()
        
        headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "User-Agent": hb_user_agent
        }
        
        url = f"https://listing-external.hepsiburada.com/listings/merchantid/{hb_merchant_id}/stock-uploads/id/{batch_id}"
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HB batch status failed: {response.status_code}", "details": response.text}
            
    except Exception as e:
        return {"error": str(e)}

def check_batch_status(batch_id):
    """Trendyol batch durumunu sorgula"""
    try:
        url = f"https://apigw.trendyol.com/integration/product/sellers/{seller_id}/products/batch-requests/{batch_id}"
        
        response = requests.get(
            url,
            auth=HTTPBasicAuth(api_key, api_secret),
            headers={'Accept': 'application/json'},
            timeout=15
        )
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return {"status": "PROCESSING", "message": "Batch henüz işlem sırasında"}
        else:
            return {"error": f"Status code: {response.status_code}", "details": response.text}
            
    except Exception as e:
        return {"error": str(e)}

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
    
    if password == MASTER_PASSWORD:
        return True
    
    if username in users:
        return check_password_hash(users[username]["password_hash"], password)
    return False

def load_products_cache():
    if os.path.exists(PRODUCTS_CACHE_FILE):
        try:
            with open(PRODUCTS_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                products = cache_data.get('products', [])
                turkey_time = cache_data.get('last_updated_turkey', None)
                return products, turkey_time
        except Exception as e:
            logging.error(f"Cache okuma hatası: {e}")
            return [], None
    return [], None

def save_products_cache(products):
    try:
        cache_data = {
            'products': products,
            'last_updated': datetime.now().isoformat(),
            'last_updated_turkey': get_current_turkey_time()
        }
        with open(PRODUCTS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logging.info(f"{len(products)} ürün cache'e kaydedildi")
        return True
    except Exception as e:
        logging.error(f"Cache kayıt hatası: {e}")
        return False

def get_week_info():
    """Haftanın yıl ve hafta numarasını döndür"""
    now = datetime.now()
    year, week, weekday = now.isocalendar()
    return year, week

def get_excel_filename():
    """Haftalık Excel dosya adı oluştur"""
    year, week = get_week_info()
    return f"stok_raporu_{year}_W{week:02d}.xlsx"

def get_week_date_range():
    """Bu haftanın başlangıç ve bitiş tarihlerini döndür"""
    now = datetime.now()
    year, week, weekday = now.isocalendar()
    
    monday = now - timedelta(days=weekday - 1)
    sunday = monday + timedelta(days=6)
    
    return monday.strftime("%d.%m.%Y"), sunday.strftime("%d.%m.%Y")

def get_current_excel_info(filename):
    """Mevcut Excel dosyasının bilgilerini al"""
    if not os.path.exists(filename):
        return 0, None
    
    try:
        df = pd.read_excel(filename)
        row_count = len(df)
        creation_time = datetime.fromtimestamp(os.path.getctime(filename))
        return row_count, creation_time
    except Exception as e:
        logging.warning(f"Excel okuma hatası: {e}")
        return 0, None

def cleanup_old_files():
    """2 aydan eski dosyaları sil"""
    cutoff_date = datetime.now() - timedelta(days=MAX_FILE_AGE_DAYS)
    cleaned_count = 0
    
    for file in os.listdir('.'):
        if file.startswith('stok_raporu_') and file.endswith('.xlsx'):
            try:
                file_time = datetime.fromtimestamp(os.path.getctime(file))
                if file_time < cutoff_date:
                    os.remove(file)
                    cleaned_count += 1
                    logging.info(f"Eski dosya silindi: {file}")
            except Exception as e:
                logging.warning(f"Dosya silme hatası {file}: {e}")
    
    if os.path.exists(ARCHIVE_FOLDER):
        for file in os.listdir(ARCHIVE_FOLDER):
            file_path = os.path.join(ARCHIVE_FOLDER, file)
            if os.path.isfile(file_path):
                try:
                    file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                    if file_time < cutoff_date:
                        os.remove(file_path)
                        cleaned_count += 1
                        logging.info(f"Eski arşiv silindi: {file}")
                except Exception as e:
                    logging.warning(f"Arşiv silme hatası {file}: {e}")
    
    if cleaned_count > 0:
        logging.info(f"Toplam {cleaned_count} eski dosya temizlendi")

def save_products_to_excel_weekly(products):
    """Haftalık Excel kayıt sistemi"""
    now = datetime.now()
    formatted_date = now.strftime("%d.%m.%Y %H:%M:%S")
    filename = get_excel_filename()
    year, week = get_week_info()
    week_start, week_end = get_week_date_range()
    
    data_rows = []
    for product in products:
        ty_barcode = product.get("barcode", "Barkod yok")
        ty_quantity = product.get("quantity", 0)
        hb_sku = product.get("hb_sku", "-")
        hb_stock = product.get("hb_stock") if product.get("hb_stock") is not None else "-"
        
        data_rows.append({
            'Hafta': f"{year}-W{week:02d}",
            'Tarih_Saat': formatted_date,
            'TY_Barkod': ty_barcode,
            'TY_Stok': ty_quantity,
            'HB_SKU': hb_sku,
            'HB_Stok': hb_stock
        })
    
    new_df = pd.DataFrame(data_rows)
    
    try:
        if os.path.exists(filename):
            existing_df = pd.read_excel(filename)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            action = "genişletildi"
        else:
            combined_df = new_df
            action = "oluşturuldu"
        
        combined_df.to_excel(filename, index=False, engine='openpyxl')
        
        total_rows = len(combined_df)
        file_size = os.path.getsize(filename) / (1024 * 1024)
        
        logging.info(f"Haftalık Excel {action}: {filename} - {total_rows:,} satır, {file_size:.1f} MB")
        
        if total_rows > MAX_ROWS_PER_FILE * 0.8:
            remaining = MAX_ROWS_PER_FILE - total_rows
            logging.warning(f"Haftalık dosya dolmak üzere! Kalan: {remaining:,} satır")
        
        if file_size > 50:
            logging.warning(f"Haftalık dosya büyük: {file_size:.1f} MB")
        
        cleanup_old_files()
        
    except Exception as e:
        logging.error(f"Haftalık Excel hatası: {str(e)}")
        save_products_to_txt_backup_weekly(products)

def save_products_to_txt_backup_weekly(products):
    """Haftalık TXT backup sistemi"""
    now = datetime.now()
    year, week = get_week_info()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    formatted_date = now.strftime("%d.%m.%Y %H:%M:%S")
    filename = f"backup_stok_{year}_W{week:02d}_{timestamp}.txt"

    with open(filename, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(f"🔄 HAFTALIK BACKUP STOK RAPORU\n")
        f.write(f"📅 {year} yılı {week}. hafta\n")
        f.write(f"🕐 Oluşturulma: {formatted_date}\n")
        f.write(f"📊 Ürün Sayısı: {len(products)}\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"{'#':<5} {'TY_Barkod':<15} {'TY_Stok':<10} {'HB_SKU':<15} {'HB_Stok':<10}\n")
        f.write("-" * 70 + "\n")
        
        for i, product in enumerate(products, start=1):
            ty_barcode = product.get("barcode", "Barkod yok")[:14]
            ty_quantity = str(product.get("quantity", "0"))
            hb_sku = str(product.get("hb_sku", "-"))[:14]
            hb_stock = str(product.get("hb_stock", "-")) if product.get("hb_stock") is not None else "-"
            
            f.write(f"{i:<5} {ty_barcode:<15} {ty_quantity:<10} {hb_sku:<15} {hb_stock:<10}\n")
    
    logging.info(f"Haftalık backup TXT: {filename}")

def get_excel_stats_weekly():
    """Haftalık Excel durumu hakkında bilgi ver"""
    filename = get_excel_filename()
    year, week = get_week_info()
    week_start, week_end = get_week_date_range()
    
    turkey_tz = pytz.timezone('Europe/Istanbul')
    
    if not os.path.exists(filename):
        return {
            "exists": False,
            "filename": filename,
            "week_info": f"{year} yılı {week}. hafta ({week_start} - {week_end})",
            "message": "Bu hafta henüz Excel raporu oluşturulmamış"
        }
    
    row_count, creation_time = get_current_excel_info(filename)
    file_size = os.path.getsize(filename) / (1024 * 1024)
    
    capacity_used = (row_count / MAX_ROWS_PER_FILE) * 100
    age_hours = (datetime.now() - creation_time).total_seconds() / 3600 if creation_time else 0
    
    updates_this_week = 0
    if row_count > 0:
        try:
            df = pd.read_excel(filename)
            updates_this_week = len(df['Tarih_Saat'].unique()) if 'Tarih_Saat' in df.columns else 0
        except:
            updates_this_week = 0
    
    _, cache_last_updated = load_products_cache()
    turkey_last_updated = cache_last_updated 
    
    
    return {
        "exists": True,
        "filename": filename,
        "week_info": f"{year} yılı {week}. hafta ({week_start} - {week_end})",
        "row_count": row_count,
        "file_size_mb": round(file_size, 2),
        "capacity_used_percent": round(capacity_used, 1),
        "age_hours": round(age_hours, 1),
        "updates_this_week": updates_this_week,
        "creation_time": creation_time.strftime("%d.%m.%Y %H:%M") if creation_time else "Bilinmiyor",
        "last_updated_turkey": turkey_last_updated
    }

def get_all_products():
    all_products = []
    page = 0
    size = 100

    while True:
        url = f"https://apigw.trendyol.com/integration/product/sellers/{seller_id}/products?page={page}&size={size}"
        response = requests.get(url, auth=HTTPBasicAuth(api_key, api_secret))

        if response.status_code != 200:
            logging.error(f"Trendyol API Hatası: {response.status_code} - {response.text}")
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
    
    token = base64.b64encode(f"{hb_username}:{hb_password}".encode()).decode()
    
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "User-Agent": hb_user_agent
    }
    
    while True:
        url = f"https://listing-external.hepsiburada.com/listings/merchantid/{hb_merchant_id}?offset={offset}&limit={limit}"
        response = requests.get(url, headers=headers)
        
        if response.status_code != 200:
            logging.error(f"Hepsiburada API Hatası: {response.status_code} - {response.text}")
            break
            
        data = response.json()
        listings = data.get('listings', [])
        
        if not listings:
            break
            
        all_hb_products.extend(listings)
        
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
            logging.warning(f"HB stok çekme hatası: {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"HB stok çekme exception: {str(e)}")
        return None

def update_hepsiburada_stock(merchant_sku, quantity):
    """Hepsiburada'da stok güncelle"""
    if not merchant_sku:
        return False, "Merchant SKU bulunamadı", None
        
    token = base64.b64encode(f"{hb_username}:{hb_password}".encode()).decode()
    
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": hb_user_agent
    }
    
    payload = [
        {
            "merchantSku": merchant_sku,
            "availableStock": quantity
        }
    ]
    
    try:
        url = f"https://listing-external.hepsiburada.com/listings/merchantid/{hb_merchant_id}/stock-uploads"
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            response_data = response.json()
            batch_id = response_data.get('id')
            
            if batch_id:
                return True, "HB stok güncelleme başlatıldı", batch_id
            else:
                return True, "HB stok güncellendi", None
        else:
            return False, f"HB stok güncelleme hatası: {response.status_code}", None
            
    except Exception as e:
        logging.error(f"HB Exception: {e}")
        return False, f"HB stok güncelleme hatası", None

def load_matches():
    if os.path.exists(MATCHES_FILE):
        with open(MATCHES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_matches_to_file(data):
    with open(MATCHES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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
    
    session_data = dict(session)
    secret_key = app.secret_key
    secret_hash = hashlib.md5(secret_key.encode()).hexdigest()[:10] if secret_key else "YOK"
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

# Rate limiting için basit cache
login_attempts = {}

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        client_ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
        
        now = datetime.now()
        if client_ip in login_attempts:
            last_attempt, count = login_attempts[client_ip]
            if now - last_attempt < timedelta(minutes=15) and count >= 5:
                flash('Çok fazla deneme! 15 dakika bekleyin.', 'error')
                return render_template('login.html')
        
        username = request.form['username']
        password = request.form['password']
        
        if verify_user(username, password):
            if client_ip in login_attempts:
                del login_attempts[client_ip]
                
            session['logged_in'] = True
            session['username'] = username
            
            users = load_users()
            session['role'] = users[username].get('role', 'user')
            
            flash('Başarıyla giriş yaptınız!', 'success')
            return redirect(url_for('index'))
        else:
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
        
        if not verify_user(session['username'], current_password):
            flash('Mevcut şifreniz yanlış!', 'error')
            return render_template('profile.html')
        
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
    products, last_updated = load_products_cache()
    
    if not products:
        products = []
        last_updated = None
    
    return render_template('index.html', 
                         products=products, 
                         last_updated=last_updated)

@app.route('/refresh_data', methods=['POST'])
@login_required
def refresh_data():
    try:
        logging.info("Veri yenileme başlatılıyor...")
        
        products = get_all_products()
        
        if not products:
            return jsonify({'error': 'Trendyol ürünleri alınamadı'}), 500
        
        logging.info(f"{len(products)} Trendyol ürünü alındı")
        
        saved_matches = load_matches()
        
        hb_products = get_hepsiburada_products()
        hb_stock_dict = {}
        
        for hb_product in hb_products:
            merchant_sku = hb_product.get('merchantSku', '')
            if merchant_sku:
                stock = hb_product.get('availableStock')
                hb_stock_dict[merchant_sku] = stock
        
        logging.info(f"{len(hb_products)} Hepsiburada ürünü alındı")
        
        for product in products:
            ty_barcode = product.get('barcode', '')
            hb_sku = saved_matches.get(ty_barcode, '')
            
            product['hb_sku'] = hb_sku
            product['hb_stock'] = None
            
            if hb_sku:
                if hb_sku in hb_stock_dict:
                    hb_stock = hb_stock_dict[hb_sku]
                else:
                    hb_stock = get_hepsiburada_stock_by_sku(hb_sku)
                    
                product['hb_stock'] = hb_stock
        
        if save_products_cache(products):
            save_products_to_excel_weekly(products)
            excel_stats = get_excel_stats_weekly()
            
            return jsonify({
                'message': f'✅ Veriler başarıyla yenilendi! {len(products)} ürün işlendi.',
                'product_count': len(products),
                'last_updated': get_current_turkey_time(),
                'excel_info': excel_stats
            })
        else:
            return jsonify({'error': 'Veriler cache\'e kaydedilemedi'}), 500
            
    except Exception as e:
        logging.error(f"Veri yenileme hatası: {str(e)}")
        return jsonify({'error': f'Veri yenileme hatası: {str(e)}'}), 500

@app.route('/match')
@login_required
def match():
    cached_products, last_updated = load_products_cache()
    
    if not cached_products:
        flash('Önce ana sayfadan "Verileri Yenile" butonuna tıklayarak verileri yükleyin!', 'error')
        return render_template('match.html', 
                             trendyol_products=[],
                             hepsiburada_products=[],
                             cache_empty=True,
                             last_updated=None)
    
    logging.info(f"Cache'den {len(cached_products)} ürün yüklendi")
    
    hepsiburada_products = get_hepsiburada_products()
    
    trendyol_products = []
    for product in cached_products:
        trendyol_products.append({
            'barcode': product.get('barcode', ''),
            'images': product.get('images', [])[:1],
            'title': product.get('title', ''),
        })
    
    logging.info(f"{len(trendyol_products)} TY, {len(hepsiburada_products)} HB ürünü hazırlandı")
    
    saved_matches = load_matches()

    for product in trendyol_products:
        product['matched_hb_sku'] = saved_matches.get(product['barcode'], '')
    
    return render_template('match.html', 
                         trendyol_products=trendyol_products,
                         hepsiburada_products=hepsiburada_products,
                         cache_empty=False,
                         last_updated=last_updated)

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
            headers=headers,
            timeout=30
        )

        if response.status_code == 200:
            response_data = response.json()
            batch_id = response_data.get('batchRequestId')
            
            if batch_id:
                import time
                time.sleep(5)
                
                batch_status = check_batch_status(batch_id)
                
                item_count = batch_status.get('itemCount', 0)
                failed_count = batch_status.get('failedItemCount', 0)
                items = batch_status.get('items', [])
                
                if len(items) > 0:
                    success_items = []
                    failed_items = []
                    
                    for item in items:
                        item_status = item.get('status', 'UNKNOWN')
                        barcode = item.get('requestItem', {}).get('barcode', 'N/A')
                        quantity = item.get('requestItem', {}).get('quantity', 'N/A')
                        reasons = item.get('failureReasons', [])
                        
                        if item_status == 'SUCCESS':
                            success_items.append(f"{barcode} → {quantity}")
                        else:
                            failed_items.append(f"{barcode}: {', '.join(reasons)}")
                    
                    if failed_count == 0:
                        message = f"✅ Stok güncelleme başarılı! {', '.join(success_items)}"
                    else:
                        message = f"⚠️ Kısmi başarı: {len(success_items)} başarılı, {failed_count} hatalı"
                    
                    return jsonify({
                        'message': message,
                        'batch_id': batch_id,
                        'completed': True,
                        'success_items': success_items,
                        'failed_items': failed_items,
                        'item_count': item_count,
                        'failed_count': failed_count
                    })
                else:
                    return jsonify({
                        'message': f"⏳ Stok güncelleme işleniyor... ({item_count} ürün)",
                        'batch_id': batch_id,
                        'completed': False,
                        'item_count': item_count,
                        'failed_count': failed_count,
                        'note': '5-15 dakika içinde tamamlanacak'
                    })
            else:
                return jsonify({
                    'message': 'Stok güncellendi',
                    'api_response': response.text
                })
        else:
            return jsonify({
                'error': f'API hatası: {response.status_code}',
                'details': response.text
            }), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/check_batch/<batch_id>')
@login_required
def check_batch_route(batch_id):
    """Manuel batch durumu sorgulama"""
    batch_status = check_batch_status(batch_id)
    return jsonify({
        'batch_id': batch_id,
        'status': batch_status
    })

@app.route('/update_hb_stock', methods=['POST'])
@login_required
def update_hb_stock():
    try:
        data = request.get_json()
        if not data or 'merchant_sku' not in data or 'quantity' not in data:
            return jsonify({'error': 'Geçersiz istek verisi'}), 400

        merchant_sku = data['merchant_sku']
        quantity = data['quantity']
        
        success, message, batch_id = update_hepsiburada_stock(merchant_sku, quantity)
        
        if success and batch_id:
            import time
            time.sleep(5)
            
            batch_status = check_hb_batch_status(batch_id)
            
            return jsonify({
                'message': f"🛒 HB stok güncelleme başlatıldı",
                'batch_id': batch_id,
                'batch_status': batch_status,
                'merchant_sku': merchant_sku,
                'quantity': quantity,
                'note': 'HB batch işlemi 5-15 dakika sürebilir'
            })
        elif success:
            return jsonify({'message': message})
        else:
            return jsonify({'error': message}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/check_hb_batch/<batch_id>')
@login_required
def check_hb_batch_route(batch_id):
    """HB batch durumu sorgulama"""
    batch_status = check_hb_batch_status(batch_id)
    return jsonify({
        'batch_id': batch_id,
        'status': batch_status
    })

@app.route('/excel_status')
@login_required
def excel_status():
    """Haftalık Excel durumu hakkında bilgi döndür"""
    stats = get_excel_stats_weekly()
    return jsonify(stats)

@app.route('/excel_files')
@login_required
def excel_files():
    """Tüm Excel dosyalarını listele"""
    files = []
    for file in os.listdir('.'):
        if file.startswith('stok_raporu_') and file.endswith('.xlsx'):
            try:
                file_time = datetime.fromtimestamp(os.path.getctime(file))
                file_size = os.path.getsize(file) / (1024 * 1024)
                row_count, _ = get_current_excel_info(file)
                
                files.append({
                    "filename": file,
                    "creation_date": file_time.strftime("%d.%m.%Y"),
                    "size_mb": round(file_size, 2),
                    "row_count": row_count,
                    "age_days": (datetime.now() - file_time).days
                })
            except:
                continue
    
    files.sort(key=lambda x: x["creation_date"], reverse=True)
    return jsonify({"files": files})

def emergency_reset_admin_password():
    """Acil durum admin şifre sıfırlama"""
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

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--reset-admin":
        emergency_reset_admin_password()
    else:
        port = int(os.getenv("PORT", 5002))  # 5001 → 5002
        debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
        
        logging.info(f"Trendyol-HB Stok Yönetimi başlatılıyor...")
        logging.info(f"Tarayıcınızda şu adresi açın: http://localhost:{port}")
        
        app.run(debug=debug_mode, host='0.0.0.0', port=port)