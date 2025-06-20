"""
Rakip Fiyat Takip Modülü - Otomatik Güncelleme Scheduler
Belirlenen saatte otomatik veri çekme işlemlerini yönetir
YENİ: Slot 0 (NeşeliÇiçekler) desteği eklendi
"""

import schedule
import time
import threading
import logging
from datetime import datetime
import pytz
from competitor_tracking import get_competitor_settings
from competitor_scraper import start_scheduled_update, is_scraping_running

# Global scheduler durumu - YENİ: Slot 0 bilgileri eklendi
scheduler_status = {
    'is_running': False,
    'current_schedule': None,
    'next_run': None,
    'last_run': None,
    'thread': None,
    'include_slot_0': True,  # YENİ: Varsayılan olarak slot 0 dahil
    'last_run_stats': None   # YENİ: Son çalışma istatistikleri
}

scheduler_lock = threading.Lock()

def get_turkey_time():
    """Türkiye saatini döndürür"""
    turkey_tz = pytz.timezone('Europe/Istanbul')
    return datetime.now(turkey_tz)

def scheduled_job():
    """
    Zamanlanmış iş - otomatik güncelleme başlatır
    YENİ: Slot 0 (NeşeliÇiçekler) dahil edilir
    """
    try:
        current_time = get_turkey_time()
        
        # YENİ: Slot 0 dahil mi kontrol et
        include_slot_0 = scheduler_status.get('include_slot_0', True)
        slot_info = "NeşeliÇiçekler dahil" if include_slot_0 else "sadece rakipler"
        
        logging.info(f"Otomatik güncelleme tetiklendi ({slot_info}): {current_time.strftime('%d.%m.%Y %H:%M:%S')}")
        
        # Eğer manuel scraping devam ediyorsa atla
        if is_scraping_running():
            logging.warning("Manuel scraping devam ediyor, otomatik güncelleme atlandı")
            return
        
        # YENİ: Otomatik güncelleme başlat (slot 0 dahil)
        success = start_scheduled_update("scheduler", include_slot_0=include_slot_0)
        
        if success:
            with scheduler_lock:
                scheduler_status['last_run'] = current_time.strftime('%d.%m.%Y %H:%M:%S')
                
            slot_msg = f"başlatıldı ({slot_info})"
            logging.info(f"Otomatik güncelleme {slot_msg}")
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

