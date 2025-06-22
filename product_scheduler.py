"""
Ürün İzleme ve Analiz Modülü - Otomatik Güncelleme Scheduler
Belirlenen saatte otomatik ürün veri çekme işlemlerini yönetir
Competitor scheduler'a benzer yapı
"""

import schedule
import time
import threading
import logging
from datetime import datetime
import pytz
from product_tracking import get_product_settings
from product_scraper import start_scheduled_update, is_scraping_running

# Global scheduler durumu
product_scheduler_status = {
    'is_running': False,
    'current_schedule': None,
    'next_run': None,
    'last_run': None,
    'thread': None,
    'last_run_stats': None
}

product_scheduler_lock = threading.Lock()

def get_turkey_time():
    """Türkiye saatini döndürür"""
    turkey_tz = pytz.timezone('Europe/Istanbul')
    return datetime.now(turkey_tz)

def scheduled_product_job():
    """
    Zamanlanmış iş - otomatik ürün güncelleme başlatır
    """
    try:
        current_time = get_turkey_time()
        
        logging.info(f"Otomatik ürün güncelleme tetiklendi: {current_time.strftime('%d.%m.%Y %H:%M:%S')}")
        
        # Eğer manuel scraping devam ediyorsa atla
        if is_scraping_running():
            logging.warning("Manuel ürün scraping devam ediyor, otomatik güncelleme atlandı")
            return
        
        # Otomatik güncelleme başlat
        success = start_scheduled_update("product_scheduler")
        
        if success:
            with product_scheduler_lock:
                product_scheduler_status['last_run'] = current_time.strftime('%d.%m.%Y %H:%M:%S')
                
            logging.info("Otomatik ürün güncelleme başlatıldı")
        else:
            logging.error("Otomatik ürün güncelleme başlatılamadı")
            
    except Exception as e:
        logging.error(f"Scheduled product job hatası: {str(e)}")

def product_scheduler_worker():
    """
    Scheduler'ın sürekli çalışması için worker thread
    """
    while True:
        try:
            with product_scheduler_lock:
                if not product_scheduler_status['is_running']:
                    break
                    
            schedule.run_pending()
            time.sleep(60)  # Her dakika kontrol et
            
        except Exception as e:
            logging.error(f"Product scheduler worker hatası: {str(e)}")
            time.sleep(60)

def start_product_scheduler():
    """
    Product scheduler'ı başlatır
    """
    global product_scheduler_status
    
    try:
        with product_scheduler_lock:
            if product_scheduler_status['is_running']:
                logging.warning("Product scheduler zaten çalışıyor")
                return True
            
            # Mevcut ayarları al
            settings = get_product_settings()
            schedule_time = settings.get('schedule_time', '10:00')
            
            # Mevcut schedule'ları temizle
            schedule.clear('product_jobs')
            
            # Yeni schedule ekle
            schedule.every().day.at(schedule_time).do(scheduled_product_job).tag('product_jobs')
            
            # Scheduler durumunu güncelle
            product_scheduler_status['is_running'] = True
            product_scheduler_status['current_schedule'] = schedule_time
            
            # Bir sonraki çalışma zamanını hesapla
            next_run = schedule.next_run()
            if next_run:
                # UTC'den Türkiye saatine çevir
                turkey_tz = pytz.timezone('Europe/Istanbul')
                next_run_turkey = next_run.replace(tzinfo=pytz.UTC).astimezone(turkey_tz)
                product_scheduler_status['next_run'] = next_run_turkey.strftime('%d.%m.%Y %H:%M:%S')
            
            # Worker thread başlat
            worker_thread = threading.Thread(target=product_scheduler_worker)
            worker_thread.daemon = True
            worker_thread.start()
            
            product_scheduler_status['thread'] = worker_thread
            
            logging.info(f"Product scheduler başlatıldı: Günlük {schedule_time} - Sonraki çalışma: {product_scheduler_status['next_run']}")
            return True
            
    except Exception as e:
        logging.error(f"Product scheduler başlatma hatası: {str(e)}")
        return False

