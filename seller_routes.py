"""
Satıcı İzleme ve Analiz Modülü - Route Yönetimi
Flask API endpoint'leri ve REST servisleri
Product routes'a benzer yapı
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from functools import wraps
import logging

# Seller modül importları
from seller_tracking import (
    check_seller_tables_exist, init_seller_database,
    add_seller_links, get_active_seller_links, get_latest_seller_data,
    get_seller_history_data, soft_delete_seller,
    get_seller_settings, save_seller_settings,
    get_seller_statistics
)
from seller_scraper import (
    start_single_seller_scraping, start_manual_seller_update,
    get_seller_scraping_status, is_seller_scraping_running
)
from seller_scheduler import (
    get_seller_scheduler_status, update_seller_scheduler
)

# Blueprint tanımı
seller_bp = Blueprint('seller', __name__)

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
            return redirect(url_for('seller.sellers'))
        return f(*args, **kwargs)
    return decorated_function

@seller_bp.route('/sellers')
@login_required
def sellers():
    """
    Ana satıcı izleme sayfası
    Tüm satıcıları ve son verilerini gösterir
    """
    try:
        # Veritabanı kontrolü
        if not check_seller_tables_exist():
            init_seller_database()
        
        # Tüm satıcı verilerini çek
        sellers_data = get_latest_seller_data()
        
        # Son güncelleme zamanını çek
        stats = get_seller_statistics()
        last_update = stats.get('last_update')
        
        return render_template('sellers.html', 
                             sellers=sellers_data,
                             last_update=last_update,
                             total_sellers=len(sellers_data))
                             
    except Exception as e:
        logging.error(f"Seller ana sayfa hatası: {str(e)}")
        flash(f'Sayfa yüklenirken hata oluştu: {str(e)}', 'error')
        return redirect(url_for('index'))

@seller_bp.route('/sellers/add', methods=['POST'])
@login_required
def add_seller():
    """
    Yeni satıcı linklerini ekler ve scraping başlatır
    """
    try:
        data = request.get_json()
        all_products_url = data.get('all_products_url', '').strip()
        seller_profile_url = data.get('seller_profile_url', '').strip()
        username = session.get('username')
        
        if not all_products_url:
            return jsonify({
                'success': False,
                'error': 'Tüm ürünler sayfası linki boş olamaz!'
            }), 400
        
        if not seller_profile_url:
            return jsonify({
                'success': False,
                'error': 'Satıcı profili linki boş olamaz!'
            }), 400
        
        # Satıcıyı veritabanına ekle
        success, message, seller_link_id = add_seller_links(
            all_products_url, seller_profile_url, username
        )
        
        if success and seller_link_id:
            # Başarılı ekleme/aktive etme sonrası yeni scraping tetikle
            try:
                print(f"🔄 Yeni aktive edilen satıcı için scraping başlatılıyor (ID: {seller_link_id})")
                
                # Scraping durumunu manuel olarak başlat
                from seller_scraper import update_seller_scraping_status
                update_seller_scraping_status(
                    is_running=True, 
                    total=1, 
                    progress=0,
                    current_item=f"Yeni satıcı verisi çekiliyor...",
                    started_by=username
                )
                
                # Arka planda scraping işlemini başlat
                import threading
                from seller_scraper import scrape_all_sellers
                
                def background_scraping():
                    try:
                        # İlerleme durumunu güncelle
                        update_seller_scraping_status(
                            progress=0,
                            current_item="Satıcı sayfaları analiz ediliyor..."
                        )
                        
                        # Sadece bu satıcı için scraping yap
                        success = scrape_all_sellers(specific_seller_id=seller_link_id, username=username)

                        
                        if success:
                            update_seller_scraping_status(
                                progress=1,
                                current_item="Satıcı verisi başarıyla kaydedildi!",
                                success_count=1
                            )
                            print(f"✅ Satıcı {seller_link_id} için scraping tamamlandı")
                        else:
                            update_seller_scraping_status(
                                progress=1,
                                current_item="Satıcı verisi çekilemedi",
                                failed_count=1,
                                error="Scraping işlemi başarısız"
                            )
                            print(f"❌ Satıcı {seller_link_id} scraping başarısız")
                        
                        # 2 saniye bekle sonra scraping durumunu kapat
                        import time
                        time.sleep(2)
                        update_seller_scraping_status(is_running=False, current_item="")
                        
                    except Exception as e:
                        update_seller_scraping_status(
                            is_running=False,
                            error=f"Scraping hatası: {str(e)}",
                            current_item=""
                        )
                        print(f"❌ Satıcı {seller_link_id} scraping hatası: {str(e)}")
                
                # Arka plan thread'i başlat
                scraping_thread = threading.Thread(target=background_scraping)
                scraping_thread.daemon = True
                scraping_thread.start()
                
                return jsonify({
                    'success': True,
                    'message': message + ' Satıcı verileri çekiliyor...',
                    'seller_link_id': seller_link_id,
                    'scraping_started': True
                })
                
            except Exception as scrape_error:
                logging.error(f"Scraping başlatma hatası: {str(scrape_error)}")
                return jsonify({
                    'success': True,
                    'message': message + ' (Veri çekme sırasında hata oluştu)',
                    'seller_link_id': seller_link_id
                })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        logging.error(f"Satıcı ekleme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@seller_bp.route('/sellers/delete/<int:seller_link_id>', methods=['POST'])
@login_required
def delete_seller(seller_link_id):
    """
    Satıcıyı soft delete yapar (is_active = 0)
    """
    try:
        username = session.get('username')
        
        success = soft_delete_seller(seller_link_id, username)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Satıcı başarıyla silindi!'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Satıcı silinirken hata oluştu!'
            }), 500
            
    except Exception as e:
        logging.error(f"Satıcı silme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@seller_bp.route('/sellers/update/manual', methods=['POST'])
@admin_required
def manual_update():
    """
    Manuel güncelleme başlatır (Admin yetkisi gerekli)
    """
    try:
        username = session.get('username')
        
        # Eğer başka bir scraping devam ediyorsa uyarı ver
        if is_seller_scraping_running():
            return jsonify({
                'success': False,
                'error': 'Başka bir güncelleme işlemi devam ediyor. Lütfen bekleyin.'
            }), 400
        
        # Manuel güncelleme başlat
        success = start_manual_seller_update(username)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Manuel satıcı güncelleme başlatıldı!'
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

@seller_bp.route('/sellers/update/status')
@login_required
def update_status():
    """
    Güncelleme durumunu kontrol eder
    İlerleme çubuğu için kullanılır
    """
    try:
        status = get_seller_scraping_status()
        
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

@seller_bp.route('/sellers/settings')
@login_required
def get_settings():
    """
    Satıcı izleme ayarlarını getirir
    """
    try:
        settings = get_seller_settings()
        
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

@seller_bp.route('/sellers/settings', methods=['POST'])
@login_required
def save_settings():
    """
    Satıcı izleme ayarlarını kaydeder
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
        save_result = save_seller_settings(schedule_time, username)
        
        if save_result:
            # Scheduler'ı güncelle
            update_seller_scheduler(schedule_time)
            
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

