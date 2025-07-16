"""
Satıcı İzleme ve Analiz Modülü - Otomatik Güncelleme Scheduler
Belirlenen saatte otomatik satıcı veri çekme işlemlerini yönetir
Product scheduler'a benzer yapı
"""

import schedule
import time
import threading
import logging
from datetime import datetime
import pytz
from seller_tracking import get_seller_settings
from seller_scraper import start_scheduled_seller_update, is_seller_scraping_running

# Global scheduler durumu
seller_scheduler_status = {
    'is_running': False,
    'current_schedule': None,
    'next_run': None,
    'last_run': None,
    'thread': None,
    'last_run_stats': None
}

seller_scheduler_lock = threading.Lock()

def get_turkey_time():
    """Türkiye saatini döndürür"""
    turkey_tz = pytz.timezone('Europe/Istanbul')
    return datetime.now(turkey_tz)

def scheduled_seller_job():
    """
    Zamanlanmış iş - otomatik satıcı güncelleme başlatır
    """
    try:
        current_time = get_turkey_time()
        
        logging.info(f"Otomatik satıcı güncelleme tetiklendi: {current_time.strftime('%d.%m.%Y %H:%M:%S')}")
        
        # Eğer manuel scraping devam ediyorsa atla
        if is_seller_scraping_running():
            logging.warning("Manuel satıcı scraping devam ediyor, otomatik güncelleme atlandı")
            return
        
        # Otomatik güncelleme başlat
        success = start_scheduled_seller_update("seller_scheduler")
        
        if success:
            with seller_scheduler_lock:
                seller_scheduler_status['last_run'] = current_time.strftime('%d.%m.%Y %H:%M:%S')
                
            logging.info("Otomatik satıcı güncelleme başlatıldı")
        else:
            logging.error("Otomatik satıcı güncelleme başlatılamadı")
            
    except Exception as e:
        logging.error(f"Scheduled seller job hatası: {str(e)}")

def seller_scheduler_worker():
    """
    Scheduler'ın sürekli çalışması için worker thread
    """
    while True:
        try:
            with seller_scheduler_lock:
                if not seller_scheduler_status['is_running']:
                    break
                    
            schedule.run_pending()
            time.sleep(60)  # Her dakika kontrol et
            
        except Exception as e:
            logging.error(f"Seller scheduler worker hatası: {str(e)}")
            time.sleep(60)

def start_seller_scheduler():
    """
    Seller scheduler'ı başlatır
    """
    global seller_scheduler_status
    
    try:
        with seller_scheduler_lock:
            if seller_scheduler_status['is_running']:
                logging.warning("Seller scheduler zaten çalışıyor")
                return True
            
            # Mevcut ayarları al
            settings = get_seller_settings()
            schedule_time = settings.get('schedule_time', '11:00')
            
            # Mevcut schedule'ları temizle
            schedule.clear('seller_jobs')
            
            # Yeni schedule ekle
            schedule.every().day.at(schedule_time).do(scheduled_seller_job).tag('seller_jobs')
            
            # Scheduler durumunu güncelle
            seller_scheduler_status['is_running'] = True
            seller_scheduler_status['current_schedule'] = schedule_time
            
            # Bir sonraki çalışma zamanını hesapla
            next_run = schedule.next_run()
            if next_run:
                # UTC'den Türkiye saatine çevir
                turkey_tz = pytz.timezone('Europe/Istanbul')
                next_run_turkey = next_run.replace(tzinfo=pytz.UTC).astimezone(turkey_tz)
                seller_scheduler_status['next_run'] = next_run_turkey.strftime('%d.%m.%Y %H:%M:%S')
            
            # Worker thread başlat
            worker_thread = threading.Thread(target=seller_scheduler_worker)
            worker_thread.daemon = True
            worker_thread.start()
            
            seller_scheduler_status['thread'] = worker_thread
            
            logging.info(f"Seller scheduler başlatıldı: Günlük {schedule_time} - Sonraki çalışma: {seller_scheduler_status['next_run']}")
            return True
            
    except Exception as e:
        logging.error(f"Seller scheduler başlatma hatası: {str(e)}")
        return False

