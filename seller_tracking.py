"""
Satıcı İzleme ve Analiz Modülü - Veritabanı ve İş Mantığı
Trendyol satıcılarını takip ederek mağaza verilerini, puanları ve performans bilgilerini toplar
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

def init_seller_database():
    """
    Satıcı takip modülü için gerekli tabloları oluşturur
    Mevcut competitor_tracking.db veritabanına yeni tablolar ekler
    """
    conn = get_db_connection()
    try:
        # 1. Seller Links Tablosu - Satıcı linklerini saklar
        conn.execute('''
            CREATE TABLE IF NOT EXISTS seller_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                all_products_url TEXT NOT NULL,
                seller_profile_url TEXT NOT NULL,
                seller_name TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT,
                updated_by TEXT,
                notes TEXT,
                UNIQUE(all_products_url, seller_profile_url)
            )
        ''')
        
        # 2. Seller Data Tablosu - Scraping ile toplanan veriler
        conn.execute('''
            CREATE TABLE IF NOT EXISTS seller_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_link_id INTEGER NOT NULL,
                seller_name TEXT,
                seller_score REAL,
                product_count INTEGER,
                follower_count INTEGER,
                store_age INTEGER,
                location TEXT,
                total_reviews INTEGER,
                total_comments INTEGER,
                overall_rating REAL,
                scraped_by TEXT NOT NULL,
                scraped_at TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                scrape_source TEXT DEFAULT 'manual',
                FOREIGN KEY (seller_link_id) REFERENCES seller_links (id)
            )
        ''')
        
        # 3. Seller Settings Tablosu - Modül ayarları
        conn.execute('''
            CREATE TABLE IF NOT EXISTS seller_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by TEXT NOT NULL
            )
        ''')
        
        # Index'leri oluştur
        conn.execute('CREATE INDEX IF NOT EXISTS idx_seller_links_active ON seller_links(is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_seller_links_urls ON seller_links(all_products_url, seller_profile_url)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_seller_data_link_id ON seller_data(seller_link_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_seller_data_active ON seller_data(is_active)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_seller_data_scraped_at ON seller_data(scraped_at)')
        
        # Varsayılan ayarları ekle
        conn.execute('''
            INSERT OR IGNORE INTO seller_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', '11:00', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        conn.execute('''
            INSERT OR IGNORE INTO seller_settings (setting_key, setting_value, updated_at, updated_by)
            VALUES ('scraping_enabled', 'true', ?, 'system')
        ''', (datetime.now().isoformat(),))
        
        conn.commit()
        logging.info("Seller tracking veritabanı tabloları başarıyla oluşturuldu")
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Seller veritabanı oluşturma hatası: {str(e)}")
        raise
    finally:
        conn.close()

def check_seller_tables_exist() -> bool:
    """Gerekli seller tablolarının var olup olmadığını kontrol eder"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name IN ('seller_links', 'seller_data', 'seller_settings')
        ''')
        tables = cursor.fetchall()
        return len(tables) == 3
    except Exception as e:
        logging.error(f"Seller tablo kontrolü hatası: {str(e)}")
        return False
    finally:
        conn.close()

def validate_trendyol_seller_url(url: str, url_type: str) -> bool:
    """
    Trendyol satıcı URL'ini doğrular
    url_type: 'all_products' veya 'profile'
    Returns: True if valid Trendyol seller URL
    """
    if not url or not url.strip():
        return False
        
    # Genel Trendyol kontrolü
    if not url.strip().startswith(('http://', 'https://')):
        return False
        
    if 'trendyol.com' not in url.strip():
        return False
    
    # URL tipine göre özel kontroller
    if url_type == 'all_products':
        # Tüm ürünler sayfası: /sr veya /butik içermeli
        return '/sr' in url or '/butik' in url
    elif url_type == 'profile':
        # Profil sayfası: iki format destekleniyor
        # Eski format: satici-profili
        # Yeni format: magaza/profil
        return 'satici-profili' in url or 'magaza/profil' in url
    
    return True


def parse_follower_count(follower_text: str) -> int:
    """
    Takipçi sayısını parse eder - TÜRKİYE FORMATI
    Örnekler: 
    - "14,6B" -> 14600 (14.6 Bin - Türkiye formatı)
    - "1,2M" -> 1200000 (1.2 Milyon) 
    - "500K" -> 500000 (500 Bin)
    - "1234" -> 1234 (Direkt sayı)
    """
    if not follower_text:
        return 0
    
    # Metni temizle - sadece sayılar, virgül, nokta ve harf kalsın
    clean_text = ''.join(c for c in follower_text if c.isdigit() or c in ',.KMB').upper()
    
    if not clean_text:
        return 0
    
    try:
        # B (Bin) formatı - Türkçe "Bin" kısaltması
        if 'B' in clean_text:
            number_part = clean_text.replace('B', '')
            # Türkiye formatı: virgül ondalık ayırıcı
            if ',' in number_part:
                number_part = number_part.replace(',', '.')  # 14,6 -> 14.6
            base_number = float(number_part)
            return int(base_number * 1000)  # Bin = 1000
        
        # K (Bin) formatı - 1K = 1000
        elif 'K' in clean_text:
            number_part = clean_text.replace('K', '')
            if ',' in number_part:
                number_part = number_part.replace(',', '.')
            base_number = float(number_part)
            return int(base_number * 1000)
        
        # M (Milyon) formatı - 1M = 1,000,000
        elif 'M' in clean_text:
            number_part = clean_text.replace('M', '')
            if ',' in number_part:
                number_part = number_part.replace(',', '.')
            base_number = float(number_part)
            return int(base_number * 1000000)
        
        # Direkt sayı
        else:
            # Türkiye formatında virgül varsa nokta yap
            if ',' in clean_text and '.' not in clean_text:
                clean_text = clean_text.replace(',', '.')
            
            if '.' in clean_text:
                return int(float(clean_text))
            else:
                return int(clean_text)
                
    except (ValueError, TypeError):
        # Parse edemezse 0 döndür
        print(f"⚠️ Parse hatası: '{follower_text}' -> '{clean_text}'")
        return 0

def parse_store_age(age_text: str) -> int:
    """
    Mağaza yaşını parse eder (5 Yıl -> 5)
    """
    if not age_text:
        return 0
    
    try:
        # Sadece sayıları çıkar
        numbers = re.findall(r'\d+', age_text)
        if numbers:
            return int(numbers[0])
        return 0
    except (ValueError, TypeError):
        logging.warning(f"Mağaza yaşı parse edilemedi: {age_text}")
        return 0

def add_seller_links(all_products_url: str, seller_profile_url: str, username: str) -> Tuple[bool, str, Optional[int]]:
    """
    Yeni satıcı linklerini ekler veya pasif olan satıcıyı yeniden aktive eder
    Returns: (success, message, seller_link_id)
    """
    def _add_seller_operation():
        # URL'leri normalize et ve popup parametrelerini ekle
        all_products_url_clean = normalize_seller_url(all_products_url.strip(), 'all_products')
        seller_profile_url_clean = normalize_seller_url(seller_profile_url.strip(), 'profile')
        
        # Validasyonlar
        if not validate_trendyol_seller_url(all_products_url_clean, 'all_products'):
            return False, "Geçerli bir Trendyol tüm ürünler sayfası linki giriniz!", None
        
        if not validate_trendyol_seller_url(seller_profile_url_clean, 'profile'):
            return False, "Geçerli bir Trendyol satıcı profili linki giriniz!", None
        
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Önce aktif kayıt var mı kontrol et
            cursor = conn.execute('''
                SELECT id FROM seller_links 
                WHERE (all_products_url = ? OR seller_profile_url = ?) 
                AND is_active = 1
            ''', (all_products_url_clean, seller_profile_url_clean))
            
            existing_active = cursor.fetchone()
            if existing_active:
                return False, "Bu satıcı linkleri zaten sistemde aktif!", None
            
            # Pasif kayıt var mı kontrol et
            cursor = conn.execute('''
                SELECT id FROM seller_links 
                WHERE (all_products_url = ? OR seller_profile_url = ?) 
                AND is_active = 0
            ''', (all_products_url_clean, seller_profile_url_clean))
            
            existing_passive = cursor.fetchone()
            
            if existing_passive:
                # Pasif kaydı aktive et
                seller_link_id = existing_passive[0]
                
                print(f"🔍 DEBUG: Pasif kayıt bulundu, aktive ediliyor. ID: {seller_link_id}")
                
                # seller_links tablosunu aktive et
                try:
                    result = conn.execute('''
                        UPDATE seller_links 
                        SET is_active = 1, updated_at = ?, updated_by = ?
                        WHERE id = ?
                    ''', (current_time, username, seller_link_id))
                    print(f"🔍 DEBUG: seller_links güncellendi, etkilenen satır: {result.rowcount}")
                except Exception as e:
                    print(f"❌ DEBUG: seller_links güncelleme hatası: {str(e)}")
                    raise
                
                # seller_data tablosundaki ilgili kayıtları da aktive et
                try:
                    result = conn.execute('''
                        UPDATE seller_data 
                        SET is_active = 1
                        WHERE seller_link_id = ?
                    ''', (seller_link_id,))
                    print(f"🔍 DEBUG: seller_data güncellendi, etkilenen satır: {result.rowcount}")
                except Exception as e:
                    print(f"❌ DEBUG: seller_data güncelleme hatası: {str(e)}")
                    raise
                
                conn.commit()
                
                logging.info(f"Pasif satıcı yeniden aktive edildi: {all_products_url_clean} | {seller_profile_url_clean} (ID: {seller_link_id})")
                return True, "Pasif satıcı başarıyla yeniden aktive edildi!", seller_link_id
            
            else:
                # Yeni kaydı ekle
                cursor = conn.execute('''
                    INSERT INTO seller_links 
                    (all_products_url, seller_profile_url, is_active, created_at, created_by)
                    VALUES (?, ?, 1, ?, ?)
                ''', (all_products_url_clean, seller_profile_url_clean, current_time, username))
                
                seller_link_id = cursor.lastrowid
                conn.commit()
                
                logging.info(f"Yeni satıcı linkleri eklendi: {all_products_url_clean} | {seller_profile_url_clean} (ID: {seller_link_id})")
                return True, "Satıcı linkleri başarıyla eklendi!", seller_link_id
            
        except sqlite3.IntegrityError:
            conn.rollback()
            return False, "Bu link kombinasyonu zaten mevcut!", None
        except Exception as e:
            conn.rollback()
            logging.error(f"Satıcı linki ekleme hatası: {str(e)}")
            return False, f"Veritabanı hatası: {str(e)}", None
        finally:
            conn.close()
    
    return safe_db_operation(_add_seller_operation)

def get_active_seller_links() -> List[Dict]:
    """
    Aktif satıcı linklerini getirir
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT id, all_products_url, seller_profile_url, seller_name,
                   created_at, created_by
            FROM seller_links 
            WHERE is_active = 1
            ORDER BY created_at DESC
        ''')
        
        links = []
        for row in cursor.fetchall():
            links.append({
                'id': row['id'],
                'all_products_url': row['all_products_url'],
                'seller_profile_url': row['seller_profile_url'],
                'seller_name': row['seller_name'],
                'created_at': row['created_at'],
                'created_by': row['created_by']
            })
        
        return links
        
    except Exception as e:
        logging.error(f"Aktif satıcı linkleri getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def save_seller_data(seller_link_id: int, seller_name: str, seller_score: float,
                    product_count: int, follower_count: int, store_age: int,
                    location: str, total_reviews: int, total_comments: int,
                    overall_rating: float, scraped_by: str) -> bool:
    """
    Scraping ile elde edilen satıcı verilerini kaydeder
    """
    def _save_seller_data_operation():
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Satıcı verilerini kaydet
            conn.execute('''
                INSERT INTO seller_data 
                (seller_link_id, seller_name, seller_score, product_count, 
                 follower_count, store_age, location, total_reviews, 
                 total_comments, overall_rating, scraped_by, scraped_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ''', (seller_link_id, seller_name[:200] if seller_name else None, 
                  seller_score, product_count, follower_count, store_age,
                  location[:100] if location else None, total_reviews, 
                  total_comments, overall_rating, scraped_by, current_time))
            
            # Eğer satıcı adı varsa ve daha önce kaydedilmemişse seller_links tablosunu güncelle
            if seller_name:
                cursor = conn.execute('''
                    SELECT seller_name FROM seller_links WHERE id = ?
                ''', (seller_link_id,))
                current_name = cursor.fetchone()
                
                if not current_name or not current_name['seller_name']:
                    conn.execute('''
                        UPDATE seller_links 
                        SET seller_name = ?, updated_at = ?, updated_by = ?
                        WHERE id = ?
                    ''', (seller_name[:100] if seller_name else None, 
                          current_time, scraped_by, seller_link_id))
            
            conn.commit()
            logging.info(f"Satıcı verisi kaydedildi: Link ID {seller_link_id}")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Satıcı verisi kaydetme hatası: {str(e)}")
            return False
        finally:
            conn.close()
    
    return safe_db_operation(_save_seller_data_operation)