@seller_bp.route('/sellers/history/<int:seller_link_id>/<data_type>')
@login_required
def get_seller_history(seller_link_id, data_type):
    """
    Belirli bir satıcı için belirtilen veri tipinin son 30 günlük geçmişini getirir
    data_type: 'seller_score', 'product_count', 'follower_count', 'total_reviews', 'total_comments', 'overall_rating'
    """
    try:
        # Geçerli veri tiplerini kontrol et
        valid_types = ['seller_score', 'product_count', 'follower_count', 'total_reviews', 'total_comments', 'overall_rating']
        if data_type not in valid_types:
            return jsonify({
                'success': False,
                'error': 'Geçersiz veri tipi'
            }), 400
        
        # 30 günlük geçmiş veriyi çek
        history_data = get_seller_history_data(seller_link_id, data_type, 30)
        
        if not history_data:
            return jsonify({
                'success': True,
                'history': [],
                'message': 'Bu satıcı için henüz geçmiş veri yok'
            })
        
        # Veri tipine göre başlık ve birim belirle
        data_info = {
            'seller_score': {'title': 'Satıcı Puanı', 'unit': 'puan'},
            'product_count': {'title': 'Ürün Sayısı', 'unit': 'adet'},
            'follower_count': {'title': 'Takipçi Sayısı', 'unit': 'kişi'},
            'total_reviews': {'title': 'Toplam Değerlendirme', 'unit': 'adet'},
            'total_comments': {'title': 'Toplam Yorum', 'unit': 'adet'},
            'overall_rating': {'title': 'Genel Rating', 'unit': 'puan'}
        }
        
        return jsonify({
            'success': True,
            'history': history_data,
            'data_info': data_info[data_type],
            'data_type': data_type,
            'seller_link_id': seller_link_id,
            'total_days': len(history_data)
        })
        
    except Exception as e:
        logging.error(f"Satıcı geçmişi getirme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@seller_bp.route('/sellers/statistics')
@login_required
def get_statistics():
    """
    Satıcı izleme modülü istatistiklerini getirir
    """
    try:
        stats = {
            'seller_stats': get_seller_statistics(),
            'scheduler_status': get_seller_scheduler_status(),
            'scraping_status': get_seller_scraping_status()
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

@seller_bp.route('/sellers/debug')
@login_required
def debug_info():
    """
    Debug bilgileri için endpoint
    """
    try:
        debug_data = {
            'tables_exist': check_seller_tables_exist(),
            'seller_stats': get_seller_statistics(),
            'scheduler_status': get_seller_scheduler_status(),
            'scraping_status': get_seller_scraping_status(),
            'settings': get_seller_settings()
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

@seller_bp.route('/sellers/refresh-data')
@login_required 
def refresh_data():
    """
    Sayfa verilerini yeniler (AJAX için)
    """
    try:
        # Güncel satıcı verilerini çek
        sellers_data = get_latest_seller_data()
        
        # İstatistikleri çek
        stats = get_seller_statistics()
        
        return jsonify({
            'success': True,
            'sellers': sellers_data,
            'statistics': stats,
            'total_sellers': len(sellers_data)
        })
        
    except Exception as e:
        logging.error(f"Veri yenileme hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@seller_bp.route('/sellers/search')
@login_required
def search_sellers():
    """
    Satıcı arama (filtreleme) için endpoint
    Query parameter: q (arama terimi)
    """
    try:
        search_term = request.args.get('q', '').strip().lower()
        
        if not search_term:
            # Arama terimi yoksa tüm satıcıları döndür
            sellers_data = get_latest_seller_data()
        else:
            # Arama terimi varsa filtrele
            all_sellers = get_latest_seller_data()
            sellers_data = []
            
            for seller in all_sellers:
                # Satıcı adında veya konumda arama terimi var mı kontrol et
                seller_name = seller.get('seller_name', '').lower()
                location = seller.get('location', '').lower()
                
                if search_term in seller_name or search_term in location:
                    sellers_data.append(seller)
        
        return jsonify({
            'success': True,
            'sellers': sellers_data,
            'search_term': search_term,
            'total_found': len(sellers_data)
        })
        
    except Exception as e:
        logging.error(f"Satıcı arama hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@seller_bp.route('/sellers/export')
@login_required
def export_sellers():
    """
    Satıcı verilerini Excel/CSV formatında dışa aktarır
    Query parameter: format (excel/csv)
    """
    try:
        export_format = request.args.get('format', 'excel').lower()
        
        if export_format not in ['excel', 'csv']:
            return jsonify({
                'success': False,
                'error': 'Geçersiz export formatı. excel veya csv olmalıdır.'
            }), 400
        
        # Tüm satıcı verilerini çek
        sellers_data = get_latest_seller_data()
        
        if not sellers_data:
            return jsonify({
                'success': False,
                'error': 'Dışa aktarılacak veri bulunamadı.'
            }), 404
        
        # Şimdilik sadece veri sayısını döndür (gerçek export implementasyonu sonra eklenebilir)
        return jsonify({
            'success': True,
            'message': f'{len(sellers_data)} satıcı {export_format.upper()} formatında hazırlandı',
            'total_sellers': len(sellers_data),
            'format': export_format
        })
        
    except Exception as e:
        logging.error(f"Export hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Hata yakalama
@seller_bp.errorhandler(404)
def not_found(error):
    """404 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'Sayfa bulunamadı'
    }), 404

@seller_bp.errorhandler(500)
def internal_error(error):
    """500 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'İç sunucu hatası'
    }), 500

@seller_bp.errorhandler(403)
def forbidden(error):
    """403 hata yakalama"""
    return jsonify({
        'success': False,
        'error': 'Bu işlem için yetkiniz yok'
    }), 403

# Blueprint context processor (template değişkenleri için)
@seller_bp.context_processor
def inject_seller_stats():
    """Template'lere satıcı istatistiklerini enjekte eder"""
    try:
        if 'logged_in' in session:
            stats = get_seller_statistics()
            return dict(seller_stats=stats)
        return dict(seller_stats={})
    except:
        return dict(seller_stats={})

# Blueprint filters (template helper fonksiyonları)
@seller_bp.app_template_filter('format_seller_name')
def format_seller_name(name, max_length=25):
    """Satıcı adını belirtilen uzunlukta keser"""
    if not name:
        return "Satıcı adı yok"
    
    if len(name) <= max_length:
        return name
    
    return name[:max_length] + "..."

@seller_bp.app_template_filter('format_follower_count')
def format_follower_count(count):
    """Takipçi sayısını formatlar"""
    if count is None or count == 0:
        return "0"
    
    try:
        count = int(count)
        if count >= 1000000:
            return f"{count/1000000:.1f}M"
        elif count >= 1000:
            return f"{count/1000:.1f}K"
        else:
            return str(count)
    except:
        return "0"

@seller_bp.app_template_filter('format_seller_score')
def format_seller_score(score):
    """Satıcı puanını formatlar"""
    if score is None or score == 0:
        return "0,0"
    
    try:
        return f"{float(score):.1f}"
    except:
        return "0,0"

@seller_bp.app_template_filter('format_store_age')
def format_store_age(age):
    """Mağaza yaşını formatlar"""
    if age is None or age == 0:
        return "Bilinmiyor"
    
    try:
        age = int(age)
        if age == 1:
            return "1 Yıl"
        else:
            return f"{age} Yıl"
    except:
        return "Bilinmiyor"

@seller_bp.app_template_filter('format_location')
def format_location(location):
    """Konum formatlar"""
    if not location or location.strip() == "":
        return "Bilinmiyor"
    
    return location.strip()

@seller_bp.app_template_filter('format_rating')
def format_rating(rating):
    """Rating formatlar"""
    if rating is None or rating == 0:
        return "0,0"
    
    try:
        return f"{float(rating):.1f}"
    except:
        return "0,0"

# Test route'u (geliştirme aşamasında kullanılabilir)
@seller_bp.route('/sellers/test')
@login_required
def test_seller_system():
    """Satıcı sistemi test endpoint'i"""
    try:
        test_results = {
            'database_tables': check_seller_tables_exist(),
            'seller_count': len(get_latest_seller_data()),
            'scheduler_running': get_seller_scheduler_status().get('is_running', False),
            'scraping_running': is_seller_scraping_running(),
            'settings_loaded': bool(get_seller_settings())
        }
        
        return jsonify({
            'success': True,
            'test_results': test_results,
            'message': 'Satıcı sistemi test tamamlandı'
        })
        
    except Exception as e:
        logging.error(f"Sistem test hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Satıcı URL validasyon endpoint'i
@seller_bp.route('/sellers/validate-urls', methods=['POST'])
@login_required
def validate_urls():
    """
    Satıcı URL'lerini doğrular (AJAX için)
    """
    try:
        data = request.get_json()
        all_products_url = data.get('all_products_url', '').strip()
        seller_profile_url = data.get('seller_profile_url', '').strip()
        
        from seller_tracking import validate_trendyol_seller_url
        
        validation_results = {
            'all_products_valid': validate_trendyol_seller_url(all_products_url, 'all_products'),
            'profile_valid': validate_trendyol_seller_url(seller_profile_url, 'profile'),
            'all_products_message': '',
            'profile_message': ''
        }
        
        # Hata mesajlarını belirle
        if not validation_results['all_products_valid']:
            if not all_products_url:
                validation_results['all_products_message'] = 'Link boş olamaz'
            elif 'trendyol.com' not in all_products_url:
                validation_results['all_products_message'] = 'Geçerli bir Trendyol linki giriniz'
            elif '/sr' not in all_products_url and '/butik' not in all_products_url:
                validation_results['all_products_message'] = 'Tüm ürünler sayfası linki olmalıdır (/sr veya /butik içermeli)'
            else:
                validation_results['all_products_message'] = 'Geçersiz format'
        
        if not validation_results['profile_valid']:
            if not seller_profile_url:
                validation_results['profile_message'] = 'Link boş olamaz'
            elif 'trendyol.com' not in seller_profile_url:
                validation_results['profile_message'] = 'Geçerli bir Trendyol linki giriniz'
            elif 'satici-profili' not in seller_profile_url and 'magaza/profil' not in seller_profile_url:
                validation_results['profile_message'] = 'Satıcı profil sayfası linki olmalıdır (satici-profili veya magaza/profil içermeli)'
            else:
                validation_results['profile_message'] = 'Geçersiz format'
        
        return jsonify({
            'success': True,
            'validation': validation_results
        })
        
    except Exception as e:
        logging.error(f"URL validasyon hatası: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

print("✅ Seller routes modülü yüklendi - Flask API endpoint'leri hazır")