def stop_product_scheduler():
    """
    Product scheduler'ı durdurur
    """
    global product_scheduler_status
    
    try:
        with product_scheduler_lock:
            if not product_scheduler_status['is_running']:
                logging.warning("Product scheduler zaten durmuş")
                return True
            
            # Schedule'ları temizle
            schedule.clear('product_jobs')
            
            # Durumu güncelle
            product_scheduler_status['is_running'] = False
            product_scheduler_status['current_schedule'] = None
            product_scheduler_status['next_run'] = None
            product_scheduler_status['thread'] = None
            
            logging.info("Product scheduler durduruldu")
            return True
            
    except Exception as e:
        logging.error(f"Product scheduler durdurma hatası: {str(e)}")
        return False

def update_product_scheduler(new_schedule_time: str):
    """
    Product scheduler'ın çalışma saatini günceller
    """
    try:
        # Önce durdur
        stop_product_scheduler()
        
        # Kısa bekleme
        time.sleep(1)
        
        # Yeni saatlerle başlat
        success = start_product_scheduler()
        
        if success:
            logging.info(f"Product scheduler güncellendi: Yeni saat {new_schedule_time}")
            return True
        else:
            logging.error("Product scheduler güncellenemedi")
            return False
            
    except Exception as e:
        logging.error(f"Product scheduler güncelleme hatası: {str(e)}")
        return False

def get_product_scheduler_status() -> dict:
    """
    Mevcut product scheduler durumunu döndürür
    """
    with product_scheduler_lock:
        status = product_scheduler_status.copy()
        
        # Eğer çalışıyorsa sonraki çalışma zamanını güncelle
        if status['is_running']:
            try:
                next_run = None
                for job in schedule.jobs:
                    if 'product_jobs' in job.tags:
                        next_run = job.next_run
                        break
                
                if next_run:
                    turkey_tz = pytz.timezone('Europe/Istanbul')
                    next_run_turkey = next_run.replace(tzinfo=pytz.UTC).astimezone(turkey_tz)
                    status['next_run'] = next_run_turkey.strftime('%d.%m.%Y %H:%M:%S')
            except:
                pass
        
        return status

def validate_product_schedule_time(time_str: str) -> bool:
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

def get_product_next_run_info() -> dict:
    """
    Sonraki çalışma hakkında detaylı bilgi döndürür
    """
    try:
        current_time = get_turkey_time()
        settings = get_product_settings()
        schedule_time = settings.get('schedule_time', '10:00')
        
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
        logging.error(f"Product next run info hatası: {str(e)}")
        return {}

def get_product_scheduler_statistics() -> dict:
    """
    Product scheduler istatistiklerini getirir
    """
    try:
        from product_tracking import get_product_statistics
        from product_scraper import get_scraping_statistics
        
        stats = {
            'scheduler_status': get_product_scheduler_status(),
            'product_stats': get_product_statistics(),
            'scraping_stats': get_scraping_statistics(),
            'next_run_info': get_product_next_run_info()
        }
        
        return stats
        
    except Exception as e:
        logging.error(f"Product scheduler istatistik hatası: {str(e)}")
        return {}

def schedule_immediate_product_update():
    """
    Anlık ürün güncelleme zamanlar
    Normal schedule'dan bağımsız
    """
    def immediate_product_job():
        try:
            logging.info("Anlık ürün güncellemesi başlatılıyor")
            start_scheduled_update("immediate_product_scheduler")
        except Exception as e:
            logging.error(f"Anlık ürün güncellemesi hatası: {str(e)}")
    
    # 5 saniye sonra çalıştır
    schedule.every(5).seconds.do(immediate_product_job).tag('immediate_product')
    
    logging.info("Anlık ürün güncellemesi 5 saniye sonra başlayacak")

def cancel_immediate_product_jobs():
    """
    Anlık ürün işlerini iptal eder
    """
    schedule.clear('immediate_product')
    logging.info("Anlık ürün işleri iptal edildi")

