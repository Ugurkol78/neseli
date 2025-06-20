"""
Rakip Fiyat Takip Modülü - Veritabanı ve İş Mantığı
SQLite veritabanı yönetimi ve core fonksiyonlar
YENİ: Slot 0 (NeşeliÇiçekler) desteği eklendi
"""

import sqlite3
import json
import os
from datetime import datetime
import logging
import re
import time
from typing import List, Dict, Optional, Tuple
import threading

# Veritabanı dosya yolu
COMPETITOR_DB_PATH = 'competitor_tracking.db'

# Thread-safe veritabanı erişimi için lock
db_lock = threading.Lock()

def get_db_connection():
    """SQLite veritabanı bağlantısı oluşturur - thread safe"""
    with db_lock:
        # Timeout ekle ve WAL mode kullan
        conn = sqlite3.connect(COMPETITOR_DB_PATH, timeout=30.0)
        conn.row_factory = sqlite3.Row  # Dict-like access
        
        # WAL mode'u etkinleştir (daha iyi concurrency)
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA cache_size=1000;')
        conn.execute('PRAGMA temp_store=memory;')
        
        return conn

def safe_db_operation(operation_func, *args, **kwargs):
    """
    Veritabanı işlemlerini güvenli şekilde yapar
    Retry mekanizması ile database lock hatalarını çözer
    """
    max_retries = 3
    retry_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            return operation_func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                logging.warning(f"Database locked, retry {attempt + 1}/{max_retries}")
                time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                continue
            else:
                logging.error(f"Database operation failed: {str(e)}")
                raise
        except Exception as e:
            logging.error(f"Unexpected database error: {str(e)}")
            raise
    
    raise sqlite3.OperationalError("Max retries exceeded for database operation")