def start_scheduler(include_slot_0: bool = True):
    """
    Scheduler'ı başlatır
    YENİ: include_slot_0 parametresi eklendi
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
            
            # YENİ: Slot 0 ayarını kontrol et
            include_neseli_setting = settings.get('include_neselicicekler_in_scraping', 'true')
            if include_neseli_setting.lower() == 'false':
                include_slot_0 = False
            
            # Mevcut schedule'ları temizle
            schedule.clear()
            
            # Yeni schedule ekle
            schedule.every().day.at(schedule_time).do(scheduled_job)
            
            # Scheduler durumunu güncelle
            scheduler_status['is_running'] = True
            scheduler_status['current_schedule'] = schedule_time
            scheduler_status['include_slot_0'] = include_slot_0
            
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
            
            slot_info = "NeşeliÇiçekler dahil" if include_slot_0 else "sadece rakipler"
            logging.info(f"Scheduler başlatıldı ({slot_info}): Günlük {schedule_time} - Sonraki çalışma: {scheduler_status['next_run']}")
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

def update_scheduler(new_schedule_time: str, include_slot_0: bool = None):
    """
    Scheduler'ın çalışma saatini günceller
    YENİ: include_slot_0 parametresi eklendi
    """
    try:
        # Mevcut slot 0 ayarını koru (eğer yeni bir değer verilmemişse)
        if include_slot_0 is None:
            include_slot_0 = scheduler_status.get('include_slot_0', True)
        
        # Önce durdur
        stop_scheduler()
        
        # Kısa bekleme
        time.sleep(1)
        
        # Yeni saatlerle başlat
        success = start_scheduler(include_slot_0=include_slot_0)
        
        if success:
            slot_info = "NeşeliÇiçekler dahil" if include_slot_0 else "sadece rakipler"
            logging.info(f"Scheduler güncellendi ({slot_info}): Yeni saat {new_schedule_time}")
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
    YENİ: Slot 0 bilgileri dahil
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
        
        # YENİ: İstatistik bilgileri ekle
        if status['is_running'] and status['include_slot_0']:
            try:
                from competitor_scraper import get_scraping_statistics
                status['stats'] = get_scraping_statistics()
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
    YENİ: Slot 0 bilgileri dahil
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
        
        # YENİ: Slot 0 bilgisi
        include_slot_0 = scheduler_status.get('include_slot_0', True)
        
        return {
            'current_time': current_time.strftime('%d.%m.%Y %H:%M:%S'),
            'next_run': target_time.strftime('%d.%m.%Y %H:%M:%S'),
            'hours_remaining': hours_remaining,
            'minutes_remaining': minutes_remaining,
            'schedule_time': schedule_time,
            'include_slot_0': include_slot_0,
            'slot_info': "NeşeliÇiçekler dahil" if include_slot_0 else "Sadece rakipler"
        }
        
    except Exception as e:
        logging.error(f"Next run info hatası: {str(e)}")
        return {}

# YENİ FONKSİYONLAR: Slot 0 yönetimi

def toggle_neselicicekler_in_scheduler(include: bool, username: str = "system") -> bool:
    """
    NeşeliÇiçekler'in scheduler'a dahil edilip edilmeyeceğini ayarlar
    """
    try:
        from competitor_tracking import save_competitor_settings
        
        # Ayarı kaydet
        current_time = datetime.now().isoformat()
        conn = get_db_connection()
        
        conn.execute('''
            INSERT OR REPLACE INTO competitor_settings 
            (setting_key, setting_value, updated_at, updated_by)
            VALUES ('include_neselicicekler_in_scraping', ?, ?, ?)
        ''', ('true' if include else 'false', current_time, username))
        
        conn.commit()
        conn.close()
        
        # Scheduler'ı güncelle
        current_schedule = scheduler_status.get('current_schedule', '09:00')
        success = update_scheduler(current_schedule, include_slot_0=include)
        
        action = "dahil edildi" if include else "hariç tutuldu"
        logging.info(f"NeşeliÇiçekler scheduler'a {action}")
        
        return success
        
    except Exception as e:
        logging.error(f"NeşeliÇiçekler toggle hatası: {str(e)}")
        return False

def get_scheduler_statistics() -> dict:
    """
    Scheduler istatistiklerini getirir
    """
    try:
        from competitor_tracking import get_total_links_count, get_total_prices_count, get_neselicicekler_price_stats
        
        stats = {
            'scheduler_status': get_scheduler_status(),
            'total_links': {
                'all': get_total_links_count(include_slot_0=True),
                'competitors': get_total_links_count(include_slot_0=False),
                'neselicicekler': get_total_links_count(slot_0_only=True)
            },
            'total_prices': {
                'all': get_total_prices_count(include_slot_0=True),
                'competitors': get_total_prices_count(include_slot_0=False),
                'neselicicekler': get_total_prices_count(slot_0_only=True)
            },
            'neselicicekler_stats': get_neselicicekler_price_stats(),
            'next_run_info': get_next_run_info()
        }
        
        return stats
        
    except Exception as e:
        logging.error(f"Scheduler istatistik hatası: {str(e)}")
        return {}

def schedule_immediate_neselicicekler_update():
    """
    NeşeliÇiçekler için anlık güncelleme zamanlar
    Normal schedule'dan bağımsız
    """
    def immediate_job():
        try:
            from competitor_scraper import start_neselicicekler_only_update
            logging.info("Anlık NeşeliÇiçekler güncellemesi başlatılıyor")
            start_neselicicekler_only_update("immediate_scheduler")
        except Exception as e:
            logging.error(f"Anlık NeşeliÇiçekler güncellemesi hatası: {str(e)}")
    
    # 5 saniye sonra çalıştır
    schedule.every(5).seconds.do(immediate_job).tag('immediate_neseli')
    
    logging.info("Anlık NeşeliÇiçekler güncellemesi 5 saniye sonra başlayacak")

def cancel_immediate_jobs():
    """
    Anlık işleri iptal eder
    """
    schedule.clear('immediate_neseli')
    logging.info("Anlık işler iptal edildi")

# Uygulama başlarken scheduler'ı otomatik başlat
def init_scheduler():
    """
    Uygulama başladığında scheduler'ı başlatır
    YENİ: Slot 0 ayarını kontrol eder
    """
    try:
        # Ayarları kontrol et
        settings = get_competitor_settings()
        if 'schedule_time' in settings:
            
            # YENİ: Slot 0 ayarını kontrol et
            include_slot_0 = True
            if 'include_neselicicekler_in_scraping' in settings:
                include_slot_0 = settings['include_neselicicekler_in_scraping'].lower() == 'true'
            
            success = start_scheduler(include_slot_0=include_slot_0)
            if success:
                slot_info = "NeşeliÇiçekler dahil" if include_slot_0 else "sadece rakipler"
                logging.info(f"Scheduler otomatik olarak başlatıldı ({slot_info})")
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
        cancel_immediate_jobs()
        logging.info("Scheduler temizlendi")
    except Exception as e:
        logging.error(f"Scheduler cleanup hatası: {str(e)}")

# YENİ: get_db_connection import'u
def get_db_connection():
    """Import competitor_tracking get_db_connection"""
    from competitor_tracking import get_db_connection as get_conn
    return get_conn()