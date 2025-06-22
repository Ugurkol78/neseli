"""
Ürün İzleme ve Analiz Modülü - Route Yönetimi
Flask API endpoint'leri ve REST servisleri
Competitor routes'a benzer yapı
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from functools import wraps
import logging

# Product modül importları
from product_tracking import (
    check_product_tables_exist, init_product_database,
    add_product_link, get_active_product_links, get_latest_product_data,
    get_product_history_data, soft_delete_product,
    get_product_settings, save_product_settings,
    get_product_statistics
)
from product_scraper import (
    start_single_product_scraping, start_manual_update,
    get_scraping_status, is_scraping_running
)
from product_scheduler import (
    get_product_scheduler_status, update_product_scheduler
)

# Blueprint tanımı
product_bp = Blueprint('product', __name__)

def login_required(f):
    """Login kontrolü decorator fonksiyonu"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Admin kontrolü decorator fonksiyonu"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or session.get('role') != 'admin':
            flash('Bu işlem için admin yetkisi gerekli!', 'error')
            return redirect(url_for('product.products'))
        return f(*args, **kwargs)
    return decorated_function

@product_bp.route('/products')
@login_required
def products():
    """
    Ana ürün izleme sayfası
    Tüm ürünleri ve son verilerini gösterir
    """
    try:
        # Veritabanı kontrolü
        if not check_product_tables_exist():
            init_product_database()
        
        # Tüm ürün verilerini çek
        products_data = get_latest_product_data()
        
        # Son güncelleme zamanını çek
        stats = get_product_statistics()
        last_update = stats.get('last_update')
        
        return render_template('products.html', 
                             products=products_data,
                             last_update=last_update,
                             total_products=len(products_data))
                             
    except Exception as e:
        logging.error(f"Product ana sayfa hatası: {str(e)}")
        flash(f'Sayfa yüklenirken hata oluştu: {str(e)}', 'error')
        return redirect(url_for('index'))

