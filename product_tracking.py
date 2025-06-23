"""
Ürün İzleme ve Analiz Modülü - Veritabanı ve İş Mantığı
Trendyol ürünlerini takip ederek satış verilerini, yorumları ve fiyat bilgilerini toplar
Mevcut competitor_tracking.db veritabanını kullanır
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
import logging
import re
import time
from typing import List, Dict, Optional, Tuple
import threading

# Mevcut competitor veritabanını kullan
from competitor_tracking import get_db_connection, safe_db_operation

# Thread-safe veritabanı erişimi için lock (mevcut sistemden)
db_lock = threading.Lock()

def init_product_database():
    """
    Ürün takip modülü için gerekli tabloları oluşturur
    Mevcut competitor_tracking.db veritabanına yeni tablolar ekler
    """
    conn = get_db_connection()
    try:
        # 1. Product Links Tablosu - Ürün linklerini saklar
        conn.execute('''
            CREATE TABLE IF NOT EXISTS product_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_url TEXT NOT NULL UNIQUE,
                first_comment_date TEXT NOT NULL,
                product_image_url TEXT,
                product_title TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT,
                updated_by TEXT,
                notes TEXT
            )
        ''')
        
        # 2. Product Data Tablosu - Scraping ile toplanan veriler
        conn.execute('''
            CREATE TABLE IF NOT EXISTS product_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_link_id INTEGER NOT NULL,
                seller_name TEXT,
                product_title TEXT,
                price REAL,
                comment_count INTEGER,
                question_count INTEGER,
                rating REAL,
                sales_3day INTEGER,
                daily_estimated_sales REAL,
                seller_rating REAL,
                scraped_by TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                scrape_source TEXT DEFAULT 'manual',
                FOREIGN KEY (product_link_id) REFERENCES product_links (id)
            )
        ''')
        
        # 3. Product Settings Tablosu - Modül ayarları
        conn.execute('''
            CREATE TABLE IF NOT EXISTS product_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )
        ''')
        
        # Index'leri oluştur
        conn.execute('CREATE INDEX IF NOT EXISTS idx_product_links_active ON product_links(is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_product_links_url ON product_links(product_url)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_product_data_link_id ON product_data(product_link_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_product_data_active ON product_data(is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_product_data_scraped_at ON product_data(scraped_at)')
        
        # Varsayılan ayarları ekle
        conn.execute('''
            INSERT OR IGNORE INTO product_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', '10:00', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        conn.execute('''
            INSERT OR IGNORE INTO product_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('scraping_enabled', 'true', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        conn.commit()
        logging.info("Product tracking veritabanı tabloları başarıyla oluşturuldu")
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Product veritabanı oluşturma hatası: {str(e)}")
        raise
    finally:
        conn.close()

def check_product_tables_exist() -> bool:
    """Gerekli product tablolarının var olup olmadığını kontrol eder"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name IN ('product_links', 'product_data', 'product_settings')
        ''')
        tables = cursor.fetchall()
        return len(tables) == 3
    except Exception as e:
        logging.error(f"Product tablo kontrolü hatası: {str(e)}")
        return False
    finally:
        conn.close()

def validate_trendyol_url(url: str) -> bool:
    """
    Trendyol URL'ini doğrular
    Returns: True if valid Trendyol URL
    """
    if not url or not url.strip():
        return False
        
    trendyol_pattern = re.compile(r'https?://(?:www\.)?trendyol\.com/.*')
    return bool(trendyol_pattern.match(url.strip()))

def validate_date_format(date_str: str) -> bool:
    """
    Tarih formatını doğrular (DD/MM/YYYY)
    Returns: True if valid date format
    """
    if not date_str or not date_str.strip():
        return False
    
    try:
        # DD/MM/YYYY formatını kontrol et
        datetime.strptime(date_str.strip(), '%d/%m/%Y')
        return True
    except ValueError:
        return False

def convert_date_to_iso(date_str: str) -> str:
    """
    DD/MM/YYYY formatını ISO formatına çevirir
    """
    try:
        dt = datetime.strptime(date_str.strip(), '%d/%m/%Y')
        return dt.isoformat()
    except ValueError:
        raise ValueError("Geçersiz tarih formatı. DD/MM/YYYY formatında giriniz.")

def add_product_link(product_url: str, first_comment_date: str, username: str) -> Tuple[bool, str, Optional[int]]:
    """
    Yeni ürün linki ekler
    Returns: (success, message, product_link_id)
    """
    def _add_product_operation():
        # Validasyonlar
        if not validate_trendyol_url(product_url):
            return False, "Geçerli bir Trendyol linki giriniz!", None
        
        if not validate_date_format(first_comment_date):
            return False, "Tarih formatı DD/MM/YYYY olmalıdır!", None
        
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            first_comment_iso = convert_date_to_iso(first_comment_date)
            
            # URL'in daha önce eklenip eklenmediğini kontrol et
            cursor = conn.execute('''
                SELECT id FROM product_links 
                WHERE product_url = ? AND is_active = 1
            ''', (product_url.strip(),))
            
            existing = cursor.fetchone()
            if existing:
                return False, "Bu ürün linki zaten eklenmiş!", None
            
            # Yeni kaydı ekle
            cursor = conn.execute('''
                INSERT INTO product_links 
                (product_url, first_comment_date, is_active, created_at, created_by)
                VALUES (?, ?, 1, ?, ?)
            ''', (product_url.strip(), first_comment_iso, current_time, username))
            
            product_link_id = cursor.lastrowid
            conn.commit()
            
            logging.info(f"Yeni ürün linki eklendi: {product_url} (ID: {product_link_id})")
            return True, "Ürün linki başarıyla eklendi!", product_link_id
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Ürün linki ekleme hatası: {str(e)}")
            return False, f"Veritabanı hatası: {str(e)}", None
        finally:
            conn.close()
    
    return safe_db_operation(_add_product_operation)

def get_active_product_links() -> List[Dict]:
    """
    Aktif ürün linklerini getirir
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT id, product_url, first_comment_date, product_image_url, 
                   product_title, created_at, created_by
            FROM product_links 
            WHERE is_active = 1
            ORDER BY created_at DESC
        ''')
        
        links = []
        for row in cursor.fetchall():
            links.append({
                'id': row['id'],
                'product_url': row['product_url'],
                'first_comment_date': row['first_comment_date'],
                'product_image_url': row['product_image_url'],
                'product_title': row['product_title'],
                'created_at': row['created_at'],
                'created_by': row['created_by']
            })
        
        return links
        
    except Exception as e:
        logging.error(f"Aktif ürün linkleri getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def save_product_data(product_link_id: int, seller_name: str, product_title: str, 
                     price: float, comment_count: int, question_count: int, 
                     rating: float, sales_3day: Optional[int], seller_rating: float,
                     scraped_by: str, product_image_url: Optional[str] = None) -> bool:
    """
    Scraping ile elde edilen ürün verilerini kaydeder
    """
    def _save_product_data_operation():
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Günlük tahmini satış hesapla
            daily_estimated_sales = None
            if sales_3day is not None and sales_3day > 0:
                daily_estimated_sales = sales_3day / 3.0
            
            # Ürün verilerini kaydet
            conn.execute('''
                INSERT INTO product_data 
                (product_link_id, seller_name, product_title, price, comment_count, 
                 question_count, rating, sales_3day, daily_estimated_sales, 
                 seller_rating, scraped_by, scraped_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (product_link_id, seller_name, product_title[:200] if product_title else None, 
                  price, comment_count, question_count, rating, sales_3day, 
                  daily_estimated_sales, seller_rating, scraped_by, current_time))
            
            # Eğer ürün resmi varsa ve daha önce kaydedilmemişse product_links tablosunu güncelle
            if product_image_url:
                cursor = conn.execute('''
                    SELECT product_image_url FROM product_links WHERE id = ?
                ''', (product_link_id,))
                current_image = cursor.fetchone()
                
                if not current_image or not current_image['product_image_url']:
                    conn.execute('''
                        UPDATE product_links 
                        SET product_image_url = ?, product_title = ?, updated_at = ?, updated_by = ?
                        WHERE id = ?
                    ''', (product_image_url, product_title[:100] if product_title else None, 
                          current_time, scraped_by, product_link_id))
            
            conn.commit()
            logging.info(f"Ürün verisi kaydedildi: Link ID {product_link_id}")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Ürün verisi kaydetme hatası: {str(e)}")
            return False
        finally:
            conn.close()
    
    return safe_db_operation(_save_product_data_operation)


def get_latest_product_data() -> List[Dict]:
    """
    Her ürün için en son scraping verilerini getirir (ana sayfa listesi için)
    YENİ: price ve seller_rating alanları eklendi
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT 
                pl.id as link_id,
                pl.product_url,
                pl.first_comment_date,
                pl.product_image_url,
                pl.product_title as saved_title,
                pd.product_title,
                pd.seller_name,
                pd.comment_count,
                pd.rating,
                pd.question_count,
                pd.daily_estimated_sales,
                pd.scraped_at,
                pd.price,              -- YENİ: Price alanı eklendi
                pd.seller_rating       -- YENİ: Seller rating alanı eklendi
            FROM product_links pl
            LEFT JOIN product_data pd ON pl.id = pd.product_link_id 
                AND pd.id = (
                    SELECT id FROM product_data pd2 
                    WHERE pd2.product_link_id = pl.id 
                    AND pd2.is_active = 1 
                    ORDER BY pd2.scraped_at DESC 
                    LIMIT 1
                )
            WHERE pl.is_active = 1
            ORDER BY pl.created_at DESC
        ''')
        
        products = []
        for row in cursor.fetchall():
            # İlk yorum tarihini DD/MM/YYYY formatına çevir
            first_comment_display = ""
            if row['first_comment_date']:
                try:
                    dt = datetime.fromisoformat(row['first_comment_date'])
                    first_comment_display = dt.strftime('%d/%m/%Y')
                except:
                    first_comment_display = row['first_comment_date']
            
            # Son güncelleme tarihini formatla
            last_update_display = ""
            if row['scraped_at']:
                try:
                    dt = datetime.fromisoformat(row['scraped_at'])
                    last_update_display = dt.strftime('%d/%m/%Y %H:%M')
                except:
                    last_update_display = row['scraped_at']
            
            # YENİ: Price ve seller_rating değerlerini kontrol et ve formatla
            price_value = row['price'] if row['price'] is not None else 0.0
            seller_rating_value = row['seller_rating'] if row['seller_rating'] is not None else 0.0
            
            products.append({
                'link_id': row['link_id'],
                'product_url': row['product_url'],
                'first_comment_date': first_comment_display,
                'product_image_url': row['product_image_url'],
                'product_title': row['product_title'] or row['saved_title'] or 'Başlık yükleniyor...',
                'seller_name': row['seller_name'] or '-',
                'comment_count': row['comment_count'] or 0,
                'rating': row['rating'] or 0,
                'question_count': row['question_count'] or 0,
                'daily_estimated_sales': row['daily_estimated_sales'] or 0,
                'last_update': last_update_display or 'Henüz güncellenmedi',
                'price': price_value,                    # YENİ: Price alanı eklendi
                'seller_rating': seller_rating_value    # YENİ: Seller rating alanı eklendi
            })
        
        return products
        
    except Exception as e:
        logging.error(f"Son ürün verileri getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()


def get_product_history_data(product_link_id: int, data_type: str, days: int = 30) -> List[Dict]:
    """
    Belirli bir ürün için belirtilen veri tipinin son 30 günlük geçmişini getirir
    Her gün için en son kaydı alır, kayıt yoksa o günü atlar
    data_type: 'comment_count', 'rating', 'question_count', 'daily_estimated_sales', 'price', 'seller_rating', 'sales_3day'
    """
    conn = get_db_connection()
    try:
        # Geçerli veri tiplerini kontrol et
        valid_types = ['comment_count', 'rating', 'question_count', 'daily_estimated_sales', 'price', 'seller_rating', 'sales_3day']
        if data_type not in valid_types:
            return []
        
        # Son N günlük veriyi çek - HER GÜN İÇİN EN SON KAYIT
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor = conn.execute(f'''
            SELECT 
                DATE(scraped_at) as date,
                {data_type} as value,
                MAX(scraped_at) as latest_scraped_at,
                product_title
            FROM product_data 
            WHERE product_link_id = ? 
            AND is_active = 1
            AND datetime(scraped_at) >= datetime(?)
            AND {data_type} IS NOT NULL
            GROUP BY DATE(scraped_at)
            ORDER BY date ASC
        ''', (product_link_id, start_date))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                'date': row['date'],
                'value': row['value'],
                'scraped_at': row['latest_scraped_at'],
                'product_title': row['product_title']
            })
        
        return history
        
    except Exception as e:
        logging.error(f"Ürün geçmiş verisi getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def soft_delete_product(product_link_id: int, username: str) -> bool:
    """
    Ürünü soft delete yapar (is_active = 0)
    Hem product_links hem de product_data tablolarını günceller
    """
    def _soft_delete_operation():
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Product links tablosunu güncelle
            conn.execute('''
                UPDATE product_links 
                SET is_active = 0, updated_at = ?, updated_by = ?
                WHERE id = ?
            ''', (current_time, username, product_link_id))
            
            # Product data tablosundaki tüm kayıtları pasif yap
            conn.execute('''
                UPDATE product_data 
                SET is_active = 0
                WHERE product_link_id = ?
            ''', (product_link_id,))
            
            conn.commit()
            logging.info(f"Ürün pasif yapıldı: Link ID {product_link_id}")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Ürün silme hatası: {str(e)}")
            return False
        finally:
            conn.close()
    
    return safe_db_operation(_soft_delete_operation)

def get_product_settings() -> Dict:
    """Ürün takip ayarlarını getirir"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT setting_key, setting_value
            FROM product_settings
        ''')
        
        settings = {}
        for row in cursor.fetchall():
            settings[row['setting_key']] = row['setting_value']
        
        return settings
        
    except Exception as e:
        logging.error(f"Ürün ayarları getirme hatası: {str(e)}")
        return {}
    finally:
        conn.close()

def save_product_settings(schedule_time: str, username: str) -> bool:
    """Ürün takip ayarlarını kaydeder"""
    conn = get_db_connection()
    try:
        current_time = datetime.now().isoformat()
        
        conn.execute('''
            INSERT OR REPLACE INTO product_settings 
            (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', ?, ?, ?)
        ''', (schedule_time, current_time, username))
        
        conn.commit()
        logging.info(f"Ürün ayarları kaydedildi: schedule_time = {schedule_time}")
        return True
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Ürün ayarları kaydetme hatası: {str(e)}")
        return False
    finally:
        conn.close()

def get_product_statistics() -> Dict:
    """
    Ürün takip modülü istatistiklerini getirir
    """
    conn = get_db_connection()
    try:
        # Aktif ürün sayısı
        cursor = conn.execute('SELECT COUNT(*) as count FROM product_links WHERE is_active = 1')
        active_products = cursor.fetchone()['count']
        
        # Toplam veri sayısı
        cursor = conn.execute('SELECT COUNT(*) as count FROM product_data WHERE is_active = 1')
        total_data_points = cursor.fetchone()['count']
        
        # Son güncelleme
        cursor = conn.execute('''
            SELECT MAX(scraped_at) as last_update
            FROM product_data 
            WHERE is_active = 1
        ''')
        last_update = cursor.fetchone()['last_update']
        
        return {
            'active_products': active_products,
            'total_data_points': total_data_points,
            'last_update': last_update
        }
        
    except Exception as e:
        logging.error(f"Ürün istatistikleri getirme hatası: {str(e)}")
        return {
            'active_products': 0,
            'total_data_points': 0,
            'last_update': None
        }
    finally:
        conn.close()

# İlk kurulum - tabloları oluştur
def ensure_product_tables():
    """Ürün tabloları yoksa oluştur"""
    if not check_product_tables_exist():
        init_product_database()
        logging.info("Ürün takip tabloları oluşturuldu")

# Modül yüklendiğinde tabloları kontrol et
ensure_product_tables()