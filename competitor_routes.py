"""
Rakip Fiyat Takip Modülü - Route Yönetimi
Trendyol ürünleri için rakip fiyat analizi ve takip sistemi
YENİ: Slot 0 (NeşeliÇiçekler) desteği eklendi
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from functools import wraps
import logging

# Competitor modül importları
from competitor_tracking import (
    init_competitor_database, check_tables_exist,
    get_links_by_barcode, save_links, validate_trendyol_links,
    get_active_prices_by_barcode, get_competitor_settings,
    save_competitor_settings, get_last_update,
    get_total_links_count, get_total_prices_count,
    get_db_connection
)
from competitor_scraper import (
    start_scraping_for_new_links, start_manual_update,
    get_update_status, is_scraping_running
)
from competitor_scheduler import (
    get_scheduler_status, update_scheduler
)

# Blueprint tanımı
competitor_bp = Blueprint('competitor', __name__)

def login_required(f):
    """Login kontrolü decorator fonksiyonu"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@competitor_bp.route('/competitors')
@login_required
def competitors():
    """
    Ana rakip takip sayfası
    Trendyol ürünlerini ve rakip fiyatları gösterir
    """
    try:
        # Veritabanı kontrolü
        if not check_tables_exist():
            init_competitor_database()
        
        # Cache'den ürünleri çek (mevcut app.py'den)
        from app import load_products_cache
        products, last_cache_update = load_products_cache()
        
        if not products:
            flash('Önce ana sayfadan "Verileri Yenile" butonuna tıklayarak Trendyol verilerini yükleyin!', 'error')
            return redirect(url_for('index'))
        
        # Her ürün için rakip fiyat verilerini hazırla (sadece slot 1-5)
        competitor_data = {}
        for product in products:
            barcode = product.get('barcode', '')
            if barcode:
                # Bu barkod için aktif fiyatları al (sadece slot 1-5, slot 0 hariç)
                prices = get_active_prices_by_barcode(barcode, exclude_slot_0=True)
                competitor_data[barcode] = prices
        
        # Son güncelleme zamanını çek
        last_update = get_last_update()
        
        return render_template('competitors.html', 
                             products=products,
                             competitor_data=competitor_data,
                             last_update=last_update,
                             cache_empty=False)
                             
    except Exception as e:
        logging.error(f"Competitor ana sayfa hatası: {str(e)}")
        flash(f'Sayfa yüklenirken hata oluştu: {str(e)}', 'error')
        return redirect(url_for('index'))