def init_competitor_database():
    """
    Rakip takip için gerekli tabloları oluşturur
    YENİ: Slot 0 desteği ile güncellenmiş constraint'ler
    """
    conn = get_db_connection()
    try:
        # 1. Competitor Links Tablosu - YENİ: Slot 0 desteği
        conn.execute('''
            CREATE TABLE IF NOT EXISTS competitor_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                slot_number INTEGER NOT NULL CHECK(slot_number >= 0 AND slot_number <= 5),
                competitor_url TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT,
                updated_by TEXT,
                notes TEXT,
                UNIQUE(barcode, slot_number)
            )
        ''')
        
        # 2. Competitor Prices Tablosu - YENİ: Slot 0 desteği
        conn.execute('''
            CREATE TABLE IF NOT EXISTS competitor_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                competitor_url TEXT NOT NULL,
                slot_number INTEGER NOT NULL CHECK(slot_number >= 0 AND slot_number <= 5),
                scrape_datetime TEXT NOT NULL,
                scraped_by TEXT NOT NULL,
                product_name TEXT,
                price REAL,
                seller_name TEXT,
                status TEXT DEFAULT 'A',
                scrape_source TEXT DEFAULT 'manual',
                FOREIGN KEY (barcode, slot_number) REFERENCES competitor_links (barcode, slot_number)
            )
        ''')
        
        # 3. Competitor Settings Tablosu - Değişiklik yok
        conn.execute('''
            CREATE TABLE IF NOT EXISTS competitor_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )
        ''')
        
        # Index'leri oluştur - YENİ: Slot 0 dahil
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_links_barcode ON competitor_links(barcode)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_links_slot ON competitor_links(slot_number)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_links_active ON competitor_links(is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_barcode ON competitor_prices(barcode)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_slot ON competitor_prices(slot_number)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_status ON competitor_prices(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_datetime ON competitor_prices(scrape_datetime)')
        
        # Varsayılan ayarları ekle
        conn.execute('''
            INSERT OR IGNORE INTO competitor_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', '09:00', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        # YENİ: NeşeliÇiçekler için özel ayar
        conn.execute('''
            INSERT OR IGNORE INTO competitor_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('include_neselicicekler_in_scraping', 'true', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        conn.commit()
        logging.info("Competitor tracking veritabanı başarıyla oluşturuldu (Slot 0 desteği ile)")
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Veritabanı oluşturma hatası: {str(e)}")
        raise
    finally:
        conn.close()

def check_tables_exist() -> bool:
    """Gerekli tabloların var olup olmadığını kontrol eder"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name IN ('competitor_links', 'competitor_prices', 'competitor_settings')
        ''')
        tables = cursor.fetchall()
        return len(tables) == 3
    except Exception as e:
        logging.error(f"Tablo kontrolü hatası: {str(e)}")
        return False
    finally:
        conn.close()

def validate_trendyol_links(links: List[str]) -> Tuple[bool, List[str]]:
    """
    Trendyol linklerini doğrular
    Returns: (is_valid, invalid_links)
    """
    invalid_links = []
    trendyol_pattern = re.compile(r'https?://(?:www\.)?trendyol\.com/.*')
    
    for link in links:
        if link.strip():  # Boş olmayan linkler için
            if not trendyol_pattern.match(link.strip()):
                invalid_links.append(link)
    
    return len(invalid_links) == 0, invalid_links

# competitor_tracking.py dosyasında get_links_by_barcode fonksiyonunu şununla değiştirin:

def get_links_by_barcode(barcode: str, include_slot_0: bool = False) -> List[Dict]:
    """Barcode için linkleri getir - DEBUG TEMIZLENDI"""
    conn = get_db_connection()
    try:
        if include_slot_0:
            slot_condition = "slot_number >= 0 AND slot_number <= 5"
        else:
            slot_condition = "slot_number >= 1 AND slot_number <= 5"
        
        cursor = conn.execute(f'''
            SELECT slot_number, competitor_url, is_active, created_at, updated_at
            FROM competitor_links 
            WHERE barcode = ? AND is_active = 1 AND ({slot_condition})
            ORDER BY slot_number
        ''', (barcode,))
        
        links = []
        for row in cursor.fetchall():
            links.append({
                'slot_number': row['slot_number'],
                'url': row['competitor_url'],
                'is_active': bool(row['is_active']),
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            })
        
        return links
        
    except Exception as e:
        logging.error(f"Link getirme hatası - {barcode}: {str(e)}")
        return []
    finally:
        conn.close()

def save_links(barcode: str, links: List[str], username: str) -> bool:
    """
    Barkod için link listesini kaydeder - ESKİ FONKSİYON
    Slot bazında güncelleme yapar (slot 1-5)
    """
    slot_links = {}
    for i, link_url in enumerate(links):
        slot_number = i + 1  # Index 0 = slot 1, index 1 = slot 2, vs.
        if link_url and link_url.strip():
            slot_links[slot_number] = link_url.strip()
    
    return save_links_by_slots(barcode, slot_links, username, include_slot_0=False)

def save_links_by_slots(barcode: str, slot_links: Dict[int, str], username: str, include_slot_0: bool = False) -> bool:
    """
    YENİ: Slot numaraları ile link kaydetme fonksiyonu
    slot_links: {slot_number: url} format
    include_slot_0: Slot 0 işlensin mi?
    """
    def _save_links_operation():
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Slot aralığını belirle
            if include_slot_0:
                valid_slots = range(0, 6)  # 0-5
                slot_name = "slot 0-5"
            else:
                valid_slots = range(1, 6)  # 1-5
                slot_name = "slot 1-5"
            
            # Önce bu barkod için mevcut linkleri al
            existing_links = get_links_by_barcode(barcode, include_slot_0=include_slot_0)
            existing_slots = {link['slot_number']: link['url'] for link in existing_links}
            
            # Slot işlemleri
            processed_slots = set()
            
            for slot_number, new_url in slot_links.items():
                if slot_number not in valid_slots:
                    logging.warning(f"Geçersiz slot numarası: {slot_number} ({slot_name} aralığında olmalı)")
                    continue
                
                processed_slots.add(slot_number)
                new_url = new_url.strip() if new_url else ''
                
                if not new_url:
                    # Boş link - mevcut slot varsa pasif yap
                    if slot_number in existing_slots:
                        conn.execute('''
                            UPDATE competitor_links 
                            SET is_active = 0, updated_at = ?, updated_by = ?
                            WHERE barcode = ? AND slot_number = ?
                        ''', (current_time, username, barcode, slot_number))
                        
                        # İlgili fiyat kayıtlarını da pasif yap
                        conn.execute('''
                            UPDATE competitor_prices 
                            SET status = 'P'
                            WHERE barcode = ? AND slot_number = ?
                        ''', (barcode, slot_number))
                        
                        logging.info(f"Slot {slot_number} pasif yapıldı: {barcode}")
                    continue
                
                # Aynı barkod için aynı link var mı kontrol et (farklı slotlarda)
                cursor = conn.execute('''
                    SELECT slot_number FROM competitor_links 
                    WHERE barcode = ? AND competitor_url = ? AND is_active = 1 AND slot_number != ?
                ''', (barcode, new_url, slot_number))
                
                existing_same_url = cursor.fetchone()
                if existing_same_url:
                    logging.warning(f"Aynı link farklı slotta mevcut: {barcode} - {new_url} (Slot {existing_same_url['slot_number']})")
                    continue
                
                # Link kaydı/güncelleme
                if slot_number in existing_slots:
                    if existing_slots[slot_number] != new_url:
                        # Mevcut slot güncelleniyor
                        conn.execute('''
                            UPDATE competitor_links 
                            SET competitor_url = ?, updated_at = ?, updated_by = ?, is_active = 1
                            WHERE barcode = ? AND slot_number = ?
                        ''', (new_url, current_time, username, barcode, slot_number))
                        
                        # Eski URL'nin fiyat kayıtlarını pasif yap
                        conn.execute('''
                            UPDATE competitor_prices 
                            SET status = 'P'
                            WHERE barcode = ? AND slot_number = ?
                        ''', (barcode, slot_number))
                        
                        logging.info(f"Slot {slot_number} güncellendi: {barcode} -> {new_url}")
                else:

                    # Yeni slot ekleniyor
                    conn.execute('''
                        INSERT OR REPLACE INTO competitor_links 
                        (barcode, slot_number, competitor_url, is_active, created_at, created_by)
                        VALUES (?, ?, ?, 1, ?, ?)
                    ''', (barcode, slot_number, new_url, current_time, username))
                    
                    logging.info(f"Yeni slot {slot_number} eklendi: {barcode} -> {new_url}")
            
            # İşlenmeyen slotları pasif yap (eğer mevcut ise)
            for slot_number in valid_slots:
                if slot_number not in processed_slots and slot_number in existing_slots:
                    conn.execute('''
                        UPDATE competitor_links 
                        SET is_active = 0, updated_at = ?, updated_by = ?
                        WHERE barcode = ? AND slot_number = ?
                    ''', (current_time, username, barcode, slot_number))
                    
                    conn.execute('''
                        UPDATE competitor_prices 
                        SET status = 'P'
                        WHERE barcode = ? AND slot_number = ?
                    ''', (barcode, slot_number))
                    
                    logging.info(f"İşlenmeyen slot {slot_number} pasif yapıldı: {barcode}")
            
            conn.commit()
            
            slot_info = "NeşeliÇiçekler dahil " if include_slot_0 else ""
            logging.info(f"Linkler başarıyla kaydedildi: {barcode} ({slot_info}{len([url for url in slot_links.values() if url])} aktif link)")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Link kaydetme hatası - {barcode}: {str(e)}")
            return False
        finally:
            conn.close()
    
    return safe_db_operation(_save_links_operation)

def get_active_prices_by_barcode(barcode: str, exclude_slot_0: bool = False) -> List[Dict]:
    """
    Belirli bir barkod için en son aktif fiyat verilerini slot sırasına göre getirir
    YENİ: exclude_slot_0 parametresi eklendi - DÜZELTME: notes kolonu kaldırıldı
    """
    conn = get_db_connection()
    try:
        if exclude_slot_0:
            # Slot 0 hariç (rakip fiyatlar için)
            slot_condition = "p.slot_number >= 1 AND p.slot_number <= 5"
        else:
            # Tüm slotlar (0-5)
            slot_condition = "p.slot_number >= 0 AND p.slot_number <= 5"
        
        # DÜZELTME: notes kaldırıldı
        cursor = conn.execute(f'''
            SELECT p.slot_number, p.product_name, p.price, p.seller_name, 
                   p.scrape_datetime, l.competitor_url
            FROM competitor_prices p
            INNER JOIN competitor_links l ON p.barcode = l.barcode AND p.slot_number = l.slot_number
            WHERE p.barcode = ? AND p.status = 'A' AND l.is_active = 1 AND ({slot_condition})
            ORDER BY p.slot_number, p.scrape_datetime DESC
        ''', (barcode,))
        
        # Slot başına sadece en son kaydı al
        prices = []
        seen_slots = set()
        
        for row in cursor.fetchall():
            if row['slot_number'] not in seen_slots:
                prices.append({
                    'slot_number': row['slot_number'],
                    'product_name': row['product_name'],
                    'price': row['price'],
                    'seller_name': row['seller_name'],
                    'scrape_datetime': row['scrape_datetime'],
                    'competitor_url': row['competitor_url']
                })
                seen_slots.add(row['slot_number'])
        
        return prices
        
    except Exception as e:
        logging.error(f"Fiyat getirme hatası - {barcode}: {str(e)}")
        return []
    finally:
        conn.close()

def save_scraped_price(barcode: str, competitor_url: str, slot_number: int, 
                      product_name: str, price: float, seller_name: str, 
                      scraped_by: str) -> bool:  # scrape_source kaldırıldı
    """
    Scraping ile elde edilen fiyat verisini kaydeder
    YENİ: slot_number 0-5 aralığında olabilir, scrape_source eklendi
    """
    if not (0 <= slot_number <= 5):
        logging.error(f"Geçersiz slot numarası: {slot_number} (0-5 aralığında olmalı)")
        return False
    
    conn = get_db_connection()
    try:
        current_time = datetime.now().isoformat()
        
        conn.execute('''
            INSERT INTO competitor_prices 
            (barcode, competitor_url, slot_number, scrape_datetime, scraped_by, 
             product_name, price, seller_name, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'A')
        ''', (barcode, competitor_url, slot_number, current_time, scraped_by, 
              product_name, price, seller_name))
        
        conn.commit()
        
        slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Rakip Slot {slot_number}"
        logging.info(f"Fiyat verisi kaydedildi: {barcode} - {slot_info} - {price}₺")
        return True
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Fiyat kaydetme hatası: {str(e)}")
        return False
    finally:
        conn.close()

def get_all_active_links(include_slot_0: bool = False) -> List[Dict]:
    """
    Tüm aktif linkleri getirir (otomatik güncelleme için)
    YENİ: include_slot_0 parametresi eklendi
    """
    conn = get_db_connection()
    try:
        if include_slot_0:
            slot_condition = "slot_number >= 0 AND slot_number <= 5"
        else:
            slot_condition = "slot_number >= 1 AND slot_number <= 5"
        
        cursor = conn.execute(f'''
            SELECT barcode, slot_number, competitor_url
            FROM competitor_links 
            WHERE is_active = 1 AND ({slot_condition})
            ORDER BY barcode, slot_number
        ''')
        
        links = []
        for row in cursor.fetchall():
            links.append({
                'barcode': row['barcode'],
                'slot_number': row['slot_number'],
                'url': row['competitor_url']
            })
        
        return links
        
    except Exception as e:
        logging.error(f"Aktif link getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def get_last_update(include_slot_0: bool = False) -> Optional[str]:
    """
    Son güncelleme zamanını getirir
    YENİ: include_slot_0 parametresi eklendi
    """
    conn = get_db_connection()
    try:
        if include_slot_0:
            slot_condition = "slot_number >= 0 AND slot_number <= 5"
        else:
            slot_condition = "slot_number >= 1 AND slot_number <= 5"
        
        cursor = conn.execute(f'''
            SELECT MAX(scrape_datetime) as last_update
            FROM competitor_prices 
            WHERE status = 'A' AND ({slot_condition})
        ''')
        
        result = cursor.fetchone()
        return result['last_update'] if result and result['last_update'] else None
        
    except Exception as e:
        logging.error(f"Son güncelleme zamanı getirme hatası: {str(e)}")
        return None
    finally:
        conn.close()

def get_competitor_settings() -> Dict:
    """Rakip takip ayarlarını getirir"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT setting_key, setting_value
            FROM competitor_settings
        ''')
        
        settings = {}
        for row in cursor.fetchall():
            settings[row['setting_key']] = row['setting_value']
        
        return settings
        
    except Exception as e:
        logging.error(f"Ayar getirme hatası: {str(e)}")
        return {}
    finally:
        conn.close()

def save_competitor_settings(schedule_time: str, username: str) -> bool:
    """Rakip takip ayarlarını kaydeder"""
    conn = get_db_connection()
    try:
        current_time = datetime.now().isoformat()
        
        conn.execute('''
            INSERT OR REPLACE INTO competitor_settings 
            (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', ?, ?, ?)
        ''', (schedule_time, current_time, username))
        
        conn.commit()
        logging.info(f"Ayarlar kaydedildi: schedule_time = {schedule_time}")
        return True
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Ayar kaydetme hatası: {str(e)}")
        return False
    finally:
        conn.close()

def get_total_links_count(include_slot_0: bool = False, slot_0_only: bool = False) -> int:
    """
    Toplam aktif link sayısını getirir
    YENİ: include_slot_0 ve slot_0_only parametreleri eklendi
    """
    conn = get_db_connection()
    try:
        if slot_0_only:
            # Sadece slot 0
            condition = "slot_number = 0"
        elif include_slot_0:
            # Slot 0-5
            condition = "slot_number >= 0 AND slot_number <= 5"
        else:
            # Slot 1-5 (eski davranış)
            condition = "slot_number >= 1 AND slot_number <= 5"
        
        cursor = conn.execute(f'SELECT COUNT(*) as count FROM competitor_links WHERE is_active = 1 AND ({condition})')
        result = cursor.fetchone()
        return result['count'] if result else 0
    except Exception as e:
        logging.error(f"Link sayısı getirme hatası: {str(e)}")
        return 0
    finally:
        conn.close()

def get_total_prices_count(include_slot_0: bool = False, slot_0_only: bool = False) -> int:
    """
    Toplam aktif fiyat kaydı sayısını getirir
    YENİ: include_slot_0 ve slot_0_only parametreleri eklendi
    """
    conn = get_db_connection()
    try:
        if slot_0_only:
            # Sadece slot 0
            condition = "slot_number = 0"
        elif include_slot_0:
            # Slot 0-5
            condition = "slot_number >= 0 AND slot_number <= 5"
        else:
            # Slot 1-5 (eski davranış)  
            condition = "slot_number >= 1 AND slot_number <= 5"
        
        cursor = conn.execute(f'SELECT COUNT(*) as count FROM competitor_prices WHERE status = "A" AND ({condition})')
        result = cursor.fetchone()
        return result['count'] if result else 0
    except Exception as e:
        logging.error(f"Fiyat sayısı getirme hatası: {str(e)}")
        return 0
    finally:
        conn.close()

# YENİ FONKSİYONLAR: NeşeliÇiçekler özel işlemleri

def get_neselicicekler_links() -> List[Dict]:
    """
    Sadece NeşeliÇiçekler linklerini getirir (slot 0)
    """
    return get_all_active_links_by_slot(0)

def get_all_active_links_by_slot(slot_number: int) -> List[Dict]:
    """
    Belirli bir slot numarasındaki tüm aktif linkleri getirir
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT barcode, slot_number, competitor_url, notes, created_at
            FROM competitor_links 
            WHERE is_active = 1 AND slot_number = ?
            ORDER BY barcode
        ''', (slot_number,))
        
        links = []
        for row in cursor.fetchall():
            links.append({
                'barcode': row['barcode'],
                'slot_number': row['slot_number'],
                'url': row['competitor_url'],
                'notes': row['notes'],
                'created_at': row['created_at']
            })
        
        return links
        
    except Exception as e:
        logging.error(f"Slot {slot_number} link getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def get_neselicicekler_price_stats() -> Dict:
    """
    NeşeliÇiçekler (slot 0) fiyat istatistiklerini getirir
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT barcode) as unique_products,
                AVG(price) as avg_price,
                MIN(price) as min_price,
                MAX(price) as max_price,
                MAX(scrape_datetime) as last_scrape
            FROM competitor_prices 
            WHERE status = 'A' AND slot_number = 0
        ''')
        
        result = cursor.fetchone()
        if result:
            return {
                'total_records': result['total_records'],
                'unique_products': result['unique_products'],
                'avg_price': round(result['avg_price'], 2) if result['avg_price'] else 0,
                'min_price': result['min_price'],
                'max_price': result['max_price'],
                'last_scrape': result['last_scrape']
            }
        else:
            return {
                'total_records': 0,
                'unique_products': 0,
                'avg_price': 0,
                'min_price': None,
                'max_price': None,
                'last_scrape': None
            }
        
    except Exception as e:
        logging.error(f"NeşeliÇiçekler istatistik hatası: {str(e)}")
        return {}
    finally:
        conn.close()

def cleanup_old_price_data(days_to_keep: int = 90):
    """
    Eski fiyat verilerini temizler
    YENİ: Slot 0 için farklı tutma süresi olabilir
    """
    conn = get_db_connection()
    try:
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        cutoff_iso = cutoff_date.isoformat()
        
        # Eski kayıtları sil
        cursor = conn.execute('''
            DELETE FROM competitor_prices 
            WHERE datetime(scrape_datetime) < datetime(?)
        ''', (cutoff_iso,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        if deleted_count > 0:
            logging.info(f"Eski fiyat kayıtları temizlendi: {deleted_count} kayıt silindi ({days_to_keep} günden eski)")
        
        return deleted_count
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Fiyat temizleme hatası: {str(e)}")
        return 0
    finally:
        conn.close()

# İlk kurulum
if not os.path.exists(COMPETITOR_DB_PATH):
    init_competitor_database()
    logging.info("Competitor tracking veritabanı ilk kez oluşturuldu (Slot 0 desteği ile)")