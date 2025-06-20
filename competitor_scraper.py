"""
Rakip Fiyat Takip Modülü - Web Scraping
Trendyol sayfalarından ürün bilgilerini çeker
YENİ: Slot 0 (NeşeliÇiçekler) desteği eklendi
DÜZELTME: Tüm parametre uyumsuzlukları giderildi
"""

import requests
from bs4 import BeautifulSoup
import time
import random
import logging
import threading
from typing import Dict, Optional, List
from competitor_tracking import (
    save_scraped_price, 
    get_all_active_links,
    get_links_by_barcode
)

# Scraping ayarları
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

REQUEST_DELAY_MIN = 2  # Minimum bekleme süresi (saniye)
REQUEST_DELAY_MAX = 5  # Maximum bekleme süresi (saniye)
REQUEST_TIMEOUT = 15   # İstek timeout süresi (saniye)

# YENİ: Slot 0 için özel ayarlar
NESELICICEKLER_DELAY_MIN = 3  # NeşeliÇiçekler için daha uzun bekleme
NESELICICEKLER_DELAY_MAX = 7

# Global scraping durumu
scraping_status = {
    'is_running': False,
    'current_progress': 0,
    'total_items': 0,
    'current_item': '',
    'started_by': '',
    'start_time': None,
    'errors': [],
    'include_slot_0': False,  # YENİ: Slot 0 dahil mi?
    'slot_0_processed': 0,    # YENİ: Slot 0 işlem sayısı
    'competitor_processed': 0  # YENİ: Rakip slot işlem sayısı
}

scraping_lock = threading.Lock()

def get_random_headers() -> Dict[str, str]:
    """Rastgele User-Agent ile header oluşturur"""
    return {
        'User-Agent': random.choice(USER_AGENTS),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none'
    }