@product_bp.route('/products/add', methods=['POST'])
@login_required
def add_product():
    """
    Yeni ürün linki ekler ve scraping başlatır
    """
    try:
        data = request.get_json()
        product_url = data.get('product_url', '').strip()
        first_comment_date = data.get('first_comment_date', '').strip()
        username = session.get('username')
        
        if not product_url:
            return jsonify({
                'success': False,
                'error': 'Ürün linki boş olamaz!'
            }), 400
        
        if not first_comment_date:
            return jsonify({
                'success': False,
                'error': 'İlk yorum tarihi boş olamaz!'
            }), 400
        
        # Ürünü veritabanına ekle
        success, message, product_link_id = add_product_link(
            product_url, first_comment_date, username
        )
        
        if success and product_link_id:
            # Arka planda scraping başlat
            try:
                start_single_product_scraping(product_link_id, product_url, username)
                
                return jsonify({
                    'success': True,
                    'message': message + ' Ürün verileri çekiliyor...',
                    'product_link_id': product_link_id
                })
            except Exception as scrape_error:
                logging.error(f"Scraping başlatma hatası: {str(scrape_error)}")
                return jsonify({
                    'success': True,
                    'message': message + ' (Veri çekme sırasında hata oluştu)',
                    'product_link_id': product_link_id
                })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        logging.error(f"Ürün ekleme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@product_bp.route('/products/delete/<int:product_link_id>', methods=['POST'])
@login_required
def delete_product(product_link_id):
    """
    Ürünü soft delete yapar (is_active = 0)
    """
    try:
        username = session.get('username')
        
        success = soft_delete_product(product_link_id, username)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Ürün başarıyla silindi!'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Ürün silinirken hata oluştu!'
            }), 500
            
    except Exception as e:
        logging.error(f"Ürün silme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@product_bp.route('/products/update/manual', methods=['POST'])
@admin_required
def manual_update():
    """
    Manuel güncelleme başlatır (Admin yetkisi gerekli)
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

@product_bp.route('/products/update/status')
@login_required
def update_status():
    """
    Güncelleme durumunu kontrol eder
    İlerleme çubuğu için kullanılır
    """
    try:
        status = get_scraping_status()
        
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

@product_bp.route('/products/settings')
@login_required
def get_settings():
    """
    Ürün izleme ayarlarını getirir
    """
    try:
        settings = get_product_settings()
        
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

@product_bp.route('/products/settings', methods=['POST'])
@login_required
def save_settings():
    """
    Ürün izleme ayarlarını kaydeder
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
        save_result = save_product_settings(schedule_time, username)
        
        if save_result:
            # Scheduler'ı güncelle
            update_product_scheduler(schedule_time)
            
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

@product_bp.route('/products/history/<int:product_link_id>/<data_type>')
@login_required
def get_product_history(product_link_id, data_type):
    """
    Belirli bir ürün için belirtilen veri tipinin son 30 günlük geçmişini getirir
    data_type: 'comment_count', 'rating', 'question_count', 'daily_estimated_sales', 'price', 'seller_rating', 'sales_3day'
    """
    try:
        # Geçerli veri tiplerini kontrol et
        valid_types = ['comment_count', 'rating', 'question_count', 'daily_estimated_sales', 'price', 'seller_rating', 'sales_3day']
        if data_type not in valid_types:
            return jsonify({
                'success': False,
                'error': 'Geçersiz veri tipi'
            }), 400
        
        # 30 günlük geçmiş veriyi çek
        history_data = get_product_history_data(product_link_id, data_type, 30)
        
        if not history_data:
            return jsonify({
                'success': True,
                'history': [],
                'message': 'Bu ürün için henüz geçmiş veri yok'
            })
        
        # Veri tipine göre başlık ve birim belirle
        data_info = {
            'comment_count': {'title': 'Yorum Sayısı', 'unit': 'adet'},
            'rating': {'title': 'Ürün Puanı', 'unit': 'puan'},
            'question_count': {'title': 'Soru Sayısı', 'unit': 'adet'},
            'daily_estimated_sales': {'title': 'Günlük Tahmini Satış', 'unit': 'adet/gün'},
            'price': {'title': 'Fiyat Trendi', 'unit': 'TL'},  # YENİ
            'seller_rating': {'title': 'Satıcı Puanı', 'unit': 'puan'},  # YENİ
            'sales_3day': {'title': '3 Günlük Satış', 'unit': 'adet'}  # YENİ
        }
        
        return jsonify({
            'success': True,
            'history': history_data,
            'data_info': data_info[data_type],
            'data_type': data_type,  # Frontend için eklendi
            'product_link_id': product_link_id,
            'total_days': len(history_data)
        })
        
    except Exception as e:
        logging.error(f"Ürün geçmişi getirme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@product_bp.route('/products/statistics')
@login_required
def get_statistics():
    """
    Ürün izleme modülü istatistiklerini getirir
    """
    try:
        stats = {
            'product_stats': get_product_statistics(),
            'scheduler_status': get_product_scheduler_status(),
            'scraping_status': get_scraping_status()
        }
        
        return jsonify({
            'success': True,
            'statistics': stats
        })
        
    except Exception as e:
        logging.error(f"İstatistik getirme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@product_bp.route('/products/debug')
@login_required
def debug_info():
    """
    Debug bilgileri için endpoint
    """
    try:
        debug_data = {
            'tables_exist': check_product_tables_exist(),
            'product_stats': get_product_statistics(),
            'scheduler_status': get_product_scheduler_status(),
            'scraping_status': get_scraping_status(),
            'settings': get_product_settings()
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

@product_bp.route('/products/refresh-data')
@login_required 
def refresh_data():
    """
    Sayfa verilerini yeniler (AJAX için)
    """
    try:
        # Güncel ürün verilerini çek
        products_data = get_latest_product_data()
        
        # İstatistikleri çek
        stats = get_product_statistics()
        
        return jsonify({
            'success': True,
            'products': products_data,
            'statistics': stats,
            'total_products': len(products_data)
        })
        
    except Exception as e:
        logging.error(f"Veri yenileme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@product_bp.route('/products/search')
@login_required
def search_products():
    """
    Ürün arama (filtreleme) için endpoint
    Query parameter: q (arama terimi)
    """
    try:
        search_term = request.args.get('q', '').strip().lower()
        
        if not search_term:
            # Arama terimi yoksa tüm ürünleri döndür
            products_data = get_latest_product_data()
        else:
            # Arama terimi varsa filtrele
            all_products = get_latest_product_data()
            products_data = []
            
            for product in all_products:
                # Ürün başlığında arama terimi var mı kontrol et (substring search)
                product_title = product.get('product_title', '').lower()
                if search_term in product_title:
                    products_data.append(product)
        
        return jsonify({
            'success': True,
            'products': products_data,
            'search_term': search_term,
            'total_found': len(products_data)
        })
        
    except Exception as e:
        logging.error(f"Ürün arama hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@product_bp.route('/products/export')
@login_required
def export_products():
    """
    Ürün verilerini Excel/CSV formatında dışa aktarır
    Query parameter: format (excel/csv)
    """
    try:
        export_format = request.args.get('format', 'excel').lower()
        
        if export_format not in ['excel', 'csv']:
            return jsonify({
                'success': False,
                'error': 'Geçersiz export formatı. excel veya csv olmalıdır.'
            }), 400
        
        # Tüm ürün verilerini çek
        products_data = get_latest_product_data()
        
        if not products_data:
            return jsonify({
                'success': False,
                'error': 'Dışa aktarılacak veri bulunamadı.'
            }), 404
        
        # Şimdilik sadece veri sayısını döndür (gerçek export implementasyonu sonra eklenebilir)
        return jsonify({
            'success': True,
            'message': f'{len(products_data)} ürün {export_format.upper()} formatında hazırlandı',
            'total_products': len(products_data),
            'format': export_format
        })
        
    except Exception as e:
        logging.error(f"Export hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Hata yakalama
@product_bp.errorhandler(404)
def not_found(error):
    """404 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'Sayfa bulunamadı'
    }), 404

@product_bp.errorhandler(500)
def internal_error(error):
    """500 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'İç sunucu hatası'
    }), 500

@product_bp.errorhandler(403)
def forbidden(error):
    """403 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'Bu işlem için yetkiniz yok'
    }), 403

# Blueprint context processor (template değişkenleri için)
@product_bp.context_processor
def inject_product_stats():
    """Template'lere ürün istatistiklerini enjekte eder"""
    try:
        if 'logged_in' in session:
            stats = get_product_statistics()
            return dict(product_stats=stats)
        return dict(product_stats={})
    except:
        return dict(product_stats={})

# Blueprint filters (template helper fonksiyonları)
@product_bp.app_template_filter('format_product_title')
def format_product_title(title, max_length=30):
    """Ürün başlığını belirtilen uzunlukta keser"""
    if not title:
        return "Başlık yok"
    
    if len(title) <= max_length:
        return title
    
    return title[:max_length] + "..."

@product_bp.app_template_filter('format_price')
def format_price(price):
    """Fiyatı Türk Lirası formatında gösterir"""
    if price is None or price == 0:
        return "0,00₺"
    
    try:
        return f"{float(price):,.2f}₺".replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return "0,00₺"

@product_bp.app_template_filter('format_rating')
def format_rating(rating):
    """Puanı yıldız formatında gösterir"""
    if rating is None or rating == 0:
        return "⭐ 0,0"
    
    try:
        star_count = "⭐" * int(rating)
        return f"{star_count} {float(rating):.1f}"
    except:
        return "⭐ 0,0"


# Test route'u (geliştirme aşamasında kullanılabilir)
@product_bp.route('/products/test')
@login_required
def test_product_system():
    """Ürün sistemi test endpoint'i"""
    try:
        test_results = {
            'database_tables': check_product_tables_exist(),
            'product_count': len(get_latest_product_data()),
            'scheduler_running': get_product_scheduler_status().get('is_running', False),
            'scraping_running': is_scraping_running(),
            'settings_loaded': bool(get_product_settings())
        }
        
        return jsonify({
            'success': True,
            'test_results': test_results,
            'message': 'Ürün sistemi test tamamlandı'
        })
        
    except Exception as e:
        logging.error(f"Sistem test hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

