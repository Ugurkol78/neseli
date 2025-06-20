"""
Rakip Fiyat Takip Modülü - Veritabanı ve İş Mantığı
SQLite veritabanı yönetimi ve core fonksiyonlar
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
    İlk kurulumda çağrılır
    """
    conn = get_db_connection()
    try:
        # 1. Competitor Links Tablosu - Barkod ve slot bazında link tutma
        conn.execute('''
            CREATE TABLE IF NOT EXISTS competitor_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                slot_number INTEGER NOT NULL,
                competitor_url TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT,
                updated_by TEXT,
                UNIQUE(barcode, slot_number)
            )
        ''')
        
        # 2. Competitor Prices Tablosu - Fiyat geçmişi
        conn.execute('''
            CREATE TABLE IF NOT EXISTS competitor_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                competitor_url TEXT NOT NULL,
                slot_number INTEGER NOT NULL,
                scrape_datetime TEXT NOT NULL,
                scraped_by TEXT NOT NULL,
                product_name TEXT,
                price REAL,
                seller_name TEXT,
                status TEXT DEFAULT 'A',
                FOREIGN KEY (barcode, slot_number) REFERENCES competitor_links (barcode, slot_number)
            )
        ''')
        
        # 3. Competitor Settings Tablosu - Sistem ayarları
        conn.execute('''
            CREATE TABLE IF NOT EXISTS competitor_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )
        ''')
        
        # Index'leri oluştur
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_links_barcode ON competitor_links(barcode)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_barcode ON competitor_prices(barcode)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_status ON competitor_prices(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_competitor_prices_datetime ON competitor_prices(scrape_datetime)')
        
        # Varsayılan ayarları ekle
        conn.execute('''
            INSERT OR IGNORE INTO competitor_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', '09:00', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        conn.commit()
        logging.info("Competitor tracking veritabanı başarıyla oluşturuldu")
        
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

def get_links_by_barcode(barcode: str) -> List[Dict]:
    """
    Belirli bir barkod için kayıtlı linkleri slot sırasına göre getirir
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT slot_number, competitor_url, is_active, created_at, updated_at
            FROM competitor_links 
            WHERE barcode = ? AND is_active = 1
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
    Barkod için link listesini kaydeder
    Slot bazında güncelleme yapar
    """
    def _save_links_operation():
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Önce bu barkod için mevcut linkleri al
            existing_links = get_links_by_barcode(barcode)
            existing_slots = {link['slot_number']: link['url'] for link in existing_links}
            
            # Yeni linkler ile karşılaştır
            for slot_number, new_url in enumerate(links, 1):
                new_url = new_url.strip()
                
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
                    continue
                
                # Aynı barkod için aynı link var mı kontrol et (farklı slotlarda)
                cursor = conn.execute('''
                    SELECT slot_number FROM competitor_links 
                    WHERE barcode = ? AND competitor_url = ? AND is_active = 1
                ''', (barcode, new_url))
                
                existing_same_url = cursor.fetchone()
                if existing_same_url and existing_same_url['slot_number'] != slot_number:
                    logging.warning(f"Aynı link farklı slotta mevcut: {barcode} - {new_url}")
                    return False
                
                # Link kaydı/güncelleme
                if slot_number in existing_slots:
                    if existing_slots[slot_number] != new_url:
                        # Mevcut slot güncelleniyor
                        conn.execute('''
                            UPDATE competitor_links 
                            SET competitor_url = ?, updated_at = ?, updated_by = ?
                            WHERE barcode = ? AND slot_number = ?
                        ''', (new_url, current_time, username, barcode, slot_number))
                        
                        # Eski URL'nin fiyat kayıtlarını pasif yap
                        conn.execute('''
                            UPDATE competitor_prices 
                            SET status = 'P'
                            WHERE barcode = ? AND slot_number = ?
                        ''', (barcode, slot_number))
                else:
                    # Yeni slot ekleniyor
                    conn.execute('''
                        INSERT OR REPLACE INTO competitor_links 
                        (barcode, slot_number, competitor_url, is_active, created_at, created_by)
                        VALUES (?, ?, ?, 1, ?, ?)
                    ''', (barcode, slot_number, new_url, current_time, username))
            
            conn.commit()
            logging.info(f"Linkler başarıyla kaydedildi: {barcode}")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Link kaydetme hatası - {barcode}: {str(e)}")
            return False
        finally:
            conn.close()
    
    return safe_db_operation(_save_links_operation)

def get_active_prices_by_barcode(barcode: str) -> List[Dict]:
    """
    Belirli bir barkod için en son aktif fiyat verilerini slot sırasına göre getirir
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT DISTINCT p.slot_number, p.product_name, p.price, p.seller_name, 
                   p.scrape_datetime, l.competitor_url
            FROM competitor_prices p
            INNER JOIN competitor_links l ON p.barcode = l.barcode AND p.slot_number = l.slot_number
            WHERE p.barcode = ? AND p.status = 'A' AND l.is_active = 1
            AND p.scrape_datetime = (
                SELECT MAX(p2.scrape_datetime) 
                FROM competitor_prices p2 
                WHERE p2.barcode = p.barcode AND p2.slot_number = p.slot_number AND p2.status = 'A'
            )
            ORDER BY p.slot_number
        ''', (barcode,))
        
        prices = []
        for row in cursor.fetchall():
            prices.append({
                'slot_number': row['slot_number'],
                'product_name': row['product_name'],
                'price': row['price'],
                'seller_name': row['seller_name'],
                'scrape_datetime': row['scrape_datetime'],
                'competitor_url': row['competitor_url']
            })
        
        return prices
        
    except Exception as e:
        logging.error(f"Fiyat getirme hatası - {barcode}: {str(e)}")
        return []
    finally:
        conn.close()

def save_scraped_price(barcode: str, competitor_url: str, slot_number: int, 
                      product_name: str, price: float, seller_name: str, 
                      scraped_by: str) -> bool:
    """
    Scraping ile elde edilen fiyat verisini kaydeder
    """
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
        logging.info(f"Fiyat verisi kaydedildi: {barcode} - Slot {slot_number} - {price}₺")
        return True
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Fiyat kaydetme hatası: {str(e)}")
        return False
    finally:
        conn.close()

def get_all_active_links() -> List[Dict]:
    """
    Tüm aktif linkleri getirir (otomatik güncelleme için)
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT barcode, slot_number, competitor_url
            FROM competitor_links 
            WHERE is_active = 1
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

def get_last_update() -> Optional[str]:
    """Son güncelleme zamanını getirir"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT MAX(scrape_datetime) as last_update
            FROM competitor_prices 
            WHERE status = 'A'
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

def get_total_links_count() -> int:
    """Toplam aktif link sayısını getirir"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('SELECT COUNT(*) as count FROM competitor_links WHERE is_active = 1')
        result = cursor.fetchone()
        return result['count'] if result else 0
    except Exception as e:
        logging.error(f"Link sayısı getirme hatası: {str(e)}")
        return 0
    finally:
        conn.close()

def get_total_prices_count() -> int:
    """Toplam aktif fiyat kaydı sayısını getirir"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('SELECT COUNT(*) as count FROM competitor_prices WHERE status = "A"')
        result = cursor.fetchone()
        return result['count'] if result else 0
    except Exception as e:
        logging.error(f"Fiyat sayısı getirme hatası: {str(e)}")
        return 0
    finally:
        conn.close()

# İlk kurulum
if not os.path.exists(COMPETITOR_DB_PATH):
    init_competitor_database()
    logging.info("Competitor tracking veritabanı ilk kez oluşturuldu")