def scrape_trendyol_product(url: str, slot_number: int = 1) -> Optional[Dict[str, str]]:
    """
    Trendyol ürün sayfasından bilgileri çeker
    YENİ: slot_number parametresi eklendi (slot 0 için özel işlemler)
    Returns: {'product_name': str, 'price': float, 'seller_name': str} or None
    """
    try:
        headers = get_random_headers()
        
        # YENİ: Slot 0 için daha uzun bekleme
        if slot_number == 0:
            delay = random.uniform(NESELICICEKLER_DELAY_MIN, NESELICICEKLER_DELAY_MAX)
            logging.info(f"NeşeliÇiçekler slot scraping (daha uzun bekleme): {delay:.1f}s")
        else:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        
        time.sleep(delay)
        
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        # Ürün adını çek - TAM BAŞLIK
        product_name = None
        name_selectors = [
            'h1[data-testid="product-title"]',  # Sizin bulduğunuz - tam başlık
            'h1.product-title',
            'h1.pr-new-br',  # Tüm h1, sadece span değil
            'h1',  # Genel h1
            '.product-name h1',
            '.pr-new-br',
            'h1[data-testid="product-name"]',
            '.product-title'
        ]
        
        for selector in name_selectors:
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                # HTML tag'larını temizle ve tam metni al
                product_name = element.get_text(separator=' ', strip=True)
                
                # YENİ: Slot 0 için özel loglama
                if slot_number == 0:
                    logging.info(f"NeşeliÇiçekler ürün adı bulundu - Selector: {selector}, Değer: {product_name[:100]}...")
                else:
                    logging.info(f"Ürün adı bulundu - Selector: {selector}, Değer: {product_name[:100]}...")
                break
        
        # Fiyatı çek
        price = None
        price_selectors = [
            '.prc-dsc',
            '.prc-slg',
            '.product-price .prc-dsc',
            '.price-current',
            '[data-testid="price-current-price"]',
            '.product-price-container .prc-dsc'
        ]
        
        for selector in price_selectors:
            element = soup.select_one(selector)
            if element:
                price_text = element.get_text(strip=True)
                # Fiyat metninden sayısal değeri çıkar
                price_clean = ''.join(filter(lambda x: x.isdigit() or x == ',', price_text))
                if price_clean:
                    try:
                        price = float(price_clean.replace(',', '.'))
                        
                        # YENİ: Slot 0 için özel loglama
                        if slot_number == 0:
                            logging.info(f"NeşeliÇiçekler fiyat bulundu: {price}₺")
                        else:
                            logging.info(f"Fiyat bulundu: {price}₺")
                        break
                    except ValueError:
                        continue
        
        # Satıcı adını çek - Debug sonuçlarına göre güncellendi
        seller_name = None
        
        # Öncelikle doğru selector'ı dene
        seller_selectors = [
            '.product-description-market-place',  # Debug'da bulduğumuz doğru selector!
            'span.product-description-market-place',  # Daha spesifik
            '.merchant-name',  # Yedek
            'div[class*="merchant-name"]',  
            '[class*="merchant-name"]',  
            '.seller-name', 
            '.product-merchant a',
            '.pdp-merchant-info a',
            '[data-testid="merchant-name"]',
            '.merchant-info .merchant-name'
        ]
        
        for selector in seller_selectors:
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                seller_name = element.get_text(strip=True)
                
                # YENİ: Slot 0 için özel loglama
                if slot_number == 0:
                    logging.info(f"NeşeliÇiçekler satıcı bulundu - Selector: {selector}, Değer: {seller_name}")
                else:
                    logging.info(f"Satıcı bulundu - Selector: {selector}, Değer: {seller_name}")
                break
        
        # Dinamik class arama (yedek)
        if not seller_name:
            merchant_divs = soup.find_all('div', class_=lambda x: x and 'merchant-name' in str(x))
            if merchant_divs:
                seller_name = merchant_divs[0].get_text(strip=True)
                
                # YENİ: Slot 0 için özel loglama
                if slot_number == 0:
                    logging.info(f"NeşeliÇiçekler dinamik class ile satıcı bulundu: {seller_name}")
                else:
                    logging.info(f"Dinamik class ile satıcı bulundu: {seller_name}")
        
        # YENİ: Slot 0 için NeşeliÇiçekler kontrolü
        if slot_number == 0:
            # NeşeliÇiçekler text'ini direkt ara
            neseli_spans = soup.find_all('span', string=lambda text: text and 'NeşeliÇiçekler' in text)
            if neseli_spans:
                seller_name = neseli_spans[0].get_text(strip=True)
                logging.info(f"NeşeliÇiçekler direct text search ile bulundu: {seller_name}")
            elif not seller_name:
                # Slot 0 ise ve satıcı bulunamazsa NeşeliÇiçekler olarak varsay
                seller_name = "NeşeliÇiçekler"
                logging.info(f"Slot 0 için varsayılan satıcı: {seller_name}")
        else:
            # Rakip ürünler için CenNetHome kontrolü
            cennet_spans = soup.find_all('span', string=lambda text: text and 'CenNetHome' in text)
            if cennet_spans:
                seller_name = cennet_spans[0].get_text(strip=True)
                logging.info(f"Direct text search ile satıcı bulundu: {seller_name}")
        
        # Eğer satıcı hala bulunamazsa
        if not seller_name:
            if slot_number == 0:
                logging.warning(f"NeşeliÇiçekler satıcı bulunamadı - URL: {url}")
                seller_name = "NeşeliÇiçekler"  # Varsayılan
            else:
                logging.warning(f"Satıcı bulunamadı - URL: {url}")
                seller_name = "Bilinmiyor"
        
        # Sonuçları kontrol et
        if product_name and price is not None:
            return {
                'product_name': product_name[:200],  # Uzunluk sınırı
                'price': price,
                'seller_name': seller_name[:100] if seller_name else ("NeşeliÇiçekler" if slot_number == 0 else "Bilinmiyor")
            }
        else:
            slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Slot {slot_number}"
            logging.warning(f"Eksik veri - {slot_info} - URL: {url}, Name: {product_name}, Price: {price}, Seller: {seller_name}")
            return None
            
    except requests.exceptions.RequestException as e:
        slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Slot {slot_number}"
        logging.error(f"Request hatası - {slot_info} - {url}: {str(e)}")
        return None
    except Exception as e:
        slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Slot {slot_number}"
        logging.error(f"Scraping hatası - {slot_info} - {url}: {str(e)}")
        return None