@competitor_bp.route('/competitors/links/<barcode>')
@login_required
def get_competitor_links(barcode):
    """
    Belirli bir barkod için kayıtlı rakip linklerini getirir
    YENİ: Slot 0 (NeşeliÇiçekler) da dahil edilir
    """
    try:
        # Tüm linkler (slot 0-5)
        all_links = get_links_by_barcode(barcode, include_slot_0=True)
        
        # Frontend için format: slot numarası ve URL
        links_response = []
        for link in all_links:
            links_response.append({
                'slot_number': link['slot_number'],
                'url': link['url'],
                'is_active': link['is_active'],
                'created_at': link['created_at'],
                'updated_at': link['updated_at']
            })
        
        return jsonify({
            'success': True,
            'links': links_response,
            'barcode': barcode
        })
        
    except Exception as e:
        logging.error(f"Link getirme hatası - {barcode}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@competitor_bp.route('/competitors/links/<barcode>', methods=['POST'])
@login_required
def save_competitor_links(barcode):
    """
    Rakip linklerini kaydeder
    YENİ: Slot 0 (NeşeliÇiçekler) desteği eklendi + DEBUG LOGGING
    """
    try:
        data = request.get_json()
        links = data.get('links', [])
        include_slot_0 = data.get('include_slot_0', False)
        username = session.get('username')
        
        logging.info(f"🔧 DEBUG: Link kaydetme başlatıldı - Barkod: {barcode}")
        logging.info(f"🔧 DEBUG: Gelen linkler: {links}")
        logging.info(f"🔧 DEBUG: include_slot_0: {include_slot_0}")
        
        # YENİ: Slot mapping sistemi
        # links[0] = slot 0 (NeşeliÇiçekler)
        # links[1-5] = slot 1-5 (Rakipler)
        
        if include_slot_0:
            # Yeni sistem: slot 0 dahil
            slot_links = {}
            for i, link_url in enumerate(links):
                slot_number = i  # Index 0 = slot 0, index 1 = slot 1, vs.
                if link_url and link_url.strip():
                    slot_links[slot_number] = link_url.strip()
        else:
            # Eski sistem: sadece slot 1-5
            slot_links = {}
            for i, link_url in enumerate(links):
                slot_number = i + 1  # Index 0 = slot 1, index 1 = slot 2, vs.
                if link_url and link_url.strip():
                    slot_links[slot_number] = link_url.strip()
        
        logging.info(f"🔧 DEBUG: Slot mapping sonucu: {slot_links}")
        
        # Link validasyonu
        non_empty_links = [url for url in slot_links.values() if url]
        is_valid, invalid_links = validate_trendyol_links(non_empty_links)
        
        if not is_valid:
            logging.error(f"🔧 DEBUG: Geçersiz linkler: {invalid_links}")
            return jsonify({
                'success': False,
                'error': f'Geçersiz Trendyol linki: {", ".join(invalid_links)}'
            }), 400
        
        logging.info(f"🔧 DEBUG: Link validasyonu başarılı")
        
        # Linkleri kaydet (güncellenmiş save_links fonksiyonu)
        logging.info(f"🔧 DEBUG: save_links_with_slots çağrılıyor...")
        save_result = save_links_with_slots(barcode, slot_links, username, include_slot_0)
        
        if save_result:
            logging.info(f"🔧 DEBUG: Linkler kaydedildi, scraping başlatılıyor...")
            
            # Yeni linkler için scraping başlat (slot 0 dahil)
            try:
                start_scraping_for_new_links_with_slots(barcode, slot_links, username)
                logging.info(f"🔧 DEBUG: Scraping thread başlatıldı!")
            except Exception as scrape_error:
                logging.error(f"🔧 DEBUG: Scraping başlatma hatası: {str(scrape_error)}")
            
            slot_count = len([url for url in slot_links.values() if url])
            slot_info = "NeşeliÇiçekler dahil " if include_slot_0 else ""
            
            return jsonify({
                'success': True,
                'message': f'Linkler başarıyla kaydedildi ({slot_info}{slot_count} link) ve fiyat güncelleme başlatıldı!'
            })
        else:
            logging.error(f"🔧 DEBUG: Link kaydetme başarısız!")
            return jsonify({
                'success': False,
                'error': 'Linkler kaydedilirken hata oluştu!'
            }), 500
            
    except Exception as e:
        logging.error(f"🔧 DEBUG: Link kaydetme exception: {str(e)}")
        logging.error(f"Link kaydetme hatası - {barcode}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@competitor_bp.route('/competitors/prices/<barcode>')
@login_required
def get_competitor_prices(barcode):
    """
    Belirli bir barkod için aktif rakip fiyatlarını getirir
    GÜNCELLEME: Slot 0 hariç tutulur (sadece rakip fiyatlar)
    """
    try:
        # Sadece slot 1-5 fiyatları (slot 0 hariç)
        prices = get_active_prices_by_barcode(barcode, exclude_slot_0=True)
        
        return jsonify({
            'success': True,
            'prices': prices,
            'barcode': barcode
        })
        
    except Exception as e:
        logging.error(f"Fiyat getirme hatası - {barcode}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@competitor_bp.route('/competitors/update/manual', methods=['POST'])
@login_required
def manual_update():
    """
    Manuel güncelleme başlatır
    YENİ: Slot 0 (NeşeliÇiçekler) da dahil edilir
    """
    try:
        username = session.get('username')
        
        # Eğer başka bir scraping devam ediyorsa uyarı ver
        if is_scraping_running():
            return jsonify({
                'success': False,
                'error': 'Başka bir güncelleme işlemi devam ediyor. Lütfen bekleyin.'
            }), 400
        
        # Manuel güncelleme başlat (slot 0 dahil)
        success = start_manual_update_with_slot_0(username)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Manuel güncelleme başlatıldı (NeşeliÇiçekler dahil)!'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Güncelleme başlatılamadı!'
            }), 500
        
    except Exception as e:
        logging.error(f"Manuel güncelleme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@competitor_bp.route('/competitors/update/status')
@login_required
def update_status():
    """
    Güncelleme durumunu kontrol eder
    İlerleme çubuğu için kullanılır
    """
    try:
        status = get_update_status()
        
        return jsonify({
            'success': True,
            'status': status
        })
        
    except Exception as e:
        logging.error(f"Durum kontrolü hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@competitor_bp.route('/competitors/settings')
@login_required
def get_settings():
    """
    Rakip takip ayarlarını getirir
    """
    try:
        settings = get_competitor_settings()
        
        return jsonify({
            'success': True,
            'settings': settings
        })
        
    except Exception as e:
        logging.error(f"Ayar getirme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@competitor_bp.route('/competitors/settings', methods=['POST'])
@login_required
def save_settings():
    """
    Rakip takip ayarlarını kaydeder
    Otomatik güncelleme saati için kullanılır
    """
    try:
        data = request.get_json()
        schedule_time = data.get('schedule_time')
        username = session.get('username')
        
        # Saat formatını doğrula
        if not schedule_time or ':' not in schedule_time:
            return jsonify({
                'success': False,
                'error': 'Geçerli bir saat formatı giriniz (HH:MM)'
            }), 400
        
        try:
            hour, minute = schedule_time.split(':')
            hour = int(hour)
            minute = int(minute)
            
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
                
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'Geçerli bir saat formatı giriniz (00:00-23:59)'
            }), 400
        
        # Ayarları kaydet
        save_result = save_competitor_settings(schedule_time, username)
        
        if save_result:
            # Scheduler'ı güncelle
            update_scheduler(schedule_time)
            
            return jsonify({
                'success': True,
                'message': 'Ayarlar başarıyla kaydedildi!'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Ayarlar kaydedilirken hata oluştu!'
            }), 500
            
    except Exception as e:
        logging.error(f"Ayar kaydetme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# YENİ ROUTE: Fiyat Geçmişi Grafiği İçin
@competitor_bp.route('/competitors/price-history/<barcode>/<int:slot_number>')
@login_required
def get_price_history(barcode, slot_number):
    """
    Belirli bir barkod ve slot için son 30 günlük fiyat geçmişini getirir
    YENİ: Slot 0 (NeşeliÇiçekler) de desteklenir ama grafik gösterilmez
    """
    try:
        from datetime import datetime, timedelta
        
        # Slot 0 kontrolü - grafik gösterilmez ama API çalışır
        if slot_number == 0:
            return jsonify({
                'success': False,
                'error': 'NeşeliÇiçekler fiyat geçmişi grafik olarak gösterilmez'
            }), 400
        
        # Son 30 gün tarih aralığını hesapla
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        conn = get_db_connection()
        cursor = conn.execute('''
            SELECT 
                DATE(scrape_datetime) as price_date,
                price,
                product_name,
                seller_name,
                MAX(scrape_datetime) as latest_scrape
            FROM competitor_prices 
            WHERE barcode = ? 
            AND slot_number = ? 
            AND status = 'A'
            AND datetime(scrape_datetime) >= datetime(?)
            AND datetime(scrape_datetime) <= datetime(?)
            GROUP BY DATE(scrape_datetime)
            ORDER BY price_date ASC
        ''', (barcode, slot_number, start_date.isoformat(), end_date.isoformat()))
        
        price_history = []
        for row in cursor.fetchall():
            price_history.append({
                'date': row['price_date'],
                'price': row['price'],
                'product_name': row['product_name'],
                'seller_name': row['seller_name']
            })
        
        conn.close()
        
        # Link bilgisini de al
        link_info = None
        links = get_links_by_barcode(barcode, include_slot_0=True)
        for link in links:
            if link['slot_number'] == slot_number:
                link_info = link
                break
        
        return jsonify({
            'success': True,
            'barcode': barcode,
            'slot_number': slot_number,
            'price_history': price_history,
            'link_info': link_info,
            'total_days': len(price_history),
            'date_range': {
                'start': start_date.strftime('%d.%m.%Y'),
                'end': end_date.strftime('%d.%m.%Y')
            }
        })
        
    except Exception as e:
        logging.error(f"Fiyat geçmişi getirme hatası - {barcode}, slot {slot_number}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@competitor_bp.route('/competitors/debug')
@login_required
def debug_info():
    """
    Debug bilgileri için endpoint
    YENİ: Slot 0 istatistikleri de dahil
    """
    try:
        debug_data = {
            'tables_exist': check_tables_exist(),
            'total_links': get_total_links_count(include_slot_0=True),
            'total_competitor_links': get_total_links_count(include_slot_0=False),
            'total_neselicicekler_links': get_total_links_count(slot_0_only=True),
            'total_prices': get_total_prices_count(include_slot_0=True),
            'total_competitor_prices': get_total_prices_count(include_slot_0=False),
            'total_neselicicekler_prices': get_total_prices_count(slot_0_only=True),
            'last_update': get_last_update(),
            'scheduler_status': get_scheduler_status(),
            'scraping_status': get_update_status()
        }
        
        return jsonify({
            'success': True,
            'debug': debug_data
        })
        
    except Exception as e:
        logging.error(f"Debug bilgisi hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# YENİ ROUTE: Fiyat Karşılaştırması
@competitor_bp.route('/competitors/comparison/<barcode>')
@login_required
def get_price_comparison(barcode):
    """
    Belirli bir barkod için tüm scraping kaynaklarının son 30 günlük fiyat karşılaştırmasını getirir
    Sadece Slot 0 (NeşeliÇiçekler) + Slot 1-5 (Rakipler) - TY ana fiyatı dahil değil
    """
    try:
        from datetime import datetime, timedelta
        
        # Son 30 gün tarih aralığını hesapla
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        comparison_data = []
        
        # SADECE SCRAPING VERİLERİ: Slot 0-5 (NeşeliÇiçekler + Rakipler)
        conn = get_db_connection()
        
        # Her slot için en son aktif fiyat verilerini al (slot 0 dahil)
        active_prices = get_active_prices_by_barcode(barcode, exclude_slot_0=False)
        
        for price_data in active_prices:
            slot_number = price_data['slot_number']
            seller_name = price_data['seller_name']
            
            # Bu slot için son 30 günlük fiyat geçmişini al
            cursor = conn.execute('''
                SELECT 
                    DATE(scrape_datetime) as price_date,
                    price,
                    product_name,
                    seller_name,
                    MAX(scrape_datetime) as latest_scrape
                FROM competitor_prices 
                WHERE barcode = ? 
                AND slot_number = ? 
                AND status = 'A'
                AND datetime(scrape_datetime) >= datetime(?)
                AND datetime(scrape_datetime) <= datetime(?)
                GROUP BY DATE(scrape_datetime)
                ORDER BY price_date ASC
            ''', (barcode, slot_number, start_date.isoformat(), end_date.isoformat()))
            
            price_history = []
            for row in cursor.fetchall():
                price_history.append({
                    'date': row['price_date'],
                    'price': row['price'],
                    'product_name': row['product_name'],
                    'seller_name': row['seller_name']
                })
            
            # Veri varsa comparison_data'ya ekle
            if price_history:
                # Source name belirleme
                if slot_number == 0:
                    source_name = f"NeşeliÇiçekler ({seller_name})"
                    source_type = 'neselicicekler'
                else:
                    source_name = f"{seller_name}"  # Sadece mağaza adı
                    source_type = 'competitor'
                
                comparison_data.append({
                    'source_name': source_name,
                    'source_type': source_type,
                    'slot_number': slot_number,
                    'latest_price': price_history[-1]['price'] if price_history else None,
                    'price_history': price_history
                })
        
        conn.close()
        
        # Slot numarasına göre sırala (slot 0 en başta)
        comparison_data.sort(key=lambda x: x['slot_number'])
        
        # Karşılaştırma için minimum 2 kaynak gerekli
        if len(comparison_data) < 2:
            return jsonify({
                'success': True,
                'comparison_data': [],
                'barcode': barcode,
                'message': 'Karşılaştırma için en az 2 scraping verisi gereklidir'
            })
        
        return jsonify({
            'success': True,
            'barcode': barcode,
            'comparison_data': comparison_data,
            'total_sources': len(comparison_data),
            'date_range': {
                'start': start_date.strftime('%d.%m.%Y'),
                'end': end_date.strftime('%d.%m.%Y')
            }
        })
        
    except Exception as e:
        logging.error(f"Fiyat karşılaştırma hatası - {barcode}: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500



# DÜZELTME: Bu kısımları competitor_routes.py dosyasında değiştirin

# YENİ FONKSİYONLAR: Slot 0 desteği için - DÜZELTME

def save_links_with_slots(barcode: str, slot_links: dict, username: str, include_slot_0: bool = False):
    """
    Slot numaraları ile linkleri kaydet
    slot_links: {slot_number: url}
    DÜZELTME: Doğru fonksiyon çağrısı
    """
    try:
        from competitor_tracking import save_links_by_slots  # DOĞRU İMPORT
        return save_links_by_slots(barcode, slot_links, username, include_slot_0)
    except ImportError:
        # Fallback: Eski save_links fonksiyonunu kullan
        logging.warning("save_links_by_slots fonksiyonu bulunamadı, eski sistem kullanılıyor")
        from competitor_tracking import save_links
        
        # Slot mapping'i eski format'a çevir
        links_array = [''] * 6  # 0-5 slots
        for slot_num, url in slot_links.items():
            if 0 <= slot_num <= 5:
                links_array[slot_num] = url
        
        # Slot 0'ı skip et eğer include_slot_0 False ise
        if not include_slot_0:
            links_array = links_array[1:]  # İlk elementi çıkar
        
        return save_links(barcode, links_array, username)

def start_scraping_for_new_links_with_slots(barcode: str, slot_links: dict, username: str):
    """
    Yeni kaydedilen linkler için scraping başlat (slot 0 dahil)
    DÜZELTME: Doğru fonksiyon çağrısı
    """
    try:
        from competitor_scraper import start_scraping_for_new_links_by_slots  # DOĞRU İMPORT
        return start_scraping_for_new_links_by_slots(barcode, slot_links, username)
    except ImportError:
        # Fallback: Eski fonksiyonu kullan ama sadece slot 1-5 için
        logging.warning("start_scraping_for_new_links_by_slots fonksiyonu bulunamadı")
        from competitor_scraper import start_scraping_for_new_links
        
        # Sadece slot 1-5 linklerini çıkar
        competitor_links = []
        for slot_num in range(1, 6):
            if slot_num in slot_links:
                competitor_links.append(slot_links[slot_num])
            else:
                competitor_links.append('')
        
        return start_scraping_for_new_links(barcode, competitor_links, username)

def start_manual_update_with_slot_0(username: str):
    """
    Manuel güncelleme başlat (slot 0 dahil)
    """
    try:
        from competitor_scraper import start_manual_update_with_slot_0 as scraper_manual_update
        return scraper_manual_update(username)
    except ImportError:
        # Fallback: Eski fonksiyonu kullan
        logging.warning("start_manual_update_with_slot_0 fonksiyonu bulunamadı, eski sistem kullanılıyor")
        from competitor_scraper import start_manual_update
        return start_manual_update(username)

# Hata yakalama
@competitor_bp.errorhandler(404)
def not_found(error):
    """404 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'Sayfa bulunamadı'
    }), 404

@competitor_bp.errorhandler(500)
def internal_error(error):
    """500 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'İç sunucu hatası'
    }), 500