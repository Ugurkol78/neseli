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
import shutil
from datetime import timedelta

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your-secret-key-here")  # .env dosyasına ekleyin

MATCHES_FILE = 'match.json'
USERS_FILE = 'users.json'
PRODUCTS_CACHE_FILE = 'products_cache.json'  # Yeni: Ürün cache dosyası

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

# YENİ: Cache'den ürün verilerini yükle
def load_products_cache():
    """Cache'den ürün verilerini yükle"""
    if os.path.exists(PRODUCTS_CACHE_FILE):
        try:
            with open(PRODUCTS_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                return cache_data.get('products', []), cache_data.get('last_updated', None)
        except Exception as e:
            print(f"Cache okuma hatası: {e}")
            return [], None
    return [], None

# YENİ: Cache'e ürün verilerini kaydet
def save_products_cache(products):
    """Cache'e ürün verilerini kaydet"""
    try:
        cache_data = {
            'products': products,
            'last_updated': datetime.now().isoformat()
        }
        with open(PRODUCTS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"✅ {len(products)} ürün cache'e kaydedildi")
        return True
    except Exception as e:
        print(f"❌ Cache kayıt hatası: {e}")
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


# Excel yönetimi için sabitler
MAX_ROWS_PER_FILE = 500000  # 500k satır limiti (emergency backup)
MAX_FILE_AGE_DAYS = 60      # 2 ay = 60 gün sonra sil
ARCHIVE_FOLDER = "archives"

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
    
    # Haftanın pazartesi gününü bul
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
        print(f"⚠️ Excel okuma hatası: {e}")
        return 0, None

def cleanup_old_files():
    """2 aydan eski dosyaları sil"""
    cutoff_date = datetime.now() - timedelta(days=MAX_FILE_AGE_DAYS)
    cleaned_count = 0
    
    # Ana klasördeki haftalık dosyaları kontrol et
    for file in os.listdir('.'):
        if file.startswith('stok_raporu_') and file.endswith('.xlsx'):
            try:
                file_time = datetime.fromtimestamp(os.path.getctime(file))
                if file_time < cutoff_date:
                    os.remove(file)
                    cleaned_count += 1
                    print(f"🗑️ Eski dosya silindi: {file}")
            except Exception as e:
                print(f"⚠️ Dosya silme hatası {file}: {e}")
    
    # Arşiv klasöründeki dosyaları da temizle
    if os.path.exists(ARCHIVE_FOLDER):
        for file in os.listdir(ARCHIVE_FOLDER):
            file_path = os.path.join(ARCHIVE_FOLDER, file)
            if os.path.isfile(file_path):
                try:
                    file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                    if file_time < cutoff_date:
                        os.remove(file_path)
                        cleaned_count += 1
                        print(f"🗑️ Eski arşiv silindi: {file}")
                except Exception as e:
                    print(f"⚠️ Arşiv silme hatası {file}: {e}")
    
    if cleaned_count > 0:
        print(f"🧹 Toplam {cleaned_count} eski dosya temizlendi (2+ ay önce)")

def save_products_to_excel_weekly(products):
    """Haftalık Excel kayıt sistemi"""
    now = datetime.now()
    formatted_date = now.strftime("%d.%m.%Y %H:%M:%S")
    filename = get_excel_filename()
    year, week = get_week_info()
    week_start, week_end = get_week_date_range()
    
    print(f"📊 Haftalık Excel kaydı: {filename}")
    print(f"📅 {year} yılı {week}. hafta ({week_start} - {week_end})")
    
    # Her ürün için satır oluştur
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
    
    # DataFrame oluştur
    new_df = pd.DataFrame(data_rows)
    
    try:
        if os.path.exists(filename):
            # Bu haftanın dosyasını genişlet
            existing_df = pd.read_excel(filename)
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            action = "genişletildi"
        else:
            # Yeni hafta dosyası oluştur
            combined_df = new_df
            action = "oluşturuldu"
        
        # Excel'e kaydet
        combined_df.to_excel(filename, index=False, engine='openpyxl')
        
        # Dosya bilgilerini yazdır
        total_rows = len(combined_df)
        file_size = os.path.getsize(filename) / (1024 * 1024)  # MB
        
        print(f"✅ Haftalık Excel {action}: {filename}")
        print(f"📊 Bu hafta toplam: {total_rows:,} satır | Dosya boyutu: {file_size:.1f} MB")
        print(f"🆕 Eklenen kayıt: {len(new_df)} ürün")
        
        # Uyarılar
        if total_rows > MAX_ROWS_PER_FILE * 0.8:
            remaining = MAX_ROWS_PER_FILE - total_rows
            print(f"⚠️ Haftalık dosya dolmak üzere! Kalan: {remaining:,} satır")
        
        if file_size > 50:
            print(f"⚠️ Haftalık dosya büyük: {file_size:.1f} MB")
        
        # Eski dosyaları temizle (2+ ay)
        cleanup_old_files()
        
    except Exception as e:
        print(f"❌ Haftalık Excel hatası: {str(e)}")
        # Hata durumunda TXT backup
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
    
    print(f"💾 Haftalık backup TXT: {filename}")

def get_excel_stats_weekly():
    """Haftalık Excel durumu hakkında bilgi ver"""
    filename = get_excel_filename()
    year, week = get_week_info()
    week_start, week_end = get_week_date_range()
    
    if not os.path.exists(filename):
        return {
            "exists": False,
            "filename": filename,
            "week_info": f"{year} yılı {week}. hafta ({week_start} - {week_end})",
            "message": "Bu hafta henüz Excel raporu oluşturulmamış"
        }
    
    row_count, creation_time = get_current_excel_info(filename)
    file_size = os.path.getsize(filename) / (1024 * 1024)
    
    # İstatistikler
    capacity_used = (row_count / MAX_ROWS_PER_FILE) * 100
    age_hours = (datetime.now() - creation_time).total_seconds() / 3600 if creation_time else 0
    
    # Bu hafta kaç kez veri çekilmiş?
    updates_this_week = 0
    if row_count > 0:
        try:
            df = pd.read_excel(filename)
            updates_this_week = len(df['Tarih_Saat'].unique()) if 'Tarih_Saat' in df.columns else 0
        except:
            updates_this_week = 0
    
    return {
        "exists": True,
        "filename": filename,
        "week_info": f"{year} yılı {week}. hafta ({week_start} - {week_end})",
        "row_count": row_count,
        "file_size_mb": round(file_size, 2),
        "capacity_used_percent": round(capacity_used, 1),
        "age_hours": round(age_hours, 1),
        "updates_this_week": updates_this_week,
        "creation_time": creation_time.strftime("%d.%m.%Y %H:%M") if creation_time else "Bilinmiyor"
    }

# /refresh_data route'unu güncelleyin:
@app.route('/refresh_data', methods=['POST'])
@login_required
def refresh_data():
    try:
        print("🔄 Veri yenileme başlatılıyor...")
        
        # Trendyol verilerini çek
        print("📦 Trendyol ürünleri çekiliyor...")
        products = get_all_products()
        
        if not products:
            return jsonify({'error': 'Trendyol ürünleri alınamadı'}), 500
        
        print(f"✅ {len(products)} Trendyol ürünü alındı")
        
        # Eşleşmeleri yükle
        saved_matches = load_matches()
        
        # Hepsiburada ürünlerini çek ve SKU'ya göre dict oluştur
        print("🛒 Hepsiburada ürünleri çekiliyor...")
        hb_products = get_hepsiburada_products()
        hb_stock_dict = {}
        
        for hb_product in hb_products:
            merchant_sku = hb_product.get('merchantSku', '')
            if merchant_sku:
                stock = hb_product.get('availableStock')
                hb_stock_dict[merchant_sku] = stock
        
        print(f"✅ {len(hb_products)} Hepsiburada ürünü alındı")
        
        # Her ürün için HB bilgilerini ekle
        print("🔗 Ürün eşleştirmeleri yapılıyor...")
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
        
        # Verileri cache'e kaydet
        print("💾 Veriler cache'e kaydediliyor...")
        if save_products_cache(products):
            # HB bilgileri eklendikten SONRA HAFTALİK Excel'e kaydet
            print("📊 Haftalık Excel raporu oluşturuluyor...")
            save_products_to_excel_weekly(products)  # ← HAFTALIK SİSTEM
            
            # Excel istatistiklerini al
            excel_stats = get_excel_stats_weekly()
            
            return jsonify({
                'message': f'✅ Veriler başarıyla yenilendi! {len(products)} ürün işlendi.',
                'product_count': len(products),
                'last_updated': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                'excel_info': excel_stats
            })
        else:
            return jsonify({'error': 'Veriler cache\'e kaydedilemedi'}), 500
            
    except Exception as e:
        print(f"❌ Veri yenileme hatası: {str(e)}")
        return jsonify({'error': f'Veri yenileme hatası: {str(e)}'}), 500

# Excel durumu endpoint'ini güncelleyin:

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
    
    # Tarihe göre sırala (en yeni önce)
    files.sort(key=lambda x: x["creation_date"], reverse=True)
    return jsonify({"files": files})      


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

# GÜNCELLENMIŞ: Ana sayfa artık cache'den veri yükler
@app.route('/')
@login_required
def index():
    # Cache'den ürün verilerini yükle
    products, last_updated = load_products_cache()
    
    # Cache boşsa veya veri yoksa
    if not products:
        products = []
        last_updated = None
    
    return render_template('index.html', 
                         products=products, 
                         last_updated=last_updated)

# YENİ: Manuel veri yenileme endpoint'i
@app.route('/refresh_data', methods=['POST'])
@login_required
def refresh_data():
    try:
        print("🔄 Veri yenileme başlatılıyor...")
        
        # Trendyol verilerini çek
        print("📦 Trendyol ürünleri çekiliyor...")
        products = get_all_products()
        
        if not products:
            return jsonify({'error': 'Trendyol ürünleri alınamadı'}), 500
        
        print(f"✅ {len(products)} Trendyol ürünü alındı")
        
        # Eşleşmeleri yükle
        saved_matches = load_matches()
        
        # Hepsiburada ürünlerini çek ve SKU'ya göre dict oluştur
        print("🛒 Hepsiburada ürünleri çekiliyor...")
        hb_products = get_hepsiburada_products()
        hb_stock_dict = {}
        
        for hb_product in hb_products:
            merchant_sku = hb_product.get('merchantSku', '')
            if merchant_sku:
                stock = hb_product.get('availableStock')
                hb_stock_dict[merchant_sku] = stock
        
        print(f"✅ {len(hb_products)} Hepsiburada ürünü alındı")
        
        # Her ürün için HB bilgilerini ekle
        print("🔗 Ürün eşleştirmeleri yapılıyor...")
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
        
        # Verileri cache'e kaydet
        print("💾 Veriler cache'e kaydediliyor...")
        if save_products_cache(products):
            # HB bilgileri eklendikten SONRA Excel dosyasına kaydet
            print("📊 Excel raporu oluşturuluyor...")
            save_products_to_excel(products)
            
            return jsonify({
                'message': f'✅ Veriler başarıyla yenilendi! {len(products)} ürün işlendi.',
                'product_count': len(products),
                'last_updated': datetime.now().strftime('%d.%m.%Y %H:%M:%S')
            })
        else:
            return jsonify({'error': 'Veriler cache\'e kaydedilemedi'}), 500
            
    except Exception as e:
        print(f"❌ Veri yenileme hatası: {str(e)}")
        return jsonify({'error': f'Veri yenileme hatası: {str(e)}'}), 500

# ESKI match route'u bunu bulun ve değiştirin:

@app.route('/match')
@login_required
def match():
    print("🔄 Match sayfası yükleniyor...")
    
    # Cache'den Trendyol verilerini yükle
    cached_products, last_updated = load_products_cache()
    
    if not cached_products:
        print("❌ Cache boş, kullanıcıyı yönlendir")
        flash('Önce ana sayfadan "Verileri Yenile" butonuna tıklayarak verileri yükleyin!', 'error')
        return render_template('match.html', 
                             trendyol_products=[],
                             hepsiburada_products=[],
                             cache_empty=True,
                             last_updated=None)
    
    print(f"✅ Cache'den {len(cached_products)} ürün yüklendi")
    
    # Cache'den Trendyol ürünleri - sadece gerekli alanlar
    trendyol_products = []
    hb_products_set = set()  # Unique HB ürünleri için
    
    for product in cached_products:
        # Trendyol ürünü
        trendyol_products.append({
            'barcode': product.get('barcode', ''),
            'images': product.get('images', [])[:1],  # Sadece ilk resim
            'title': product.get('title', ''),
        })
        
        # HB ürünü varsa ekle
        hb_sku = product.get('hb_sku')
        if hb_sku and hb_sku != '-':
            hb_products_set.add(hb_sku)
    
    # HB ürünlerini basit liste haline getir
    hepsiburada_products = []
    for sku in hb_products_set:
        hepsiburada_products.append({
            'merchantSku': sku,
            'productName': f"Product {sku}",
            'hepsiburadaSku': sku
        })
    
    print(f"✅ {len(trendyol_products)} TY, {len(hepsiburada_products)} HB ürünü hazırlandı")
    
    # Eşleşmeleri yükle
    saved_matches = load_matches()

    # Trendyol ürünlerine eşleşmeleri ekle
    for product in trendyol_products:
        product['matched_hb_sku'] = saved_matches.get(product['barcode'], '')

    print("✅ Match sayfası hazır")
    
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
        # Port 8080 ile çalıştır (macOS AirPlay çakışması için)
        port = int(os.getenv("PORT", 8080))
        debug_mode = os.getenv("FLASK_DEBUG", "False").lower() == "true"
        
        print(f"🚀 Trendyol-HB Stok Yönetimi başlatılıyor...")
        print(f"🌐 Tarayıcınızda şu adresi açın: http://localhost:{port}")
        
        app.run(debug=debug_mode, host='0.0.0.0', port=port)