"""
Rakip Fiyat Takip Modülü - Route Yönetimi
Trendyol ürünleri için rakip fiyat analizi ve takip sistemi
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
    get_db_connection  # ⭐ Bunu ekleyin
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
        
        # Her ürün için rakip fiyat verilerini hazırla
        competitor_data = {}
        for product in products:
            barcode = product.get('barcode', '')
            if barcode:
                # Bu barkod için aktif fiyatları al
                prices = get_active_prices_by_barcode(barcode)
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
    Popup açılırken mevcut linkler için kullanılır
    """
    try:
        links = get_links_by_barcode(barcode)
        
        # 5 slot için array hazırla
        link_array = [''] * 5
        for link in links:
            slot_num = link['slot_number']
            if 1 <= slot_num <= 5:
                link_array[slot_num - 1] = link['url']
        
        return jsonify({
            'success': True,
            'links': link_array,
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
    Popup'tan gelen link verilerini işler
    """
    try:
        data = request.get_json()
        links = data.get('links', [])
        username = session.get('username')
        
        # Boş olmayan linkleri temizle ve kontrol et
        clean_links = [link.strip() for link in links]
        non_empty_links = [link for link in clean_links if link]
        
        # Link validasyonu
        is_valid, invalid_links = validate_trendyol_links(non_empty_links)
        
        if not is_valid:
            return jsonify({
                'success': False,
                'error': f'Geçersiz Trendyol linki: {", ".join(invalid_links)}'
            }), 400
        
        # Linkleri kaydet
        save_result = save_links(barcode, clean_links, username)
        
        if save_result:
            # Yeni linkler için scraping başlat
            start_scraping_for_new_links(barcode, clean_links, username)
            
            return jsonify({
                'success': True,
                'message': 'Linkler başarıyla kaydedildi ve fiyat güncelleme başlatıldı!'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Linkler kaydedilirken hata oluştu!'
            }), 500
            
    except Exception as e:
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
    Ana sayfada slot verilerini doldurmak için kullanılır
    """
    try:
        prices = get_active_prices_by_barcode(barcode)
        
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
    Tüm aktif linkler için scraping yapar
    """
    try:
        username = session.get('username')
        
        # Eğer başka bir scraping devam ediyorsa uyarı ver
        if is_scraping_running():
            return jsonify({
                'success': False,
                'error': 'Başka bir güncelleme işlemi devam ediyor. Lütfen bekleyin.'
            }), 400
        
        # Manuel güncelleme başlat
        success = start_manual_update(username)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Manuel güncelleme başlatıldı!'
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
    Grafik popup için kullanılır
    """
    try:
        from datetime import datetime, timedelta
        
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
        links = get_links_by_barcode(barcode)
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

# YENİ ROUTE: Veritabanı import kontrolü eklenmesi gerek
try:
    from competitor_tracking import get_db_connection
except ImportError:
    logging.error("competitor_tracking modülü import edilemedi!")

@competitor_bp.route('/competitors/debug')
@login_required
def debug_info():
    """
    Debug bilgileri için endpoint
    Geliştirme aşamasında kullanılacak
    """
    try:
        debug_data = {
            'tables_exist': check_tables_exist(),
            'total_links': get_total_links_count(),
            'total_prices': get_total_prices_count(),
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