def get_latest_seller_data() -> List[Dict]:
    """
    Her satıcı için en son scraping verilerini getirir (ana sayfa listesi için)
    """
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT 
                sl.id as link_id,
                sl.all_products_url,
                sl.seller_profile_url,
                sl.seller_name as saved_name,
                sd.seller_name,
                sd.seller_score,
                sd.product_count,
                sd.follower_count,
                sd.store_age,
                sd.location,
                sd.total_reviews,
                sd.total_comments,
                sd.overall_rating,
                sd.scraped_at
            FROM seller_links sl
            LEFT JOIN seller_data sd ON sl.id = sd.seller_link_id 
                AND sd.id = (
                    SELECT id FROM seller_data sd2 
                    WHERE sd2.seller_link_id = sl.id 
                    AND sd2.is_active = 1 
                    ORDER BY sd2.scraped_at DESC 
                    LIMIT 1
                )
            WHERE sl.is_active = 1
            ORDER BY sl.created_at DESC
        ''')
        
        sellers = []
        for row in cursor.fetchall():
            # Son güncelleme tarihini formatla
            last_update_display = ""
            if row['scraped_at']:
                try:
                    dt = datetime.fromisoformat(row['scraped_at'])
                    last_update_display = dt.strftime('%d/%m/%Y %H:%M')
                except:
                    last_update_display = row['scraped_at']
            
            sellers.append({
                'link_id': row['link_id'],
                'all_products_url': row['all_products_url'],
                'seller_profile_url': row['seller_profile_url'],
                'seller_name': row['seller_name'] or row['saved_name'] or 'Satıcı adı yükleniyor...',
                'location': row['location'] or '-',
                'store_age': row['store_age'] or 0,
                'seller_score': row['seller_score'] or 0,
                'follower_count': row['follower_count'] or 0,
                'product_count': row['product_count'] or 0,
                'overall_rating': row['overall_rating'] or 0,
                'total_reviews': row['total_reviews'] or 0,
                'total_comments': row['total_comments'] or 0,
                'last_update': last_update_display or 'Henüz güncellenmedi'
            })
        
        return sellers
        
    except Exception as e:
        logging.error(f"Son satıcı verileri getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def get_seller_history_data(seller_link_id: int, data_type: str, days: int = 30) -> List[Dict]:
    """
    Belirli bir satıcı için belirtilen veri tipinin son 30 günlük geçmişini getirir
    Her gün için en son kaydı alır, kayıt yoksa o günü atlar
    data_type: 'seller_score', 'product_count', 'follower_count', 'total_reviews', 'total_comments', 'overall_rating'
    """
    conn = get_db_connection()
    try:
        # Geçerli veri tiplerini kontrol et
        valid_types = ['seller_score', 'product_count', 'follower_count', 'total_reviews', 'total_comments', 'overall_rating']
        if data_type not in valid_types:
            return []
        
        # Son N günlük veriyi çek - HER GÜN İÇİN EN SON KAYIT
        start_date = (datetime.now() - timedelta(days=days)).isoformat()
        
        cursor = conn.execute(f'''
            SELECT 
                DATE(scraped_at) as date,
                {data_type} as value,
                MAX(scraped_at) as latest_scraped_at,
                seller_name
            FROM seller_data 
            WHERE seller_link_id = ? 
            AND is_active = 1
            AND datetime(scraped_at) >= datetime(?)
            AND {data_type} IS NOT NULL
            GROUP BY DATE(scraped_at)
            ORDER BY date ASC
        ''', (seller_link_id, start_date))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                'date': row['date'],
                'value': row['value'],
                'scraped_at': row['latest_scraped_at'],
                'seller_name': row['seller_name']
            })
        
        return history
        
    except Exception as e:
        logging.error(f"Satıcı geçmiş verisi getirme hatası: {str(e)}")
        return []
    finally:
        conn.close()

def soft_delete_seller(seller_link_id: int, username: str) -> bool:
    """
    Satıcıyı soft delete yapar (is_active = 0)
    Hem seller_links hem de seller_data tablolarını günceller
    """
    def _soft_delete_operation():
        conn = get_db_connection()
        try:
            current_time = datetime.now().isoformat()
            
            # Seller links tablosunu güncelle
            conn.execute('''
                UPDATE seller_links 
                SET is_active = 0, updated_at = ?, updated_by = ?
                WHERE id = ?
            ''', (current_time, username, seller_link_id))
            
            # Seller data tablosundaki tüm kayıtları pasif yap
            conn.execute('''
                UPDATE seller_data 
                SET is_active = 0
                WHERE seller_link_id = ?
            ''', (seller_link_id,))
            
            conn.commit()
            logging.info(f"Satıcı pasif yapıldı: Link ID {seller_link_id}")
            return True
            
        except Exception as e:
            conn.rollback()
            logging.error(f"Satıcı silme hatası: {str(e)}")
            return False
        finally:
            conn.close()
    
    return safe_db_operation(_soft_delete_operation)

def get_seller_settings() -> Dict:
    """Satıcı takip ayarlarını getirir"""
    conn = get_db_connection()
    try:
        cursor = conn.execute('''
            SELECT setting_key, setting_value
            FROM seller_settings
        ''')
        
        settings = {}
        for row in cursor.fetchall():
            settings[row['setting_key']] = row['setting_value']
        
        return settings
        
    except Exception as e:
        logging.error(f"Satıcı ayarları getirme hatası: {str(e)}")
        return {}
    finally:
        conn.close()

def save_seller_settings(schedule_time: str, username: str) -> bool:
    """Satıcı takip ayarlarını kaydeder"""
    conn = get_db_connection()
    try:
        current_time = datetime.now().isoformat()
        
        conn.execute('''
            INSERT OR REPLACE INTO seller_settings 
            (setting_key, setting_value, updated_at, updated_by)
            VALUES ('schedule_time', ?, ?, ?)
        ''', (schedule_time, current_time, username))
        
        conn.commit()
        logging.info(f"Satıcı ayarları kaydedildi: schedule_time = {schedule_time}")
        return True
        
    except Exception as e:
        conn.rollback()
        logging.error(f"Satıcı ayarları kaydetme hatası: {str(e)}")
        return False
    finally:
        conn.close()

def get_seller_statistics() -> Dict:
    """
    Satıcı takip modülü istatistiklerini getirir
    """
    conn = get_db_connection()
    try:
        # Aktif satıcı sayısı
        cursor = conn.execute('SELECT COUNT(*) as count FROM seller_links WHERE is_active = 1')
        active_sellers = cursor.fetchone()['count']
        
        # Toplam veri sayısı
        cursor = conn.execute('SELECT COUNT(*) as count FROM seller_data WHERE is_active = 1')
        total_data_points = cursor.fetchone()['count']
        
        # Son güncelleme
        cursor = conn.execute('''
            SELECT MAX(scraped_at) as last_update
            FROM seller_data 
            WHERE is_active = 1
        ''')
        last_update = cursor.fetchone()['last_update']
        
        return {
            'active_sellers': active_sellers,
            'total_data_points': total_data_points,
            'last_update': last_update
        }
        
    except Exception as e:
        logging.error(f"Satıcı istatistikleri getirme hatası: {str(e)}")
        return {
            'active_sellers': 0,
            'total_data_points': 0,
            'last_update': None
        }
    finally:
        conn.close()

def normalize_seller_url(url: str, url_type: str) -> str:
    """
    Satıcı URL'lerini normalize eder ve popup engelleme parametrelerini ekler
    """
    if not url or not url.strip():
        return url
    
    url = url.strip()
    
    # URL'de zaten popup=false varsa dokunma
    if 'popup=false' in url.lower():
        return url
    
    try:
        if url_type == 'all_products':
            # Tüm ürünler sayfası için &popup=false ekle
            if '?' in url:
                # Zaten parametre var, & ile ekle
                if not url.endswith('&'):
                    url += '&'
                url += 'popup=false'
            else:
                # İlk parametre, ? ile ekle
                url += '?popup=false'
                
        elif url_type == 'profile':
            # Profil sayfası için ?popup=false ekle (genelde parametre olmaz)
            if '?' in url:
                # Zaten parametre var, & ile ekle
                if not url.endswith('&'):
                    url += '&'
                url += 'popup=false'
            else:
                # İlk parametre, ? ile ekle
                url += '?popup=false'
        
        return url
        
    except Exception as e:
        logging.warning(f"URL normalize hatası: {str(e)}")
        return url

# İlk kurulum - tabloları oluştur
def ensure_seller_tables():
    """Satıcı tabloları yoksa oluştur"""
    if not check_seller_tables_exist():
        init_seller_database()
        logging.info("Satıcı takip tabloları oluşturuldu")

# Modül yüklendiğinde tabloları kontrol et
ensure_seller_tables()