# Uygulama başlarken scheduler'ı otomatik başlat
def init_product_scheduler():
    """
    Uygulama başladığında product scheduler'ı başlatır
    """
    try:
        # Ayarları kontrol et
        settings = get_product_settings()
        if 'schedule_time' in settings:
            success = start_product_scheduler()
            if success:
                logging.info("Product scheduler otomatik olarak başlatıldı")
            else:
                logging.error("Product scheduler otomatik başlatılamadı")
        else:
            logging.info("Product schedule ayarı bulunamadı, scheduler başlatılmadı")
            
    except Exception as e:
        logging.error(f"Product scheduler init hatası: {str(e)}")

# Uygulama kapatılırken scheduler'ı durdur
def cleanup_product_scheduler():
    """
    Uygulama kapatılırken product scheduler'ı temizler
    """
    try:
        stop_product_scheduler()
        cancel_immediate_product_jobs()
        logging.info("Product scheduler temizlendi")
    except Exception as e:
        logging.error(f"Product scheduler cleanup hatası: {str(e)}")

# Hem competitor hem product scheduler'ları yönetmek için ana fonksiyonlar
def start_all_schedulers():
    """
    Tüm scheduler'ları başlatır (competitor + product)
    """
    try:
        # Competitor scheduler'ı başlat
        try:
            from competitor_scheduler import start_scheduler as start_competitor_scheduler
            start_competitor_scheduler()
            logging.info("Competitor scheduler başlatıldı")
        except ImportError:
            logging.warning("Competitor scheduler modülü bulunamadı")
        except Exception as e:
            logging.error(f"Competitor scheduler başlatma hatası: {str(e)}")
        
        # Product scheduler'ı başlat
        success = start_product_scheduler()
        if success:
            logging.info("Product scheduler başlatıldı")
        else:
            logging.error("Product scheduler başlatılamadı")
            
        return True
        
    except Exception as e:
        logging.error(f"Tüm scheduler'lar başlatma hatası: {str(e)}")
        return False

def stop_all_schedulers():
    """
    Tüm scheduler'ları durdurur (competitor + product)
    """
    try:
        # Competitor scheduler'ı durdur
        try:
            from competitor_scheduler import stop_scheduler as stop_competitor_scheduler
            stop_competitor_scheduler()
            logging.info("Competitor scheduler durduruldu")
        except ImportError:
            logging.warning("Competitor scheduler modülü bulunamadı")
        except Exception as e:
            logging.error(f"Competitor scheduler durdurma hatası: {str(e)}")
        
        # Product scheduler'ı durdur
        success = stop_product_scheduler()
        if success:
            logging.info("Product scheduler durduruldu")
        else:
            logging.error("Product scheduler durdurulamadı")
            
        return True
        
    except Exception as e:
        logging.error(f"Tüm scheduler'lar durdurma hatası: {str(e)}")
        return False

def get_all_scheduler_status() -> dict:
    """
    Tüm scheduler'ların durumunu getirir
    """
    try:
        status = {
            'product_scheduler': get_product_scheduler_status()
        }
        
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

# Test fonksiyonu
def test_product_scheduler():
    """Product scheduler test fonksiyonu"""
    logging.info("Product scheduler test başlatılıyor...")
    
    # Durumu kontrol et
    status = get_product_scheduler_status()
    print("Mevcut durum:", status)
    
    # Başlat
    success = start_product_scheduler()
    print("Başlatma sonucu:", success)
    
    # Tekrar durum kontrol et
    status = get_product_scheduler_status()
    print("Yeni durum:", status)
    
    # İstatistikleri göster
    stats = get_product_scheduler_statistics()
    print("İstatistikler:", stats)
    
    return status, stats

# Uygulama başladığında otomatik olarak scheduler'ı başlat
if __name__ != "__main__":
    # Sadece modül import edildiğinde çalıştır
    try:
        # 5 saniye bekle ki diğer modüller yüklensin
        def delayed_init():
            time.sleep(5)
            init_product_scheduler()
        
        init_thread = threading.Thread(target=delayed_init)
        init_thread.daemon = True
        init_thread.start()
        
    except Exception as e:
        logging.error(f"Product scheduler otomatik başlatma hatası: {str(e)}")