def update_scraping_status(is_running: bool = None, progress: int = None, 
                          total: int = None, current_item: str = None,
                          started_by: str = None, error: str = None,
                          include_slot_0: bool = None, 
                          slot_0_processed: int = None,
                          competitor_processed: int = None):
    """
    Scraping durumunu günceller
    YENİ: Slot 0 istatistikleri eklendi
    """
    global scraping_status
    
    with scraping_lock:
        if is_running is not None:
            scraping_status['is_running'] = is_running
            if is_running:
                scraping_status['start_time'] = time.time()
                scraping_status['errors'] = []
                scraping_status['slot_0_processed'] = 0
                scraping_status['competitor_processed'] = 0
            
        if progress is not None:
            scraping_status['current_progress'] = progress
            
        if total is not None:
            scraping_status['total_items'] = total
            
        if current_item is not None:
            scraping_status['current_item'] = current_item
            
        if started_by is not None:
            scraping_status['started_by'] = started_by
            
        if error is not None:
            scraping_status['errors'].append(error)
            
        if include_slot_0 is not None:
            scraping_status['include_slot_0'] = include_slot_0
            
        if slot_0_processed is not None:
            scraping_status['slot_0_processed'] = slot_0_processed
            
        if competitor_processed is not None:
            scraping_status['competitor_processed'] = competitor_processed

def get_update_status() -> Dict:
    """
    Mevcut scraping durumunu döndürür
    YENİ: Slot 0 istatistikleri dahil
    """
    with scraping_lock:
        status = scraping_status.copy()
        if status['start_time']:
            status['elapsed_time'] = time.time() - status['start_time']
        else:
            status['elapsed_time'] = 0
        return status

def scrape_single_link(barcode: str, slot_number: int, url: str, scraped_by: str) -> bool:
    """
    Tek bir link için scraping yapar
    YENİ: slot_number 0 desteği - DÜZELTME: scrape_source parametresi kaldırıldı
    """
    try:
        slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Rakip Slot {slot_number}"
        logging.info(f"Scraping başlatılıyor: {barcode} - {slot_info} - {url}")
        
        product_data = scrape_trendyol_product(url, slot_number)
        
        if product_data:
            success = save_scraped_price(
                barcode=barcode,
                competitor_url=url,
                slot_number=slot_number,
                product_name=product_data['product_name'],
                price=product_data['price'],
                seller_name=product_data['seller_name'],
                scraped_by=scraped_by
                # scrape_source kaldırıldı
            )
            
            if success:
                logging.info(f"Scraping başarılı: {barcode} - {slot_info} - {product_data['price']}₺")
                return True
            else:
                logging.error(f"Veri kaydetme hatası: {barcode} - {slot_info} - {url}")
                return False
        else:
            logging.warning(f"Scraping başarısız: {barcode} - {slot_info} - {url}")
            return False
            
    except Exception as e:
        slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Rakip Slot {slot_number}"
        logging.error(f"Scraping exception: {barcode} - {slot_info} - {url} - {str(e)}")
        return False

def start_scraping_for_new_links(barcode: str, links: List[str], scraped_by: str):
    """
    Yeni kaydedilen linkler için scraping başlatır (ESKİ FONKSİYON - Sadece slot 1-5)
    Arka planda çalışır
    """
    def scrape_worker():
        try:
            active_links = get_links_by_barcode(barcode, include_slot_0=False)
            link_dict = {link['slot_number']: link['url'] for link in active_links}
            
            for slot_number, url in enumerate(links, 1):
                if url.strip() and slot_number in link_dict:
                    if link_dict[slot_number] == url.strip():
                        scrape_single_link(barcode, slot_number, url.strip(), scraped_by)
                        
        except Exception as e:
            logging.error(f"Yeni link scraping hatası: {str(e)}")
    
    # Arka planda çalıştır
    thread = threading.Thread(target=scrape_worker)
    thread.daemon = True
    thread.start()

def start_scraping_for_new_links_by_slots(barcode: str, slot_links: Dict[int, str], scraped_by: str):
    """
    YENİ: Slot numaraları ile yeni kaydedilen linkler için scraping başlatır
    slot_links: {slot_number: url}
    """
    def scrape_worker():
        try:
            for slot_number, url in slot_links.items():
                if url and url.strip():
                    scrape_single_link(barcode, slot_number, url.strip(), scraped_by)
                    
                    # Slot 0 için daha uzun bekleme
                    if slot_number == 0:
                        time.sleep(random.uniform(NESELICICEKLER_DELAY_MIN, NESELICICEKLER_DELAY_MAX))
                    else:
                        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                        
        except Exception as e:
            logging.error(f"Yeni slot link scraping hatası: {str(e)}")
    
    # Arka planda çalıştır
    thread = threading.Thread(target=scrape_worker)
    thread.daemon = True
    thread.start()

def start_manual_update(username: str):
    """
    Manuel güncelleme başlatır - ESKİ FONKSİYON (Sadece slot 1-5)
    Tüm aktif linkler için scraping yapar
    """
    return start_manual_update_with_slot_0(username, include_slot_0=False)