def stop_seller_scheduler():
    """
    Seller scheduler'ı durdurur
    """
    global seller_scheduler_status
    
    try:
        with seller_scheduler_lock:
            if not seller_scheduler_status['is_running']:
                logging.warning("Seller scheduler zaten durmuş")
                return True
            
            # Schedule'ları temizle
            schedule.clear('seller_jobs')
            
            # Durumu güncelle
            seller_scheduler_status['is_running'] = False
            seller_scheduler_status['current_schedule'] = None
            seller_scheduler_status['next_run'] = None
            seller_scheduler_status['thread'] = None
            
            logging.info("Seller scheduler durduruldu")
            return True
            
    except Exception as e:
        logging.error(f"Seller scheduler durdurma hatası: {str(e)}")
        return False

def update_seller_scheduler(new_schedule_time: str):
    """
    Seller scheduler'ın çalışma saatini günceller
    """
    try:
        # Önce durdur
        stop_seller_scheduler()
        
        # Kısa bekleme
        time.sleep(1)
        
        # Yeni saatlerle başlat
        success = start_seller_scheduler()
        
        if success:
            logging.info(f"Seller scheduler güncellendi: Yeni saat {new_schedule_time}")
            return True
        else:
            logging.error("Seller scheduler güncellenemedi")
            return False
            
    except Exception as e:
        logging.error(f"Seller scheduler güncelleme hatası: {str(e)}")
        return False

def get_seller_scheduler_status() -> dict:
    """
    Mevcut seller scheduler durumunu döndürür
    """
    with seller_scheduler_lock:
        status = seller_scheduler_status.copy()
        
        # Eğer çalışıyorsa sonraki çalışma zamanını güncelle
        if status['is_running']:
            try:
                next_run = None
                for job in schedule.jobs:
                    if 'seller_jobs' in job.tags:
                        next_run = job.next_run
                        break
                
                if next_run:
                    turkey_tz = pytz.timezone('Europe/Istanbul')
                    next_run_turkey = next_run.replace(tzinfo=pytz.UTC).astimezone(turkey_tz)
                    status['next_run'] = next_run_turkey.strftime('%d.%m.%Y %H:%M:%S')
            except:
                pass
        
        return status

def validate_seller_schedule_time(time_str: str) -> bool:
    """
    Saat formatını doğrular (HH:MM)
    """
    try:
        hour, minute = time_str.split(':')
        hour = int(hour)
        minute = int(minute)
        
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return True
        else:
            return False
            
    except ValueError:
        return False

