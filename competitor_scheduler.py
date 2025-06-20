"""
Rakip Fiyat Takip Modülü - Otomatik Güncelleme Scheduler
Belirlenen saatte otomatik veri çekme işlemlerini yönetir
"""

import schedule
import time
import threading
import logging
from datetime import datetime
import pytz
from competitor_tracking import get_competitor_settings
from competitor_scraper import start_scheduled_update, is_scraping_running

# Global scheduler durumu
scheduler_status = {
    'is_running': False,
    'current_schedule': None,
    'next_run': None,
    'last_run': None,
    'thread': None
}

scheduler_lock = threading.Lock()

def get_turkey_time():
    """Türkiye saatini döndürür"""
    turkey_tz = pytz.timezone('Europe/Istanbul')
    return datetime.now(turkey_tz)

def scheduled_job():
    """
    Zamanlanmış iş - otomatik güncelleme başlatır
    """
    try:
        current_time = get_turkey_time()
        logging.info(f"Otomatik güncelleme tetiklendi: {current_time.strftime('%d.%m.%Y %H:%M:%S')}")
        
        # Eğer manuel scraping devam ediyorsa atla
        if is_scraping_running():
            logging.warning("Manuel scraping devam ediyor, otomatik güncelleme atlandı")
            return
        
        # Otomatik güncelleme başlat
        success = start_scheduled_update("scheduler")
        
        if success:
            with scheduler_lock:
                scheduler_status['last_run'] = current_time.strftime('%d.%m.%Y %H:%M:%S')
            logging.info("Otomatik güncelleme başarıyla başlatıldı")
        else:
            logging.error("Otomatik güncelleme başlatılamadı")
            
    except Exception as e:
        logging.error(f"Scheduled job hatası: {str(e)}")

def scheduler_worker():
    """
    Scheduler'ın sürekli çalışması için worker thread
    """
    while True:
        try:
            with scheduler_lock:
                if not scheduler_status['is_running']:
                    break
                    
            schedule.run_pending()
            time.sleep(60)  # Her dakika kontrol et
            
        except Exception as e:
            logging.error(f"Scheduler worker hatası: {str(e)}")
            time.sleep(60)

def start_scheduler():
    """
    Scheduler'ı başlatır
    """
    global scheduler_status
    
    try:
        with scheduler_lock:
            if scheduler_status['is_running']:
                logging.warning("Scheduler zaten çalışıyor")
                return True
            
            # Mevcut ayarları al
            settings = get_competitor_settings()
            schedule_time = settings.get('schedule_time', '09:00')
            
            # Mevcut schedule'ları temizle
            schedule.clear()
            
            # Yeni schedule ekle
            schedule.every().day.at(schedule_time).do(scheduled_job)
            
            # Scheduler durumunu güncelle
            scheduler_status['is_running'] = True
            scheduler_status['current_schedule'] = schedule_time
            
            # Bir sonraki çalışma zamanını hesapla
            next_run = schedule.next_run()
            if next_run:
                # UTC'den Türkiye saatine çevir
                turkey_tz = pytz.timezone('Europe/Istanbul')
                next_run_turkey = next_run.replace(tzinfo=pytz.UTC).astimezone(turkey_tz)
                scheduler_status['next_run'] = next_run_turkey.strftime('%d.%m.%Y %H:%M:%S')
            
            # Worker thread başlat
            worker_thread = threading.Thread(target=scheduler_worker)
            worker_thread.daemon = True
            worker_thread.start()
            
            scheduler_status['thread'] = worker_thread
            
            logging.info(f"Scheduler başlatıldı: Günlük {schedule_time} - Sonraki çalışma: {scheduler_status['next_run']}")
            return True
            
    except Exception as e:
        logging.error(f"Scheduler başlatma hatası: {str(e)}")
        return False

def stop_scheduler():
    """
    Scheduler'ı durdurur
    """
    global scheduler_status
    
    try:
        with scheduler_lock:
            if not scheduler_status['is_running']:
                logging.warning("Scheduler zaten durmuş")
                return True
            
            # Schedule'ları temizle
            schedule.clear()
            
            # Durumu güncelle
            scheduler_status['is_running'] = False
            scheduler_status['current_schedule'] = None
            scheduler_status['next_run'] = None
            scheduler_status['thread'] = None
            
            logging.info("Scheduler durduruldu")
            return True
            
    except Exception as e:
        logging.error(f"Scheduler durdurma hatası: {str(e)}")
        return False

def update_scheduler(new_schedule_time: str):
    """
    Scheduler'ın çalışma saatini günceller
    """
    try:
        # Önce durdur
        stop_scheduler()
        
        # Kısa bekleme
        time.sleep(1)
        
        # Yeni saatlerle başlat
        success = start_scheduler()
        
        if success:
            logging.info(f"Scheduler güncellendi: Yeni saat {new_schedule_time}")
            return True
        else:
            logging.error("Scheduler güncellenemedi")
            return False
            
    except Exception as e:
        logging.error(f"Scheduler güncelleme hatası: {str(e)}")
        return False

def get_scheduler_status() -> dict:
    """
    Mevcut scheduler durumunu döndürür
    """
    with scheduler_lock:
        status = scheduler_status.copy()
        
        # Eğer çalışıyorsa sonraki çalışma zamanını güncelle
        if status['is_running']:
            try:
                next_run = schedule.next_run()
                if next_run:
                    turkey_tz = pytz.timezone('Europe/Istanbul')
                    next_run_turkey = next_run.replace(tzinfo=pytz.UTC).astimezone(turkey_tz)
                    status['next_run'] = next_run_turkey.strftime('%d.%m.%Y %H:%M:%S')
            except:
                pass
        
        return status

def validate_schedule_time(time_str: str) -> bool:
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

def get_next_run_info() -> dict:
    """
    Sonraki çalışma hakkında detaylı bilgi döndürür
    """
    try:
        current_time = get_turkey_time()
        settings = get_competitor_settings()
        schedule_time = settings.get('schedule_time', '09:00')
        
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
        logging.error(f"Next run info hatası: {str(e)}")
        return {}

# Uygulama başlarken scheduler'ı otomatik başlat
def init_scheduler():
    """
    Uygulama başladığında scheduler'ı başlatır
    """
    try:
        # Ayarları kontrol et
        settings = get_competitor_settings()
        if 'schedule_time' in settings:
            success = start_scheduler()
            if success:
                logging.info("Scheduler otomatik olarak başlatıldı")
            else:
                logging.error("Scheduler otomatik başlatılamadı")
        else:
            logging.info("Schedule ayarı bulunamadı, scheduler başlatılmadı")
            
    except Exception as e:
        logging.error(f"Scheduler init hatası: {str(e)}")

# Uygulama kapatılırken scheduler'ı durdur
def cleanup_scheduler():
    """
    Uygulama kapatılırken scheduler'ı temizler
    """
    try:
        stop_scheduler()
        logging.info("Scheduler temizlendi")
    except Exception as e:
        logging.error(f"Scheduler cleanup hatası: {str(e)}")