def start_manual_update_with_slot_0(username: str, include_slot_0: bool = True):
    """
    YENİ: Manuel güncelleme başlatır (Slot 0 dahil edilebilir)
    """
    def manual_update_worker():
        try:
            update_scraping_status(is_running=True, started_by=username, include_slot_0=include_slot_0)
            
            # Tüm aktif linkleri al
            all_links = get_all_active_links(include_slot_0=include_slot_0)
            total_links = len(all_links)
            
            update_scraping_status(total=total_links, progress=0)
            
            slot_info = "Slot 0-5" if include_slot_0 else "Slot 1-5"
            logging.info(f"Manuel güncelleme başlatıldı: {total_links} link ({slot_info})")
            
            success_count = 0
            slot_0_count = 0
            competitor_count = 0
            
            for i, link_data in enumerate(all_links):
                barcode = link_data['barcode']
                slot_number = link_data['slot_number']
                url = link_data['url']
                
                # Durumu güncelle
                slot_display = "NeşeliÇiçekler" if slot_number == 0 else f"Rakip Slot {slot_number}"
                update_scraping_status(
                    progress=i + 1,
                    current_item=f"{barcode} - {slot_display}"
                )
                
                # Scraping yap - DÜZELTME: 4 parametre
                success = scrape_single_link(barcode, slot_number, url, username)
                
                if success:
                    success_count += 1
                    if slot_number == 0:
                        slot_0_count += 1
                        update_scraping_status(slot_0_processed=slot_0_count)
                    else:
                        competitor_count += 1
                        update_scraping_status(competitor_processed=competitor_count)
                else:
                    error_msg = f"Scraping hatası: {barcode} - {slot_display}"
                    update_scraping_status(error=error_msg)
                
                # Slot 0 için daha uzun bekleme
                if slot_number == 0:
                    time.sleep(random.uniform(NESELICICEKLER_DELAY_MIN, NESELICICEKLER_DELAY_MAX))
                else:
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            
            stats_info = f"{success_count}/{total_links} başarılı"
            if include_slot_0:
                stats_info += f" (NeşeliÇiçekler: {slot_0_count}, Rakipler: {competitor_count})"
            
            logging.info(f"Manuel güncelleme tamamlandı: {stats_info}")
            
        except Exception as e:
            error_msg = f"Manuel güncelleme hatası: {str(e)}"
            logging.error(error_msg)
            update_scraping_status(error=error_msg)
        finally:
            update_scraping_status(is_running=False, current_item="")
    
    # Eğer başka bir scraping devam ediyorsa başlatma
    if scraping_status['is_running']:
        logging.warning("Scraping zaten devam ediyor, yeni işlem başlatılmadı")
        return False
    
    # Arka planda çalıştır
    thread = threading.Thread(target=manual_update_worker)
    thread.daemon = True
    thread.start()
    
    return True

def start_scheduled_update(username: str = "scheduler", include_slot_0: bool = True):
    """
    Zamanlanmış güncelleme başlatır
    YENİ: include_slot_0 parametresi eklendi
    """
    def scheduled_update_worker():
        try:
            update_scraping_status(is_running=True, started_by=username, include_slot_0=include_slot_0)
            
            # Tüm aktif linkleri al
            all_links = get_all_active_links(include_slot_0=include_slot_0)
            total_links = len(all_links)
            
            update_scraping_status(total=total_links, progress=0)
            
            slot_info = "Slot 0-5" if include_slot_0 else "Slot 1-5"
            logging.info(f"Otomatik güncelleme başlatıldı: {total_links} link ({slot_info})")
            
            success_count = 0
            slot_0_count = 0
            competitor_count = 0
            
            for i, link_data in enumerate(all_links):
                barcode = link_data['barcode']
                slot_number = link_data['slot_number']
                url = link_data['url']
                
                # Durumu güncelle
                slot_display = "NeşeliÇiçekler" if slot_number == 0 else f"Rakip Slot {slot_number}"
                update_scraping_status(
                    progress=i + 1,
                    current_item=f"{barcode} - {slot_display}"
                )
                
                # Scraping yap - DÜZELTME: 4 parametre
                success = scrape_single_link(barcode, slot_number, url, username)
                
                if success:
                    success_count += 1
                    if slot_number == 0:
                        slot_0_count += 1
                        update_scraping_status(slot_0_processed=slot_0_count)
                    else:
                        competitor_count += 1
                        update_scraping_status(competitor_processed=competitor_count)
                else:
                    error_msg = f"Otomatik scraping hatası: {barcode} - {slot_display}"
                    update_scraping_status(error=error_msg)
                
                # Otomatik güncellemede daha uzun bekleme - Slot 0 için extra uzun
                if slot_number == 0:
                    time.sleep(random.uniform(NESELICICEKLER_DELAY_MIN + 2, NESELICICEKLER_DELAY_MAX + 3))
                else:
                    time.sleep(random.uniform(REQUEST_DELAY_MIN + 1, REQUEST_DELAY_MAX + 2))
            
            stats_info = f"{success_count}/{total_links} başarılı"
            if include_slot_0:
                stats_info += f" (NeşeliÇiçekler: {slot_0_count}, Rakipler: {competitor_count})"
            
            logging.info(f"Otomatik güncelleme tamamlandı: {stats_info}")
            
        except Exception as e:
            error_msg = f"Otomatik güncelleme hatası: {str(e)}"
            logging.error(error_msg)
            update_scraping_status(error=error_msg)
        finally:
            update_scraping_status(is_running=False, current_item="")
    
    # Eğer başka bir scraping devam ediyorsa başlatma
    if scraping_status['is_running']:
        logging.warning("Scraping zaten devam ediyor, otomatik işlem atlandı")
        return False
    
    # Arka planda çalıştır
    thread = threading.Thread(target=scheduled_update_worker)
    thread.daemon = True
    thread.start()
    
    return True