def get_seller_next_run_info() -> dict:
    """
    Sonraki çalışma hakkında detaylı bilgi döndürür
    """
    try:
        current_time = get_turkey_time()
        settings = get_seller_settings()
        schedule_time = settings.get('schedule_time', '11:00')
        
        # Bugün için hedef saati oluştur
        hour, minute = schedule_time.split(':')
        target_time = current_time.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        
        # Eğer hedef saat geçmişse yarına al
        if target_time <= current_time:
            target_time = target_time.replace(day=target_time.day + 1)
        
        # Kalan süreyi hesapla
        time_diff = target_time - current_time
        hours_remaining = int(time_diff.total_seconds() // 3600)
        minutes_remaining = int((time_diff.total_seconds() % 3600) // 60)
        
        return {
            'current_time': current_time.strftime('%d.%m.%Y %H:%M:%S'),
            'next_run': target_time.strftime('%d.%m.%Y %H:%M:%S'),
            'hours_remaining': hours_remaining,
            'minutes_remaining': minutes_remaining,
            'schedule_time': schedule_time
        }
        
    except Exception as e:
        logging.error(f"Seller next run info hatası: {str(e)}")
        return {}

def get_seller_scheduler_statistics() -> dict:
    """
    Seller scheduler istatistiklerini getirir
    """
    try:
        from seller_tracking import get_seller_statistics
        from seller_scraper import get_seller_scraping_statistics
        
        stats = {
            'scheduler_status': get_seller_scheduler_status(),
            'seller_stats': get_seller_statistics(),
            'scraping_stats': get_seller_scraping_statistics(),
            'next_run_info': get_seller_next_run_info()
        }
        
        return stats
        
    except Exception as e:
        logging.error(f"Seller scheduler istatistik hatası: {str(e)}")
        return {}

def schedule_immediate_seller_update():
    """
    Anlık satıcı güncelleme zamanlar
    Normal schedule'dan bağımsız
    """
    def immediate_seller_job():
        try:
            logging.info("Anlık satıcı güncellemesi başlatılıyor")
            start_scheduled_seller_update("immediate_seller_scheduler")
        except Exception as e:
            logging.error(f"Anlık satıcı güncellemesi hatası: {str(e)}")
    
    # 5 saniye sonra çalıştır
    schedule.every(5).seconds.do(immediate_seller_job).tag('immediate_seller')
    
    logging.info("Anlık satıcı güncellemesi 5 saniye sonra başlayacak")

def cancel_immediate_seller_jobs():
    """
    Anlık satıcı işlerini iptal eder
    """
    schedule.clear('immediate_seller')
    logging.info("Anlık satıcı işleri iptal edildi")

# Uygulama başlarken scheduler'ı otomatik başlat
def init_seller_scheduler():
    """
    Uygulama başladığında seller scheduler'ı başlatır
    """
    try:
        # Ayarları kontrol et
        settings = get_seller_settings()
        if 'schedule_time' in settings:
            success = start_seller_scheduler()
            if success:
                logging.info("Seller scheduler otomatik olarak başlatıldı")
            else:
                logging.error("Seller scheduler otomatik başlatılamadı")
        else:
            logging.info("Seller schedule ayarı bulunamadı, scheduler başlatılmadı")
            
    except Exception as e:
        logging.error(f"Seller scheduler init hatası: {str(e)}")

# Uygulama kapatılırken scheduler'ı durdur
def cleanup_seller_scheduler():
    """
    Uygulama kapatılırken seller scheduler'ı temizler
    """
    try:
        stop_seller_scheduler()
        cancel_immediate_seller_jobs()
        logging.info("Seller scheduler temizlendi")
    except Exception as e:
        logging.error(f"Seller scheduler cleanup hatası: {str(e)}")

# Hem product hem seller scheduler'ları yönetmek için ana fonksiyonlar
def start_all_seller_schedulers():
    """
    Tüm scheduler'ları başlatır (product + seller + competitor)
    """
    try:
        # Product scheduler'ı başlat
        try:
            from product_scheduler import start_product_scheduler
            start_product_scheduler()
            logging.info("Product scheduler başlatıldı")
        except ImportError:
            logging.warning("Product scheduler modülü bulunamadı")
        except Exception as e:
            logging.error(f"Product scheduler başlatma hatası: {str(e)}")
        
        # Competitor scheduler'ı başlat
        try:
            from competitor_scheduler import start_scheduler as start_competitor_scheduler
            start_competitor_scheduler()
            logging.info("Competitor scheduler başlatıldı")
        except ImportError:
            logging.warning("Competitor scheduler modülü bulunamadı")
        except Exception as e:
            logging.error(f"Competitor scheduler başlatma hatası: {str(e)}")
        
        # Seller scheduler'ı başlat
        success = start_seller_scheduler()
        if success:
            logging.info("Seller scheduler başlatıldı")
        else:
            logging.error("Seller scheduler başlatılamadı")
            
        return True
        
    except Exception as e:
        logging.error(f"Tüm scheduler'lar başlatma hatası: {str(e)}")
        return False

def stop_all_seller_schedulers():
    """
    Tüm scheduler'ları durdurur (product + seller + competitor)
    """
    try:
        # Product scheduler'ı durdur
        try:
            from product_scheduler import stop_product_scheduler
            stop_product_scheduler()
            logging.info("Product scheduler durduruldu")
        except ImportError:
            logging.warning("Product scheduler modülü bulunamadı")
        except Exception as e:
            logging.error(f"Product scheduler durdurma hatası: {str(e)}")
        
        # Competitor scheduler'ı durdur
        try:
            from competitor_scheduler import stop_scheduler as stop_competitor_scheduler
            stop_competitor_scheduler()
            logging.info("Competitor scheduler durduruldu")
        except ImportError:
            logging.warning("Competitor scheduler modülü bulunamadı")
        except Exception as e:
            logging.error(f"Competitor scheduler durdurma hatası: {str(e)}")
        
        # Seller scheduler'ı durdur
        success = stop_seller_scheduler()
        if success:
            logging.info("Seller scheduler durduruldu")
        else:
            logging.error("Seller scheduler durdurulamadı")
            
        return True
        
    except Exception as e:
        logging.error(f"Tüm scheduler'lar durdurma hatası: {str(e)}")
        return False

def get_all_seller_scheduler_status() -> dict:
    """
    Tüm scheduler'ların durumunu getirir
    """
    try:
        status = {
            'seller_scheduler': get_seller_scheduler_status()
        }
        
        # Product scheduler durumunu ekle
        try:
            from product_scheduler import get_product_scheduler_status
            status['product_scheduler'] = get_product_scheduler_status()
        except ImportError:
            status['product_scheduler'] = {'error': 'Modül bulunamadı'}
        except Exception as e:
            status['product_scheduler'] = {'error': str(e)}
        
        # Competitor scheduler durumunu ekle
        try:
            from competitor_scheduler import get_scheduler_status as get_competitor_status
            status['competitor_scheduler'] = get_competitor_status()
        except ImportError:
            status['competitor_scheduler'] = {'error': 'Modül bulunamadı'}
        except Exception as e:
            status['competitor_scheduler'] = {'error': str(e)}
        
        return status
        
    except Exception as e:
        logging.error(f"Tüm scheduler durumu hatası: {str(e)}")
        return {}

def sync_scheduler_times():
    """
    Tüm scheduler'ların farklı saatlerde çalışması için senkronizasyon
    """
    try:
        # Her modülün farklı saatte çalışması için offset'ler
        base_hour = 10  # Temel saat
        
        schedules = {
            'competitor': f"{base_hour:02d}:00",      # 10:00
            'product': f"{(base_hour + 1):02d}:00",   # 11:00  
            'seller': f"{(base_hour + 2):02d}:00"     # 12:00
        }
        
        logging.info("Scheduler saatleri senkronize ediliyor:")
        for module, time_str in schedules.items():
            logging.info(f"  {module}: {time_str}")
        
        return schedules
        
    except Exception as e:
        logging.error(f"Scheduler senkronizasyon hatası: {str(e)}")
        return {}

def monitor_scheduler_health():
    """
    Scheduler'ların sağlığını izler
    """
    try:
        status = get_seller_scheduler_status()
        
        if not status['is_running']:
            logging.warning("Seller scheduler çalışmıyor!")
            
            # Otomatik yeniden başlatma dene
            if init_seller_scheduler():
                logging.info("Seller scheduler otomatik olarak yeniden başlatıldı")
            else:
                logging.error("Seller scheduler yeniden başlatılamadı")
        
        # Sonraki çalışma zamanını kontrol et
        next_run = status.get('next_run')
        if next_run:
            logging.info(f"Seller scheduler sonraki çalışma: {next_run}")
        
        return status
        
    except Exception as e:
        logging.error(f"Scheduler health monitor hatası: {str(e)}")
        return {}

# Test fonksiyonu
def test_seller_scheduler():
    """Seller scheduler test fonksiyonu"""
    logging.info("Seller scheduler test başlatılıyor...")
    
    # Durumu kontrol et
    status = get_seller_scheduler_status()
    print("Mevcut durum:", status)
    
    # Başlat
    success = start_seller_scheduler()
    print("Başlatma sonucu:", success)
    
    # Tekrar durum kontrol et
    status = get_seller_scheduler_status()
    print("Yeni durum:", status)
    
    # İstatistikleri göster
    stats = get_seller_scheduler_statistics()
    print("İstatistikler:", stats)
    
    return status, stats

# Uygulama başladığında otomatik olarak scheduler'ı başlat
if __name__ != "__main__":
    # Sadece modül import edildiğinde çalıştır
    try:
        # 5 saniye bekle ki diğer modüller yüklensin
        def delayed_init():
            time.sleep(5)
            init_seller_scheduler()
        
        init_thread = threading.Thread(target=delayed_init)
        init_thread.daemon = True
        init_thread.start()
        
    except Exception as e:
        logging.error(f"Seller scheduler otomatik başlatma hatası: {str(e)}")

print("✅ Seller scheduler modülü yüklendi - Otomatik satıcı güncelleme zamanlayıcısı")