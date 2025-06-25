"""
Rakip Fiyat Takip Modülü - Web Scraping
Selenium ile Trendyol sayfalarından ürün bilgilerini çeker
YENİ: Slot 0 (NeşeliÇiçekler) desteği eklendi
GÜNCELLEME: Selenium ile JavaScript render desteği
"""

import time
import random
import logging
import threading
import re
from typing import Dict, Optional, List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

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

def setup_chrome_driver() -> webdriver.Chrome:
    """Chrome WebDriver'ı headless modda kuruluma hazırlar"""
    print(f"🔍 COMPETITOR DEBUG: Chrome driver kuruluyor...")
    
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument(f'--user-agent={random.choice(USER_AGENTS)}')
    
    print(f"🔍 COMPETITOR DEBUG: Chrome options ayarlandı (headless mode)")
    
    try:
        # Manuel path kullan
        service = Service('/usr/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print(f"✅ COMPETITOR DEBUG: Chrome driver başarıyla kuruldu")
        return driver
    except Exception as e:
        print(f"❌ COMPETITOR DEBUG: Chrome driver kurulum hatası: {str(e)}")
        logging.error(f"COMPETITOR DEBUG: Chrome driver hatası: {str(e)}")
        raise

def scrape_trendyol_product(url: str, slot_number: int = 1) -> Optional[Dict[str, str]]:
    """
    Selenium ile Trendyol ürün sayfasından bilgileri çeker
    YENİ: slot_number parametresi eklendi (slot 0 için özel işlemler)
    Returns: {'product_name': str, 'price': float, 'seller_name': str} or None
    """
    driver = None
    try:
        driver = setup_chrome_driver()
        
        # YENİ: Slot 0 için daha uzun bekleme
        if slot_number == 0:
            delay = random.uniform(NESELICICEKLER_DELAY_MIN, NESELICICEKLER_DELAY_MAX)
            logging.info(f"NeşeliÇiçekler slot scraping (daha uzun bekleme): {delay:.1f}s")
        else:
            delay = random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)
        
        time.sleep(delay)
        
        print(f"🔍 COMPETITOR DEBUG: Sayfaya gidiliyor: {url}")
        driver.get(url)
        
        # Sayfa yüklenene kadar bekle
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # JavaScript'in tam yüklenmesi için bekleme
        time.sleep(5)
        
        # Sayfayı aşağı kaydır (lazy loading için)
        driver.execute_script("window.scrollTo(0, 1000);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
        
        result = {
            'product_name': None,
            'price': None,
            'seller_name': None
        }
        
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
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                if element and element.text.strip():
                    product_name = element.text.strip()
                    
                    # YENİ: Slot 0 için özel loglama
                    if slot_number == 0:
                        logging.info(f"NeşeliÇiçekler ürün adı bulundu - Selector: {selector}, Değer: {product_name[:100]}...")
                    else:
                        logging.info(f"Ürün adı bulundu - Selector: {selector}, Değer: {product_name[:100]}...")
                    break
            except:
                continue
        
        result['product_name'] = product_name
        
        # Fiyatı çek - Product_scraper.py ile aynı mantık
        price = None
        
        print(f"🔍 COMPETITOR DEBUG: Price selector araması başlıyor... (Slot {slot_number})")
        
        # JavaScript'in yüklenmesi için ekstra bekleme
        print(f"🔍 COMPETITOR DEBUG: JavaScript yüklenmesi için 3 saniye bekleniyor...")
        time.sleep(3)
        
        # Sayfayı yeniden scroll et (lazy loading için)
        driver.execute_script("window.scrollTo(0, 600);")
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, 300);")
        time.sleep(1)
        
        price_selectors = [
            # YENİ: İndirimli fiyat önceliği (product_scraper.py ile uyumlu)
            '.price-view-discounted',            # İndirimli fiyat (611 TL)
            '[data-testid="price"] .price-view-discounted', # Daha spesifik indirimli
            '.price-view span:last-child',       # Price-view içindeki son span
            
            # YENİ: Kampanya fiyatları
            '.campaign-price .new-price',        # Kampanyalı fiyat için
            '.campaign-price-content .new-price', # Spesifik kampanya fiyatı
            'p.new-price',                       # p tag ile new-price
            '.campaign-price p.new-price',       # Campaign içi new-price
            
            # ESKİ: Mevcut selector'lar (korundu)
            '.prc-dsc',
            '.prc-slg',
            '.product-price .prc-dsc',
            
            # YENİ: Ek selector'lar
            '[data-testid="price-current-price"]', # Test ID ile
            '.price-current',                    # Mevcut fiyat
            'span[class*="price"]',              # Price içeren span
            '.prc-cntr .prc-dsc',               # Price container içi
            '.price-container span',             # Price container span
            'div[class*="price"] span',          # Price div içi span
            '.product-price span:last-child',    # Son span
            'span[data-testid*="price"]',        # Price test ID'li span
            
            # YENİ: Genel selector'lar
            '*[class*="price"]:not(:empty)',     # Price içeren boş olmayan elementler
            '*[class*="prc"]:not(:empty)',       # Prc içeren boş olmayan elementler
            
            # ESKİ: Eski container (korundu)
            '.product-price-container .prc-dsc',
            
            # YENİ: Sayfa kaynak kodunda arama (fallback)
            'body'  # Fallback: tüm sayfa içeriği
        ]
        
        price_found = False
        for i, selector in enumerate(price_selectors):
            print(f"🔍 COMPETITOR DEBUG: Price selector {i+1}/{len(price_selectors)} deneniyor: {selector} (Slot {slot_number})")
            
            # Son selector (body) için özel işlem - Sayfa kaynağında regex arama
            if selector == 'body':
                try:
                    print(f"🔍 COMPETITOR DEBUG: Fallback: Sayfa kaynak kodunda fiyat aranıyor...")
                    page_source = driver.page_source
                    
                    # Sayfa kaynağında fiyat pattern'lerini ara
                    price_patterns = [
                        r'([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)\s*TL',  # 2.959 TL, 2.959,50 TL
                        r'([0-9]{1,3}(?:\.[0-9]{3})*)\s*₺',                 # 2.959 ₺
                        r'"price"[^0-9]*([0-9]{1,6}(?:\.[0-9]{2})?)',       # JSON price field
                        r'price[^0-9]*([0-9]{1,6}(?:\.[0-9]{2})?)',         # price: 2959
                        r'fiyat[^0-9]*([0-9]{1,6}(?:\.[0-9]{2})?)',         # fiyat: 2959
                    ]
                    
                    for pattern in price_patterns:
                        matches = re.findall(pattern, page_source, re.IGNORECASE)
                        if matches:
                            for match in matches:
                                try:
                                    # Binlik ayracını kaldır ve float'a çevir
                                    price_text = match.replace('.', '').replace(',', '.')
                                    price_value = float(price_text)
                                    
                                    # Mantıklı fiyat aralığında mı kontrol et (10-1000000 TL)
                                    if 10 <= price_value <= 1000000:
                                        price = price_value
                                        print(f"✅ COMPETITOR DEBUG: FİYAT SAYFA KAYNAĞINDA BULUNDU! (Slot {slot_number})")
                                        print(f"✅ COMPETITOR DEBUG: Pattern: {pattern}")
                                        print(f"✅ COMPETITOR DEBUG: Match: {match} -> {price_value}")
                                        price_found = True
                                        break
                                except ValueError:
                                    continue
                            if price_found:
                                break
                    
                    if not price_found:
                        print(f"❌ COMPETITOR DEBUG: Sayfa kaynağında fiyat bulunamadı (Slot {slot_number})")
                        
                except Exception as e:
                    print(f"❌ COMPETITOR DEBUG: Sayfa kaynağı analiz hatası: {str(e)} (Slot {slot_number})")
                
                break  # body selector'ı son, döngüyü sonlandır
            
            # Normal selector'lar için
            try:
                # Element bulana kadar bekle (max 5 saniye)
                try:
                    element = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    print(f"✅ COMPETITOR DEBUG: Element WebDriverWait ile bulundu! (Slot {slot_number})")
                except:
                    # WebDriverWait başarısız olursa normal find_element dene
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                    print(f"✅ COMPETITOR DEBUG: Element find_element ile bulundu! (Slot {slot_number})")
                
                # Text alma ve boşluk kontrolü
                price_text = element.text.strip()
                
                # Text boşsa alternative attribute'ları dene
                if not price_text:
                    alternative_attributes = ['textContent', 'innerText', 'value', 'data-price', 'title']
                    for attr in alternative_attributes:
                        try:
                            attr_value = element.get_attribute(attr)
                            if attr_value and attr_value.strip():
                                price_text = attr_value.strip()
                                print(f"🔍 COMPETITOR DEBUG: Text '{attr}' attribute'unda bulundu: '{price_text}' (Slot {slot_number})")
                                break
                        except:
                            continue
                
                print(f"🔍 COMPETITOR DEBUG: Element text: '{price_text}' (Slot {slot_number})")
                
                if not price_text:
                    print(f"⚠️ COMPETITOR DEBUG: Element boş text döndürdü (Slot {slot_number})")
                    continue
                
                # Gelişmiş fiyat temizleme - product_scraper.py ile aynı mantık
                if price_text:
                    print(f"🔧 COMPETITOR DEBUG: Fiyat temizleme başlıyor... (Slot {slot_number})")
                    
                    # Sadece rakam, nokta, virgül ve boşluk karakterlerini al
                    price_clean = re.sub(r'[^\d\s,.]', '', price_text)
                    print(f"🔧 COMPETITOR DEBUG: İlk temizlik sonrası: '{price_clean}' (Slot {slot_number})")
                    
                    original_clean = price_clean
                    
                    # Noktayı binlik ayracı olarak kabul et, virgülü ondalık ayracı olarak
                    if ',' in price_clean and '.' in price_clean:
                        # Her ikisi varsa: nokta binlik, virgül ondalık
                        print(f"🔧 COMPETITOR DEBUG: Hem nokta hem virgül var - nokta binlik, virgül ondalık kabul ediliyor (Slot {slot_number})")
                        price_clean = price_clean.replace('.', '').replace(',', '.')
                        print(f"🔧 COMPETITOR DEBUG: Dönüşüm sonrası: '{price_clean}' (Slot {slot_number})")
                    

                    elif '.' in price_clean:
                        # Sadece nokta varsa kontrol et
                        parts = price_clean.split('.')
                        last_part_clean = parts[1].strip() if len(parts) > 1 else ""
                        print(f"🔧 COMPETITOR DEBUG: Sadece nokta var, parçalar: {parts}, son kısım temiz: '{last_part_clean}' (Slot {slot_number})")
                        
                        if len(parts) == 2 and len(last_part_clean) == 3 and last_part_clean.isdigit():
                            # 3 haneli rakam = binlik ayracı
                            print(f"🔧 COMPETITOR DEBUG: Son kısım 3 haneli rakam ({last_part_clean}) - binlik ayracı (Slot {slot_number})")
                            price_clean = price_clean.replace('.', '').replace(' ', '')
                            print(f"🔧 COMPETITOR DEBUG: Binlik ayracı kaldırıldı: '{price_clean}' (Slot {slot_number})")
                        else:
                            print(f"🔧 COMPETITOR DEBUG: Son kısım {len(last_part_clean) if len(parts) > 1 else 0} haneli - ondalık ayracı olarak bırakılıyor (Slot {slot_number})")
                    

                    elif ',' in price_clean:
                        # Sadece virgül varsa: ondalık ayracı olarak kabul et
                        print(f"🔧 COMPETITOR DEBUG: Sadece virgül var - ondalık ayracı olarak kabul ediliyor (Slot {slot_number})")
                        price_clean = price_clean.replace(',', '.')
                        print(f"🔧 COMPETITOR DEBUG: Virgül nokta ile değiştirildi: '{price_clean}' (Slot {slot_number})")
                    else:
                        print(f"🔧 COMPETITOR DEBUG: Ayraç yok, olduğu gibi bırakılıyor (Slot {slot_number})")
                    
                    # Boşlukları temizle
                    price_clean = price_clean.replace(' ', '')
                    print(f"🔧 COMPETITOR DEBUG: Boşluklar temizlendi: '{price_clean}' (Slot {slot_number})")
                    
                    if price_clean:
                        try:
                            parsed_price = float(price_clean)
                            
                            # Mantıklı fiyat aralığında mı kontrol et
                            if 10 <= parsed_price <= 1000000:
                                price = parsed_price
                                print(f"✅ COMPETITOR DEBUG: FİYAT BAŞARIYLA PARSE EDİLDİ! (Slot {slot_number})")
                                print(f"✅ COMPETITOR DEBUG: Kullanılan selector: '{selector}' (Slot {slot_number})")
                                print(f"✅ COMPETITOR DEBUG: Ham text: '{price_text}' (Slot {slot_number})")
                                print(f"✅ COMPETITOR DEBUG: Temizlenmiş text: '{original_clean}' -> '{price_clean}' (Slot {slot_number})")
                                print(f"✅ COMPETITOR DEBUG: Final fiyat: {parsed_price} (Slot {slot_number})")
                                
                                # YENİ: Slot 0 için özel loglama
                                if slot_number == 0:
                                    logging.info(f"NeşeliÇiçekler fiyat bulundu: {parsed_price}₺")
                                else:
                                    logging.info(f"Fiyat bulundu: {parsed_price}₺")
                                
                                price_found = True
                                break
                            else:
                                print(f"⚠️ COMPETITOR DEBUG: Fiyat mantıksız aralıkta ({parsed_price}), atlanıyor (Slot {slot_number})")
                                continue
                                
                        except ValueError as ve:
                            print(f"❌ COMPETITOR DEBUG: Float dönüşüm hatası: {str(ve)} (Slot {slot_number})")
                            print(f"❌ COMPETITOR DEBUG: Text: '{price_text}' -> Clean: '{price_clean}' (Slot {slot_number})")
                            continue
                
            except Exception as e:
                print(f"❌ COMPETITOR DEBUG: Selector hatası: {str(e)} (Slot {slot_number})")
                continue
        
        if not price_found:
            print(f"❌ COMPETITOR DEBUG: HİÇBİR YÖNTEMİLE FİYAT ALINAMADI! (Slot {slot_number})")
            print(f"❌ COMPETITOR DEBUG: Toplam {len(price_selectors)} yöntem denendi (Slot {slot_number})")
            print(f"❌ COMPETITOR DEBUG: Final result price: {price} (Slot {slot_number})")
        else:
            print(f"🎉 COMPETITOR DEBUG: FİYAT BAŞARIYLA BELİRLENDİ: {price} (Slot {slot_number})")
        
        result['price'] = price
        
        # Satıcı adını çek - Selenium ile
        seller_name = None
        
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
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                if element and element.text.strip():
                    seller_name = element.text.strip()
                    
                    # YENİ: Slot 0 için özel loglama
                    if slot_number == 0:
                        logging.info(f"NeşeliÇiçekler satıcı bulundu - Selector: {selector}, Değer: {seller_name}")
                    else:
                        logging.info(f"Satıcı bulundu - Selector: {selector}, Değer: {seller_name}")
                    break
            except:
                continue
        
        # YENİ: Slot 0 için NeşeliÇiçekler kontrolü
        if slot_number == 0:
            # NeşeliÇiçekler text'ini direkt ara
            try:
                neseli_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'NeşeliÇiçekler')]")
                if neseli_elements:
                    seller_name = neseli_elements[0].text.strip()
                    logging.info(f"NeşeliÇiçekler XPath ile bulundu: {seller_name}")
                elif not seller_name:
                    # Slot 0 ise ve satıcı bulunamazsa NeşeliÇiçekler olarak varsay
                    seller_name = "NeşeliÇiçekler"
                    logging.info(f"Slot 0 için varsayılan satıcı: {seller_name}")
            except:
                if not seller_name:
                    seller_name = "NeşeliÇiçekler"
        else:
            # Rakip ürünler için CenNetHome kontrolü
            try:
                cennet_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'CenNetHome')]")
                if cennet_elements:
                    seller_name = cennet_elements[0].text.strip()
                    logging.info(f"CenNetHome XPath ile bulundu: {seller_name}")
            except:
                pass
        
        # Eğer satıcı hala bulunamazsa
        if not seller_name:
            if slot_number == 0:
                logging.warning(f"NeşeliÇiçekler satıcı bulunamadı - URL: {url}")
                seller_name = "NeşeliÇiçekler"  # Varsayılan
            else:
                logging.warning(f"Satıcı bulunamadı - URL: {url}")
                seller_name = "Bilinmiyor"
        
        result['seller_name'] = seller_name
        
        # Sonuçları kontrol et
        if result['product_name'] and result['price'] is not None:
            return {
                'product_name': result['product_name'][:200],  # Uzunluk sınırı
                'price': result['price'],
                'seller_name': result['seller_name'][:100] if result['seller_name'] else ("NeşeliÇiçekler" if slot_number == 0 else "Bilinmiyor")
            }
        else:
            slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Slot {slot_number}"
            logging.warning(f"Eksik veri - {slot_info} - URL: {url}, Name: {result['product_name']}, Price: {result['price']}, Seller: {result['seller_name']}")
            return None
            
    except Exception as e:
        slot_info = "NeşeliÇiçekler" if slot_number == 0 else f"Slot {slot_number}"
        logging.error(f"Selenium scraping hatası - {slot_info} - {url}: {str(e)}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

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
                
                # Scraping yap
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
                
                # Scraping yap
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
                
                # Scraping yap
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