def is_scraping_running() -> bool:
    """Scraping işleminin devam edip etmediğini kontrol eder"""
    return scraping_status['is_running']

# YENİ FONKSİYONLAR: NeşeliÇiçekler özel işlemleri

def start_neselicicekler_only_update(username: str):
    """
    Sadece NeşeliÇiçekler (slot 0) linklerini günceller
    """
    def neseli_update_worker():
        try:
            update_scraping_status(is_running=True, started_by=username, include_slot_0=True)
            
            # Sadece slot 0 linklerini al
            from competitor_tracking import get_all_active_links_by_slot
            neseli_links = get_all_active_links_by_slot(0)
            total_links = len(neseli_links)
            
            update_scraping_status(total=total_links, progress=0)
            
            logging.info(f"NeşeliÇiçekler güncelleme başlatıldı: {total_links} link")
            
            success_count = 0
            
            for i, link_data in enumerate(neseli_links):
                barcode = link_data['barcode']
                url = link_data['url']
                
                # Durumu güncelle
                update_scraping_status(
                    progress=i + 1,
                    current_item=f"{barcode} - NeşeliÇiçekler"
                )
                
                # Scraping yap - DÜZELTME: 4 parametre
                success = scrape_single_link(barcode, 0, url, username)
                
                if success:
                    success_count += 1
                    update_scraping_status(slot_0_processed=success_count)
                else:
                    error_msg = f"NeşeliÇiçekler scraping hatası: {barcode}"
                    update_scraping_status(error=error_msg)
                
                # Uzun bekleme
                time.sleep(random.uniform(NESELICICEKLER_DELAY_MIN, NESELICICEKLER_DELAY_MAX))
            
            logging.info(f"NeşeliÇiçekler güncelleme tamamlandı: {success_count}/{total_links} başarılı")
            
        except Exception as e:
            error_msg = f"NeşeliÇiçekler güncelleme hatası: {str(e)}"
            logging.error(error_msg)
            update_scraping_status(error=error_msg)
        finally:
            update_scraping_status(is_running=False, current_item="")
    
    # Eğer başka bir scraping devam ediyorsa başlatma
    if scraping_status['is_running']:
        logging.warning("Scraping zaten devam ediyor, NeşeliÇiçekler işlem başlatılmadı")
        return False
    
    # Arka planda çalıştır
    thread = threading.Thread(target=neseli_update_worker)
    thread.daemon = True
    thread.start()
    
    return True

def get_scraping_statistics() -> Dict:
    """
    Scraping istatistiklerini getirir
    """
    try:
        from competitor_tracking import get_neselicicekler_price_stats, get_total_prices_count
        
        stats = {
            'neselicicekler': get_neselicicekler_price_stats(),
            'competitor_total': get_total_prices_count(include_slot_0=False),
            'all_total': get_total_prices_count(include_slot_0=True),
            'scraping_status': get_update_status()
        }
        
        return stats
        
    except Exception as e:
        logging.error(f"İstatistik getirme hatası: {str(e)}")
        return {}