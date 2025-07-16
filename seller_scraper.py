"""
Satıcı İzleme ve Analiz Modülü - Web Scraping (Selenium)
Product scraper'dan aynı Selenium yapısını kullanır
Production/Local uyumlu Chrome driver setup
"""

import requests
from bs4 import BeautifulSoup
import time
import random
import logging
import threading
import os  # <- Bu satırı ekleyin
from typing import Dict, Optional, List
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

from seller_tracking import (
    get_active_seller_links, save_seller_data, 
    get_seller_statistics, parse_follower_count, parse_store_age
)

# Scraping ayarları (Product scraper ile aynı)
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

REQUEST_DELAY_MIN = 5  # Seller için daha uzun bekleme
REQUEST_DELAY_MAX = 10
REQUEST_TIMEOUT = 30

# Global scraping durumu
seller_scraping_status = {
    'is_running': False,
    'current_progress': 0,
    'total_items': 0,
    'current_item': '',
    'started_by': '',
    'start_time': None,
    'errors': [],
    'success_count': 0,
    'failed_count': 0
}

seller_scraping_lock = threading.Lock()

def setup_chrome_driver(max_retries=3) -> webdriver.Chrome:
    """
    Chrome WebDriver'ı headless modda kuruluma hazırlar
    DevToolsActivePort sorunu için özel ayarlar
    """
    
    for attempt in range(max_retries):
        try:
            print(f"🔍 SELLER DEBUG: Chrome driver kuruluyor (deneme {attempt + 1})...")
            
            # Mevcut chrome ve chromedriver process'lerini temizle
            if attempt > 0:
                try:
                    import subprocess
                    subprocess.run(['pkill', '-f', 'chrome'], check=False)
                    subprocess.run(['pkill', '-f', 'chromedriver'], check=False)
                    subprocess.run(['pkill', '-f', 'google-chrome'], check=False)
                    time.sleep(5)  # Daha uzun bekle
                except:
                    pass
            
            chrome_binary = '/usr/bin/google-chrome'
            print(f"✅ SELLER DEBUG: Chrome binary: {chrome_binary}")
            
            chrome_options = Options()
            
            # TEMEL HEADLESS AYARLAR
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            
            # DEVTOOLS SORUNU İÇİN ÖZEL AYARLAR
            chrome_options.add_argument('--remote-debugging-port=9222')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-background-timer-throttling')
            chrome_options.add_argument('--disable-backgrounding-occluded-windows')
            chrome_options.add_argument('--disable-renderer-backgrounding')
            chrome_options.add_argument('--disable-features=TranslateUI,BlinkGenPropertyTrees')
            chrome_options.add_argument('--disable-ipc-flooding-protection')
            
            # MEMORY VE PROCESS AYARLARI
            chrome_options.add_argument('--memory-pressure-off')
            chrome_options.add_argument('--max_old_space_size=4096')
            chrome_options.add_argument('--disable-background-networking')
            chrome_options.add_argument('--disable-default-apps')
            chrome_options.add_argument('--disable-sync')
            chrome_options.add_argument('--disable-translate')
            chrome_options.add_argument('--hide-scrollbars')
            chrome_options.add_argument('--metrics-recording-only')
            chrome_options.add_argument('--mute-audio')
            chrome_options.add_argument('--no-first-run')
            chrome_options.add_argument('--safebrowsing-disable-auto-update')
            chrome_options.add_argument('--disable-crash-reporter')
            chrome_options.add_argument('--disable-in-process-stack-traces')
            
            # WINDOW VE DISPLAY - SABIT DEĞERLER
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--start-maximized')
            
            # PERFORMANS AYARLARI
            chrome_options.add_argument('--disable-extensions')
            chrome_options.add_argument('--disable-plugins')
            chrome_options.add_argument('--disable-images')
            chrome_options.add_argument('--disable-javascript')  # Eğer JS gerekmiyorsa
            
            # NETWORK VE SECURITY
            chrome_options.add_argument('--disable-web-security')
            chrome_options.add_argument('--disable-features=VizDisplayCompositor')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_options.add_experimental_option('useAutomationExtension', False)
            
            # USER AGENT
            chrome_options.add_argument(f'--user-agent={random.choice(USER_AGENTS)}')
            
            # LOGGING - TAMAMEN KAPAT
            chrome_options.add_argument('--disable-logging')
            chrome_options.add_argument('--disable-gpu-logging')
            chrome_options.add_argument('--log-level=3')
            chrome_options.add_argument('--silent')
            chrome_options.add_argument('--quiet')
            
            # Chrome binary path'i ayarla
            chrome_options.binary_location = chrome_binary
            
            # Environment ayarları
            try:
                import os
                import tempfile
                
                # DISPLAY ayarı
                os.environ['DISPLAY'] = ':99'
                
                # Temporary directory oluştur - HER DENEMEDE YENİ
                temp_dir = tempfile.mkdtemp(prefix='chrome_', suffix=f'_{attempt}')
                chrome_options.add_argument(f'--user-data-dir={temp_dir}')
                chrome_options.add_argument(f'--data-path={temp_dir}')
                chrome_options.add_argument(f'--disk-cache-dir={temp_dir}')
                
                # Temp directory permissions
                os.chmod(temp_dir, 0o755)
                
                print(f"🔍 SELLER DEBUG: Temp directory: {temp_dir}")
                
            except Exception as env_error:
                print(f"⚠️ SELLER DEBUG: Environment ayarlama hatası: {str(env_error)}")
                pass
            
            print(f"🔍 SELLER DEBUG: Chrome options ayarlandı (headless mode)")
            
            chromedriver_binary = '/usr/bin/chromedriver'
            print(f"✅ SELLER DEBUG: ChromeDriver binary: {chromedriver_binary}")
            
            try:
                # Service için özel ayarlar
                service = Service(chromedriver_binary)
                service.creation_flags = 0
                
                # WebDriver için özel ayarlar
                chrome_options.add_argument('--disable-dev-shm-usage')  # Tekrar ekle
                chrome_options.add_argument('--disable-software-rasterizer')
                chrome_options.add_argument('--disable-background-timer-throttling')
                
                print(f"🔍 SELLER DEBUG: WebDriver başlatılıyor...")
                
                # Driver'ı başlat
                driver = webdriver.Chrome(service=service, options=chrome_options)
                
                # WebDriver ayarları
                driver.set_page_load_timeout(60)  # Daha uzun timeout
                driver.implicitly_wait(20)
                
                print(f"🔍 SELLER DEBUG: WebDriver başlatıldı, test ediliyor...")
                
                # Bot tespit önleme
                try:
                    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    print(f"✅ SELLER DEBUG: Bot tespit önleme ayarlandı")
                except Exception as js_error:
                    print(f"⚠️ SELLER DEBUG: Bot tespit önleme hatası: {str(js_error)}")
                
                # Basit test
                try:
                    driver.get("https://www.google.com")
                    title = driver.title
                    print(f"✅ SELLER DEBUG: Test başarılı - Title: {title}")
                except Exception as test_error:
                    print(f"⚠️ SELLER DEBUG: Test hatası: {str(test_error)}")
                    # Test başarısız olsa bile driver'ı döndür
                
                print(f"✅ SELLER DEBUG: Chrome driver başarıyla kuruldu (deneme {attempt + 1})")
                return driver
                
            except Exception as prod_error:
                print(f"⚠️ SELLER DEBUG: Production path başarısız (deneme {attempt + 1}): {str(prod_error)}")
                raise prod_error
                    
        except Exception as e:
            print(f"❌ SELLER DEBUG: Chrome driver kurulum hatası (deneme {attempt + 1}): {str(e)}")
            if attempt == max_retries - 1:
                logging.error(f"SELLER DEBUG: Chrome driver hatası: {str(e)}")
                raise
            time.sleep(10)  # Daha uzun bekle

# Chrome kurulumu kontrol etme fonksiyonu
def check_chrome_installation():
    """Chrome'un sisteme kurulu olup olmadığını kontrol eder"""
    try:
        import subprocess
        import os
        
        # Chrome binary yollarını kontrol et
        chrome_paths = [
            '/usr/bin/google-chrome',
            '/usr/bin/google-chrome-stable',
            '/usr/bin/chromium-browser',
            '/usr/bin/chromium'
        ]
        
        chrome_binary = None
        for path in chrome_paths:
            if os.path.exists(path):
                chrome_binary = path
                break
        
        if not chrome_binary:
            return False, "Chrome binary bulunamadı"
        
        # Chrome versiyonunu kontrol et
        try:
            result = subprocess.run([chrome_binary, '--version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version = result.stdout.strip()
                print(f"✅ Chrome kurulu: {version}")
                return True, version
            else:
                return False, f"Chrome version komutu başarısız: {result.stderr}"
        except subprocess.TimeoutExpired:
            return False, "Chrome version komutu timeout oldu"
        except Exception as e:
            return False, f"Chrome version kontrol hatası: {str(e)}"
            
    except Exception as e:
        return False, f"Chrome kurulum kontrol hatası: {str(e)}"

# ChromeDriver kurulumu kontrol etme fonksiyonu  
def check_chromedriver_installation():
    """ChromeDriver'ın sisteme kurulu olup olmadığını kontrol eder"""
    try:
        import subprocess
        import os
        
        # ChromeDriver binary yollarını kontrol et
        chromedriver_paths = [
            '/usr/bin/chromedriver',
            '/usr/local/bin/chromedriver',
            '/opt/chromedriver/chromedriver'
        ]
        
        chromedriver_binary = None
        for path in chromedriver_paths:
            if os.path.exists(path):
                chromedriver_binary = path
                break
        
        if not chromedriver_binary:
            return False, "ChromeDriver binary bulunamadı"
        
        # ChromeDriver versiyonunu kontrol et
        try:
            result = subprocess.run([chromedriver_binary, '--version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version = result.stdout.strip()
                print(f"✅ ChromeDriver kurulu: {version}")
                return True, version
            else:
                return False, f"ChromeDriver version komutu başarısız: {result.stderr}"
        except subprocess.TimeoutExpired:
            return False, "ChromeDriver version komutu timeout oldu"
        except Exception as e:
            return False, f"ChromeDriver version kontrol hatası: {str(e)}"
            
    except Exception as e:
        return False, f"ChromeDriver kurulum kontrol hatası: {str(e)}"

# Sistem durumunu kontrol etme fonksiyonu
def check_system_status():
    """Chrome ve ChromeDriver sisteminin durumunu kontrol eder"""
    print("🔍 SELLER DEBUG: Sistem durumu kontrol ediliyor...")
    
    # Chrome kontrolü
    chrome_ok, chrome_msg = check_chrome_installation()
    print(f"Chrome durumu: {'✅' if chrome_ok else '❌'} {chrome_msg}")
    
    # ChromeDriver kontrolü
    chromedriver_ok, chromedriver_msg = check_chromedriver_installation()
    print(f"ChromeDriver durumu: {'✅' if chromedriver_ok else '❌'} {chromedriver_msg}")
    
    if chrome_ok and chromedriver_ok:
        print("✅ SELLER DEBUG: Sistem hazır!")
        return True
    else:
        print("❌ SELLER DEBUG: Sistem hazır değil!")
        return False

# Geçici: Requests ile fallback scraper
def scrape_with_requests_fallback(url: str) -> Optional[Dict[str, any]]:
    """
    Chrome yokken geçici requests çözümü
    """
    try:
        headers = {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        
        time.sleep(random.uniform(3, 6))
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        print(f"🔍 FALLBACK: Requests ile sayfa çekildi: {len(response.text)} karakter")
        
        # Basit veri çekme
        result = {
            'seller_name': None,
            'seller_score': None,
            'product_count': None,
            'follower_count': None
        }
        
        # Title'dan satıcı adı çıkarma
        title = soup.title.string if soup.title else ""
        if title:
            # "Satıcı Adı - Trendyol" formatından çıkar
            if " - Trendyol" in title:
                result['seller_name'] = title.replace(" - Trendyol", "").strip()
        
        print(f"🔍 FALLBACK: Sonuç: {result}")
        return result
        
    except Exception as e:
        print(f"❌ FALLBACK: Requests hatası: {str(e)}")
        return None

def scrape_all_products_page_selenium(url: str) -> Optional[Dict[str, any]]:
    """
    Selenium ile Tüm Ürünler Sayfası'ndan veri çeker
    Chrome yoksa requests fallback kullanır
    Returns: {'seller_name': str, 'seller_score': float, 'product_count': int, 'follower_count': int}
    """
    # Önce Chrome driver dene
    try:
        driver = setup_chrome_driver()
    except Exception as e:
        print(f"❌ SELLER DEBUG: Selenium başarısız, requests fallback kullanılıyor")
        return scrape_with_requests_fallback(url)
    
    try:
        print(f"🔍 SELLER DEBUG: Tüm ürünler sayfası çekiliyor: {url}")
        driver.get(url)
        
        # Sayfa yüklenene kadar bekle
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # JavaScript'in tam yüklenmesi için bekleme
        time.sleep(random.uniform(3, 6))
        
        # Sayfayı scroll et (lazy loading için)
        driver.execute_script("window.scrollTo(0, 800);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
        
        result = {
            'seller_name': None,
            'seller_score': None,
            'product_count': None,
            'follower_count': None
        }
        
        # Bot tespit kontrolü
        page_title = driver.title
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            print("🤖 SELLER DEBUG: Bot tespit sayfası algılandı!")
            return None
        
        print(f"🔍 SELLER DEBUG: Sayfa başlığı: {page_title}")
        
        # Satıcı Adı - Gelişmiş selector'lar
        seller_name_selectors = [
            'h1.seller-store__name.seller-info__name.ss-header-seller',
            'h1.seller-store__name',
            'h1[class*="seller-store__name"]',
            'h1[class*="seller-info__name"]',
            '.seller-store__name',
            '.seller-info__name',
            'h1[style*="color:#FFFFFF"]',
            '.seller-name',
            '.store-name',
            '[data-testid="seller-name"]',
            '.merchant-name',
            'h1'  # Fallback
        ]
        
        for selector in seller_name_selectors:
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                if element and element.text.strip():
                    result['seller_name'] = element.text.strip()
                    print(f"✅ SELLER DEBUG: Satıcı adı bulundu ({selector}): {result['seller_name']}")
                    break
            except:
                continue
        
        # Satıcı Puanı - Gelişmiş selector'lar
        seller_score_selectors = [
            '.seller-store__score.score-actual.ss-header-score',
            '.seller-store__score',
            '.score-actual',
            '.ss-header-score',
            'div[class*="seller-store__score"]',
            'div[style*="background:#049B24"]',
            'div[style*="background-color: rgb(4, 155, 36)"]',
            '.seller-score',
            '.merchant-score',
            '[data-testid="seller-score"]'
        ]
        
        for selector in seller_score_selectors:
            try:
                element = driver.find_element(By.CSS_SELECTOR, selector)
                if element:
                    score_text = element.text.strip()
                    print(f"🔍 SELLER DEBUG: Satıcı puanı element text ({selector}): '{score_text}'")
                    
                    score_match = re.search(r'(\d+[,.]?\d*)', score_text)
                    if score_match:
                        result['seller_score'] = float(score_match.group(1).replace(',', '.'))
                        print(f"✅ SELLER DEBUG: Satıcı puanı bulundu ({selector}): {result['seller_score']}")
                        break
            except:
                continue
        
        # Ürün Sayısı - Sayfa içeriğinde arama
        try:
            page_source = driver.page_source
            
            product_patterns = [
                r'(\d+(?:[.,]\d+)*)\s*sonuç',
                r'(\d+(?:[.,]\d+)*)\s*[Üü]r[üu]n',
                r'(\d+(?:[.,]\d+)*)\s*adet\s*[üu]r[üu]n',
                r'(\d+(?:[.,]\d+)*)\s*product',
                r'Toplam\s*(\d+(?:[.,]\d+)*)',
                r'(\d+(?:[.,]\d+)*)\s*ürün\s*bulundu',
                r'(\d+(?:[.,]\d+)*)\s*results?'
            ]
            
            for pattern in product_patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)  # Tüm eşleşmeleri bul
                print(f"🔍 PATTERN '{pattern}' TÜM EŞLEŞMELER: {matches}")
                
                match = re.search(pattern, page_source, re.IGNORECASE)  # İlk eşleşmeyi bul
                if match:
                    print(f"🔍 İLK EŞLEŞME: '{match.group(0)}' - SAYI: '{match.group(1)}'")
                    try:
                        product_count_str = match.group(1)
                        print(f"🔍 RAW MATCH: '{product_count_str}'")
                        product_count_str = product_count_str.replace(',', '').replace('.', '')
                        print(f"🔍 AFTER REPLACE: '{product_count_str}'")
                        result['product_count'] = int(product_count_str)
                        print(f"✅ SELLER DEBUG: Ürün sayısı bulundu (pattern {pattern}): {result['product_count']}")
                        break
                    except ValueError:
                        continue
        except Exception as e:
            print(f"⚠️ SELLER DEBUG: Ürün sayısı arama hatası: {str(e)}")
        
        # Takipçi Sayısı - Sayfa içeriğinde arama - TÜRKİYE FORMATI (VIRGÜL)
        try:
            page_source = driver.page_source
            
            follower_patterns = [
                # YENİ: DOĞRU SIRALAMA - Sayı Takipçi'den ÖNCE
                r'(\d+[,.]?\d*[BMK]?)\s*[Tt]akip[çc]i',        # "14,6B Takipçi" 
                r'(\d+[,.]?\d*[BMK]?)\s*follower',             # "14,6B follower"
                r'(\d+[,.]?\d*[BMK]?)\s*ki[şs]i\s*takip',      # "14,6B kişi takip"
                r'(\d+[,.]?\d*[BMK]?)\s*followers?',           # "14,6B followers"
                
                # HTML'den çıkarılan spesifik pattern
                r'>(\d+,\d+B)</span>\s*Takip[çc]i',           # ">14,6B</span> Takipçi"
                r'font-weight[^>]*>(\d+,\d+B)</span>',        # "font-weight: 600;">14,6B</span>"
                
                # Boşluklu formatlar
                r'(\d+[,.]?\d*)\s*[BMK]\s*[Tt]akip[çc]i',     # "14,6 B Takipçi" 
                r'(\d+[,.]?\d*)\s*[BMK]\s*follower',          # "14,6 B follower"
                
                # Eski formatlar (TERS - Takipçi önce)
                r'[Tt]akip[çc]i[^0-9]*(\d+[,.]?\d*[BMK]?)',   # ESKİ: "Takipçi: 14,6B" 
                
                # Türkiye spesifik formatları
                r'(\d+,\d+B)\s*[Tt]akip[çc]i',               # "14,6B Takipçi"
                r'(\d+,\d+K)\s*[Tt]akip[çc]i',               # "1,2K Takipçi"
                r'(\d+,\d+M)\s*[Tt]akip[çc]i'                # "1,5M Takipçi"
            ]
            
            print(f"🔍 SELLER DEBUG: Takipçi aramaya başlıyor - sayfa uzunluğu: {len(page_source)}")
            
            # Debug: "14,6" veya "takipçi" içeren kısımları bul
            debug_lines = []
            for line in page_source.split('\n'):
                line_clean = line.strip()
                if ('takip' in line_clean.lower() and any(char.isdigit() for char in line_clean)) or '14,6' in line_clean:
                    debug_lines.append(line_clean)
            
            print(f"🔍 SELLER DEBUG: Takipçi ile ilgili {len(debug_lines)} satır bulundu")
            for i, line in enumerate(debug_lines[:5]):  # İlk 5 tanesini göster
                print(f"  {i+1}. {line[:150]}...")
            
            # Özel olarak "14,6B" ara
            if "14,6B" in page_source:
                print(f"✅ SELLER DEBUG: '14,6B' metni sayfada bulundu!")
                # 14,6B çevresindeki metni bul
                index = page_source.find("14,6B")
                surrounding = page_source[max(0, index-50):index+100]
                print(f"🔍 SELLER DEBUG: '14,6B' çevresi: '{surrounding}'")
            else:
                print(f"❌ SELLER DEBUG: '14,6B' metni sayfada bulunamadı")
                # Alternatif formatları ara
                if "14.6B" in page_source:
                    print(f"✅ SELLER DEBUG: '14.6B' (noktalı) bulundu")
                if "14,6" in page_source:
                    print(f"✅ SELLER DEBUG: '14,6' (virgüllü sayı) bulundu")
                if "14.6" in page_source:
                    print(f"✅ SELLER DEBUG: '14.6' (noktalı sayı) bulundu")
            
            for pattern in follower_patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    print(f"🔍 SELLER DEBUG: Pattern '{pattern}' ile bulunan matches: {matches}")
                    for match in matches:
                        try:
                            follower_text = str(match)
                            parsed_count = parse_follower_count(follower_text)
                            if parsed_count > 0:
                                result['follower_count'] = parsed_count
                                print(f"✅ SELLER DEBUG: Takipçi sayısı bulundu (pattern {pattern}): {follower_text} -> {parsed_count}")
                                break
                        except Exception as e:
                            print(f"⚠️ SELLER DEBUG: Parse hatası: {follower_text} - {str(e)}")
                            continue
                if result.get('follower_count'):
                    break
                    
            if not result.get('follower_count'):
                print(f"❌ SELLER DEBUG: Hiçbir takipçi pattern'i çalışmadı")
                
        except Exception as e:
            print(f"⚠️ SELLER DEBUG: Takipçi sayısı arama hatası: {str(e)}")
        
        print(f"📊 SELLER DEBUG: Tüm ürünler sayfası sonucu: {result}")
        return result
        
    except Exception as e:
        print(f"❌ SELLER DEBUG: Tüm ürünler scraping hatası: {str(e)}")
        logging.error(f"Tüm ürünler scraping hatası - {url}: {str(e)}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def scrape_seller_profile_page_selenium(url: str) -> Optional[Dict[str, any]]:
    """
    Selenium ile Satıcı Profil Sayfası'ndan veri çeker
    Returns: {'store_age': int, 'location': str, 'total_reviews': int, 'total_comments': int, 'overall_rating': float}
    """
    # Önce Chrome driver dene
    try:
        driver = setup_chrome_driver()
    except Exception as e:
        print(f"❌ SELLER DEBUG: Selenium başarısız, profil scraping atlanıyor")
        return {
            'store_age': None,
            'location': None,
            'total_reviews': None,
            'total_comments': None,
            'overall_rating': None
        }
    
    try:
        print(f"🔍 SELLER DEBUG: Satıcı profil sayfası çekiliyor: {url}")
        driver.get(url)
        
        # Sayfa yüklenene kadar bekle
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # JavaScript'in tam yüklenmesi için bekleme
        time.sleep(random.uniform(3, 6))
        
        # Sayfayı scroll et
        driver.execute_script("window.scrollTo(0, 800);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
        
        result = {
            'store_age': None,
            'location': None,
            'total_reviews': None,
            'total_comments': None,
            'overall_rating': None
        }
        
        # Bot tespit kontrolü
        page_title = driver.title
        if "robot" in page_title.lower() or "captcha" in page_title.lower():
            print("🤖 SELLER DEBUG: Bot tespit sayfası algılandı!")
            return None
        
        print(f"🔍 SELLER DEBUG: Profil sayfası başlığı: {page_title}")
        
        page_source = driver.page_source
        
        # Mağaza Yaşı - Pattern matching
        try:
            age_patterns = [
                r'(\d+)\s*[Yy][ıi]l',
                r'(\d+)\s*year',
                r'Ma[ğg]aza\s*ya[şs][ıi][^0-9]*(\d+)',
                r'[Aa]ç[ıi]l[ıi][şs]\s*tarihi[^0-9]*(\d+)',
                r'(\d+)\s*yıldır',
                r'(\d+)\s*senedir'
            ]
            
            for pattern in age_patterns:
                match = re.search(pattern, page_source, re.IGNORECASE)
                if match:
                    try:
                        result['store_age'] = int(match.group(1))
                        print(f"✅ SELLER DEBUG: Mağaza yaşı bulundu (pattern {pattern}): {result['store_age']}")
                        break
                    except ValueError:
                        continue
        except Exception as e:
            print(f"⚠️ SELLER DEBUG: Mağaza yaşı arama hatası: {str(e)}")
        
        # Konum - Geliştirilmiş ve spesifik çekme
        try:
            # Önce spesifik selector'larla dene
            location_selectors = [
                '.seller-info-container__wrapper__text-container__value',  # HTML'den çıkan class
                'span[class*="text-container__value"]',                     # Partial class
                '.seller-info-container span[class*="value"]'               # Container içinde value
            ]
            
            for selector in location_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    for i, element in enumerate(elements):
                        # Bir önceki element'i kontrol et (title olabilir)
                        try:
                            parent = element.find_element(By.XPATH, "..")
                            title_element = parent.find_element(By.CSS_SELECTOR, '.seller-info-container__wrapper__text-container__title')
                            title_text = title_element.text.strip().lower()
                            
                            if 'konum' in title_text:
                                location_text = element.text.strip()
                                if location_text and len(location_text) < 50:  # Makul bir şehir adı uzunluğu
                                    result['location'] = location_text
                                    print(f"✅ SELLER DEBUG: Konum bulundu (spesifik selector): {location_text}")
                                    break
                        except:
                            continue
                    
                    if result.get('location'):
                        break
                except:
                    continue
            
            # Eğer spesifik selector çalışmazsa pattern matching dene
            if not result.get('location'):
                cities = [
                    'İstanbul', 'Ankara', 'İzmir', 'Bursa', 'Antalya', 'Adana', 'Konya', 'Gaziantep',
                    'Mersin', 'Diyarbakır', 'Kayseri', 'Eskişehir', 'Urfa', 'Malatya', 'Erzurum',
                    'Van', 'Batman', 'Elazığ', 'İçel', 'Sivas', 'Manisa', 'Tarsus', 'Kahramanmaraş',
                    'Erzincan', 'Ordu', 'Balıkesir', 'Kırıkkale', 'Kütahya', 'Tekirdağ', 'Afyon',
                    'Zonguldak', 'Çorum', 'Denizli', 'Isparta', 'Samsun', 'Trabzon', 'Sakarya',
                    'Kocaeli', 'Hatay', 'Mardin', 'Şanlıurfa', 'Adıyaman', 'Muğla', 'Aksaray'
                ]
                
                # "Konum" kelimesinden sonra şehir ara
                location_patterns = [
                    r'[Kk]onum[^a-zA-ZığüşöçİĞÜŞÖÇ]*([a-zA-ZığüşöçİĞÜŞÖÇ\s]+)',
                    r'>Konum</span><span[^>]*>([^<]+)<',  # HTML tag pattern
                    r'title">Konum</span><span[^>]*>([^<]+)<'
                ]
                
                for pattern in location_patterns:
                    match = re.search(pattern, page_source, re.IGNORECASE)
                    if match:
                        location_text = match.group(1).strip()
                        for city in cities:
                            if city in location_text:
                                result['location'] = city
                                print(f"✅ SELLER DEBUG: Konum bulundu (pattern matching): {city}")
                                break
                        if result.get('location'):
                            break
                            
        except Exception as e:
            print(f"⚠️ SELLER DEBUG: Konum arama hatası: {str(e)}")
        
        # Genel Rating - Profil sayfasında arama (HTML'den çıkarılan)
        try:
            rating_selectors = [
                '.product-review-section-wrapper__wrapper__rating_wrapper_left__rating_value',  # HTML'den
                'span[class*="rating_value"]',  # Partial class
                'span[class*="rating_wrapper_left"]',  # Parent class
                '.product-review-section span[class*="rating"]',  # Section içinde
                '.rating-value',  # Genel selector
                '.overall-rating',  # Genel selector
                '[data-testid="rating"]',  # Test ID
                'div[class*="rating"] span',  # Div içinde span
                '.rating-score'  # Score class
            ]
            
            print(f"🔍 SELLER DEBUG: Rating aranıyor - {len(rating_selectors)} selector denenecek")
            
            for selector in rating_selectors:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    print(f"🔍 SELLER DEBUG: Rating selector ({selector}) - {len(elements)} element bulundu")
                    
                    for element in elements:
                        rating_text = element.text.strip()
                        print(f"🔍 SELLER DEBUG: Rating element text ({selector}): '{rating_text}'")
                        
                        if rating_text:
                            # Rating değerini çıkar (4.2 gibi)
                            rating_match = re.search(r'(\d+[,.]?\d*)', rating_text)
                            if rating_match:
                                try:
                                    rating_value = float(rating_match.group(1).replace(',', '.'))
                                    if 0 <= rating_value <= 5:  # Geçerli rating aralığı
                                        result['overall_rating'] = rating_value
                                        print(f"✅ SELLER DEBUG: Genel rating bulundu ({selector}): {result['overall_rating']}")
                                        break
                                except ValueError:
                                    continue
                    if result.get('overall_rating'):
                        break
                except Exception as e:
                    print(f"⚠️ SELLER DEBUG: Rating selector hatası ({selector}): {str(e)}")
                    continue
            
            # Pattern matching ile de dene
            if not result.get('overall_rating'):
                print(f"🔍 SELLER DEBUG: Selector'lar başarısız, pattern matching deneniyor")
                page_source = driver.page_source
                
                # "4.2" gibi sayıları ara
                if "4.2" in page_source:
                    print(f"✅ SELLER DEBUG: '4.2' metni sayfada bulundu!")
                    # 4.2 çevresindeki metni bul
                    index = page_source.find("4.2")
                    surrounding = page_source[max(0, index-100):index+100]
                    print(f"🔍 SELLER DEBUG: '4.2' çevresi: '{surrounding[:200]}'")
                else:
                    print(f"❌ SELLER DEBUG: '4.2' metni sayfada bulunamadı")
                
                rating_patterns = [
                    r'rating_value[^>]*>(\d+[,.]?\d*)',  # HTML class'ından
                    r'[Gg]enel\s*[Rr]ating[^0-9]*(\d+[,.]?\d*)',
                    r'[Gg]enel\s*[Pp]uan[^0-9]*(\d+[,.]?\d*)',
                    r'[Oo]rtalama\s*[Pp]uan[^0-9]*(\d+[,.]?\d*)',
                    r'(\d+[,.]?\d*)\s*/\s*5',
                    r'[Rr]ating[^0-9]*(\d+[,.]?\d*)',
                    r'(\d+[,.]?\d*)\s*puan'
                ]
                
                for pattern in rating_patterns:
                    matches = re.findall(pattern, page_source, re.IGNORECASE)
                    if matches:
                        print(f"🔍 SELLER DEBUG: Rating pattern '{pattern}' ile bulunan matches: {matches}")
                        for match in matches:
                            try:
                                rating_text = str(match).replace(',', '.')
                                rating_value = float(rating_text)
                                if 0 <= rating_value <= 5:
                                    result['overall_rating'] = rating_value
                                    print(f"✅ SELLER DEBUG: Genel rating bulundu (pattern {pattern}): {result['overall_rating']}")
                                    break
                            except ValueError:
                                continue
                        if result.get('overall_rating'):
                            break
                            
        except Exception as e:
            print(f"⚠️ SELLER DEBUG: Genel rating arama hatası: {str(e)}")
     

        # YENİ GÜNCELLEMEED KOD (Bu kod ile değiştirin):
        try:
            print(f"🔍 SELLER DEBUG: Reviews/Comments aranıyor...")
            
            # 1) Önce sayfa kaynak kodunda direct arama yap
            page_source = driver.page_source
            
            # DEBUG: "Değerlendirme" ve "Yorum" kelimelerini içeren satırları bul
            debug_lines = []
            for line_num, line in enumerate(page_source.split('\n')):
                line_clean = line.strip().lower()
                if ('değerlendirme' in line_clean or 'yorum' in line_clean) and any(char.isdigit() for char in line_clean):
                    debug_lines.append((line_num, line.strip()))
            
            print(f"🔍 SELLER DEBUG: Değerlendirme/Yorum ile ilgili {len(debug_lines)} satır bulundu")
            for i, (line_num, line) in enumerate(debug_lines[:10]):  # İlk 10 tanesini göster
                print(f"  {i+1}. Satır {line_num}: {line[:100]}...")
            
            # 2) Pattern matching ile sayfa kaynağında ara
            review_patterns = [
                r'(\d+(?:[.,]\d+)*)\s*Değerlendirme',         # "664 Değerlendirme"
                r'(\d+(?:[.,]\d+)*)\s*değerlendirme',         # "664 değerlendirme"
                r'(\d+(?:[.,]\d+)*)\s*[Dd]eğerlendirme',      # Case insensitive
                r'>(\d+(?:[.,]\d+)*)</span>\s*Değerlendirme', # HTML tag içinde
                r'Değerlendirme[^0-9]*(\d+(?:[.,]\d+)*)',     # Ters sıralama
            ]
            
            comment_patterns = [
                r'(\d+(?:[.,]\d+)*)\s*Yorum',                 # "426 Yorum"
                r'(\d+(?:[.,]\d+)*)\s*yorum',                 # "426 yorum"
                r'(\d+(?:[.,]\d+)*)\s*[Yy]orum',              # Case insensitive
                r'>(\d+(?:[.,]\d+)*)</span>\s*Yorum',         # HTML tag içinde
                r'Yorum[^0-9]*(\d+(?:[.,]\d+)*)',             # Ters sıralama
            ]
            
            # Reviews arama
            for pattern in review_patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    print(f"🔍 SELLER DEBUG: Review pattern '{pattern}' ile bulunan matches: {matches}")
                    for match in matches:
                        try:
                            review_count_str = str(match).replace(',', '').replace('.', '')
                            review_count = int(review_count_str)
                            if review_count > 0:  # 0'dan büyük olmalı
                                result['total_reviews'] = review_count
                                print(f"✅ SELLER DEBUG: Total reviews bulundu (pattern): {result['total_reviews']}")
                                break
                        except ValueError:
                            continue
                if result.get('total_reviews'):
                    break
            
            # Comments arama  
            for pattern in comment_patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    print(f"🔍 SELLER DEBUG: Comment pattern '{pattern}' ile bulunan matches: {matches}")
                    for match in matches:
                        try:
                            comment_count_str = str(match).replace(',', '').replace('.', '')
                            comment_count = int(comment_count_str)
                            if comment_count > 0:  # 0'dan büyük olmalı
                                result['total_comments'] = comment_count
                                print(f"✅ SELLER DEBUG: Total comments bulundu (pattern): {result['total_comments']}")
                                break
                        except ValueError:
                            continue
                if result.get('total_comments'):
                    break
            
            # 3) Eğer pattern matching başarısızsa, CSS selector'larla dene
            if not result.get('total_reviews') or not result.get('total_comments'):
                print(f"🔍 SELLER DEBUG: Pattern matching başarısız, CSS selector'lar deneniyor...")
                
                # Gelişmiş CSS selector'lar
                review_selectors = [
                    '.product-review-section__review-count',
                    'span[class*="review-count"]',
                    'span[class*="degerlendirme"]',
                    '[data-testid*="review"]',
                    '.review-count',
                    '.degerlendirme-count',
                    'span:contains("Değerlendirme")',  # jQuery style (Selenium'da çalışmaz ama referans için)
                ]
                
                comment_selectors = [
                    '.product-review-section__comment-count', 
                    'span[class*="comment-count"]',
                    'span[class*="yorum"]',
                    '[data-testid*="comment"]',
                    '.comment-count',
                    '.yorum-count'
                ]
                
                # Reviews için selector deneme
                for selector in review_selectors:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        print(f"🔍 SELLER DEBUG: Review selector ({selector}) - {len(elements)} element bulundu")
                        
                        for element in elements:
                            text = element.text.strip()
                            print(f"🔍 SELLER DEBUG: Review element text ({selector}): '{text}'")
                            
                            if text and 'değerlendirme' in text.lower():
                                number_match = re.search(r'(\d+(?:[.,]\d+)*)', text)
                                if number_match:
                                    result['total_reviews'] = int(number_match.group(1).replace(',', '').replace('.', ''))
                                    print(f"✅ SELLER DEBUG: Total reviews bulundu (selector): {result['total_reviews']}")
                                    break
                        
                        if result.get('total_reviews'):
                            break
                    except Exception as e:
                        print(f"⚠️ SELLER DEBUG: Review selector hatası ({selector}): {str(e)}")
                
                # Comments için selector deneme 
                for selector in comment_selectors:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        print(f"🔍 SELLER DEBUG: Comment selector ({selector}) - {len(elements)} element bulundu")
                        
                        for element in elements:
                            text = element.text.strip()
                            print(f"🔍 SELLER DEBUG: Comment element text ({selector}): '{text}'")
                            
                            if text and 'yorum' in text.lower() and 'yayınlama' not in text.lower():
                                number_match = re.search(r'(\d+(?:[.,]\d+)*)', text)
                                if number_match:
                                    result['total_comments'] = int(number_match.group(1).replace(',', '').replace('.', ''))
                                    print(f"✅ SELLER DEBUG: Total comments bulundu (selector): {result['total_comments']}")
                                    break
                        
                        if result.get('total_comments'):
                            break
                    except Exception as e:
                        print(f"⚠️ SELLER DEBUG: Comment selector hatası ({selector}): {str(e)}")
            
            # 4) Son olarak JavaScript çalıştırıp tekrar dene
            if not result.get('total_reviews') or not result.get('total_comments'):
                print(f"🔍 SELLER DEBUG: JavaScript ile sayfa yeniden yükleniyor...")
                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(3)
                    driver.execute_script("window.scrollTo(0, 0);")
                    time.sleep(2)
                    
                    # Tekrar pattern matching dene
                    page_source = driver.page_source
                    
                    if not result.get('total_reviews'):
                        match = re.search(r'(\d+(?:[.,]\d+)*)\s*[Dd]eğerlendirme', page_source)
                        if match:
                            result['total_reviews'] = int(match.group(1).replace(',', '').replace('.', ''))
                            print(f"✅ SELLER DEBUG: Total reviews bulundu (JS scroll sonrası): {result['total_reviews']}")
                    
                    if not result.get('total_comments'):
                        match = re.search(r'(\d+(?:[.,]\d+)*)\s*[Yy]orum', page_source)
                        if match:
                            result['total_comments'] = int(match.group(1).replace(',', '').replace('.', ''))
                            print(f"✅ SELLER DEBUG: Total comments bulundu (JS scroll sonrası): {result['total_comments']}")
                            
                except Exception as js_error:
                    print(f"⚠️ SELLER DEBUG: JavaScript scroll hatası: {str(js_error)}")
            
            # 5) Eğer hâlâ bulunamadıysa varsayılan değerleri set et
            if not result.get('total_reviews'):
                result['total_reviews'] = 0
                print(f"❌ SELLER DEBUG: Total reviews bulunamadı, varsayılan değer: 0")
            
            if not result.get('total_comments'):
                result['total_comments'] = 0
                print(f"❌ SELLER DEBUG: Total comments bulunamadı, varsayılan değer: 0")
            
            print(f"📊 SELLER DEBUG: Final reviews/comments: Reviews={result.get('total_reviews')}, Comments={result.get('total_comments')}")
                
        except Exception as e:
            print(f"⚠️ SELLER DEBUG: Reviews/Comments arama genel hatası: {str(e)}")
            result['total_reviews'] = 0
            result['total_comments'] = 0

        print(f"📊 SELLER DEBUG: Profil sayfası sonucu: {result}")
        return result
        
    except Exception as e:
        print(f"❌ SELLER DEBUG: Profil scraping hatası: {str(e)}")
        logging.error(f"Profil scraping hatası - {url}: {str(e)}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def scrape_single_seller(seller_link_id: int, all_products_url: str, seller_profile_url: str, scraped_by: str) -> bool:
    """
    Tek bir satıcı için complete scraping yapar (Selenium ile)
    """
    try:
        print(f"🔍 SELLER DEBUG: Satıcı scraping başlatılıyor: {seller_link_id}")
        
        # Tüm ürünler sayfasından veri çek
        all_products_data = scrape_all_products_page_selenium(all_products_url)
        if not all_products_data:
            print(f"❌ SELLER DEBUG: Tüm ürünler sayfası veri çekme başarısız: {all_products_url}")
            return False
        
        # İki sayfa arası bekleme
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        
        # Profil sayfasından veri çek
        profile_data = scrape_seller_profile_page_selenium(seller_profile_url)
        if not profile_data:
            print(f"⚠️ SELLER DEBUG: Profil sayfası veri çekme başarısız, varsayılan değerlerle devam: {seller_profile_url}")
            profile_data = {
                'store_age': 0,
                'location': '',
                'total_reviews': 0,
                'total_comments': 0,
                'overall_rating': 0.0
            }
        
        # Verileri birleştir - Varsayılan değerler ekle
        combined_data = {
            'seller_name': all_products_data.get('seller_name', ''),
            'seller_score': all_products_data.get('seller_score', 0.0),
            'product_count': all_products_data.get('product_count', 0),
            'follower_count': all_products_data.get('follower_count', 0),
            'store_age': profile_data.get('store_age', 0),
            'location': profile_data.get('location', ''),
            'total_reviews': profile_data.get('total_reviews', 0),      # Varsayılan: 0
            'total_comments': profile_data.get('total_comments', 0),    # Varsayılan: 0
            'overall_rating': profile_data.get('overall_rating', 0.0)   # Varsayılan: 0.0
        }
        
        print(f"📊 SELLER DEBUG: Kaydedilecek birleşik veri: {combined_data}")
        
        # Veri kaydet
        success = save_seller_data(
            seller_link_id=seller_link_id,
            seller_name=combined_data['seller_name'],
            seller_score=combined_data['seller_score'],
            product_count=combined_data['product_count'],
            follower_count=combined_data['follower_count'],
            store_age=combined_data['store_age'],
            location=combined_data['location'],
            total_reviews=combined_data['total_reviews'],
            total_comments=combined_data['total_comments'],
            overall_rating=combined_data['overall_rating'],
            scraped_by=scraped_by
        )
        
        if success:
            print(f"✅ SELLER DEBUG: Satıcı scraping başarılı: {seller_link_id}")
            return True
        else:
            print(f"❌ SELLER DEBUG: Veri kaydetme hatası: {seller_link_id}")
            return False
            
    except Exception as e:
        print(f"❌ SELLER DEBUG: Satıcı scraping exception: {seller_link_id} - {str(e)}")
        return False

# Durum yönetimi fonksiyonları
def update_seller_scraping_status(is_running: bool = None, progress: int = None, 
                                 total: int = None, current_item: str = None,
                                 started_by: str = None, error: str = None,
                                 success_count: int = None, failed_count: int = None):
    """Scraping durumunu günceller"""
    global seller_scraping_status
    
    with seller_scraping_lock:
        if is_running is not None:
            seller_scraping_status['is_running'] = is_running
            if is_running:
                seller_scraping_status['start_time'] = time.time()
                seller_scraping_status['errors'] = []
                seller_scraping_status['success_count'] = 0
                seller_scraping_status['failed_count'] = 0
            
        if progress is not None:
            seller_scraping_status['current_progress'] = progress
            
        if total is not None:
            seller_scraping_status['total_items'] = total
            
        if current_item is not None:
            seller_scraping_status['current_item'] = current_item
            
        if started_by is not None:
            seller_scraping_status['started_by'] = started_by
            
        if error is not None:
            seller_scraping_status['errors'].append(error)
            
        if success_count is not None:
            seller_scraping_status['success_count'] = success_count
            
        if failed_count is not None:
            seller_scraping_status['failed_count'] = failed_count

def get_seller_scraping_status() -> Dict:
    """Mevcut scraping durumunu döndürür"""
    with seller_scraping_lock:
        status = seller_scraping_status.copy()
        if status['start_time']:
            status['elapsed_time'] = time.time() - status['start_time']
        else:
            status['elapsed_time'] = 0
        return status

def start_single_seller_scraping(seller_link_id: int, all_products_url: str, seller_profile_url: str, scraped_by: str):
    """
    Tek satıcı için arka planda scraping başlatır (yeni satıcı eklendiğinde)
    """
    def scrape_worker():
        try:
            success = scrape_single_seller(seller_link_id, all_products_url, seller_profile_url, scraped_by)
            if success:
                logging.info(f"Tek satıcı scraping tamamlandı: {all_products_url}")
            else:
                logging.error(f"Tek satıcı scraping başarısız: {all_products_url}")
        except Exception as e:
            logging.error(f"Tek satıcı scraping worker hatası: {str(e)}")
    
    # Arka planda çalıştır
    thread = threading.Thread(target=scrape_worker)
    thread.daemon = True
    thread.start()

def start_manual_seller_update(username: str) -> bool:
    """
    Manuel güncelleme başlatır (Admin yetkisi gerekli)
    Tüm aktif satıcılar için scraping yapar
    """
    def manual_update_worker():
        try:
            update_seller_scraping_status(is_running=True, started_by=username)
            
            # Tüm aktif satıcıları al
            sellers = get_active_seller_links()
            total_sellers = len(sellers)
            
            update_seller_scraping_status(total=total_sellers, progress=0)
            
            logging.info(f"Manuel satıcı güncelleme başlatıldı: {total_sellers} satıcı")
            
            success_count = 0
            failed_count = 0
            
            for i, seller in enumerate(sellers):
                seller_id = seller['id']
                all_products_url = seller['all_products_url']
                seller_profile_url = seller['seller_profile_url']
                
                # Durumu güncelle
                update_seller_scraping_status(
                    progress=i + 1,
                    current_item=f"Satıcı {i+1}/{total_sellers}: {seller.get('seller_name', 'Bilinmeyen')}"
                )
                
                # Scraping yap
                success = scrape_single_seller(seller_id, all_products_url, seller_profile_url, username)
                
                if success:
                    success_count += 1
                    update_seller_scraping_status(success_count=success_count)
                else:
                    failed_count += 1
                    error_msg = f"Scraping hatası: {all_products_url}"
                    update_seller_scraping_status(error=error_msg, failed_count=failed_count)
                
                # Bekleme süresi (Selenium için daha uzun)
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            
            logging.info(f"Manuel satıcı güncelleme tamamlandı: {success_count}/{total_sellers} başarılı")
            
        except Exception as e:
            error_msg = f"Manuel satıcı güncelleme hatası: {str(e)}"
            logging.error(error_msg)
            update_seller_scraping_status(error=error_msg)
        finally:
            update_seller_scraping_status(is_running=False, current_item="")
    
    # Eğer başka bir scraping devam ediyorsa başlatma
    if seller_scraping_status['is_running']:
        logging.warning("Satıcı scraping zaten devam ediyor, yeni işlem başlatılmadı")
        return False
    
    # Arka planda çalıştır
    thread = threading.Thread(target=manual_update_worker)
    thread.daemon = True
    thread.start()
    
    return True

def start_scheduled_seller_update(username: str = "seller_scheduler") -> bool:
    """
    Zamanlanmış güncelleme başlatır
    """
    def scheduled_update_worker():
        try:
            update_seller_scraping_status(is_running=True, started_by=username)
            
            # Tüm aktif satıcıları al
            sellers = get_active_seller_links()
            total_sellers = len(sellers)
            
            update_seller_scraping_status(total=total_sellers, progress=0)
            
            logging.info(f"Otomatik satıcı güncelleme başlatıldı: {total_sellers} satıcı")
            
            success_count = 0
            failed_count = 0
            
            for i, seller in enumerate(sellers):
                seller_id = seller['id']
                all_products_url = seller['all_products_url']
                seller_profile_url = seller['seller_profile_url']
                
                # Durumu güncelle
                update_seller_scraping_status(
                    progress=i + 1,
                    current_item=f"Otomatik: Satıcı {i+1}/{total_sellers}"
                )
                
                # Scraping yap
                success = scrape_single_seller(seller_id, all_products_url, seller_profile_url, username)
                
                if success:
                    success_count += 1
                    update_seller_scraping_status(success_count=success_count)
                else:
                    failed_count += 1
                    error_msg = f"Otomatik scraping hatası: {all_products_url}"
                    update_seller_scraping_status(error=error_msg, failed_count=failed_count)
                
                # Otomatik güncellemede daha uzun bekleme (Selenium için)
                time.sleep(random.uniform(REQUEST_DELAY_MIN + 5, REQUEST_DELAY_MAX + 10))
            
            logging.info(f"Otomatik satıcı güncelleme tamamlandı: {success_count}/{total_sellers} başarılı")
            
        except Exception as e:
            error_msg = f"Otomatik satıcı güncelleme hatası: {str(e)}"
            logging.error(error_msg)
            update_seller_scraping_status(error=error_msg)
        finally:
            update_seller_scraping_status(is_running=False, current_item="")
    
    # Eğer başka bir scraping devam ediyorsa başlatma
    if seller_scraping_status['is_running']:
        logging.warning("Satıcı scraping zaten devam ediyor, otomatik işlem atlandı")
        return False
    
    # Arka planda çalıştır
    thread = threading.Thread(target=scheduled_update_worker)
    thread.daemon = True
    thread.start()
    
    return True

def is_seller_scraping_running() -> bool:
    """Scraping işleminin devam edip etmediğini kontrol eder"""
    return seller_scraping_status['is_running']

def get_seller_scraping_statistics() -> Dict:
    """
    Scraping istatistiklerini getirir
    """
    try:
        seller_stats = get_seller_statistics()
        scraping_stats = get_seller_scraping_status()
        
        return {
            'seller_stats': seller_stats,
            'scraping_status': scraping_stats
        }
        
    except Exception as e:
        logging.error(f"Satıcı scraping istatistik hatası: {str(e)}")
        return {}

# Test fonksiyonu
def test_single_seller_scraping_selenium(all_products_url: str, seller_profile_url: str):
    """İki URL için test scraping (Selenium ile)"""
    logging.info(f"Test satıcı scraping başlatılıyor (Selenium):")
    logging.info(f"Tüm ürünler: {all_products_url}")
    logging.info(f"Profil: {seller_profile_url}")
    
    print("🧪 TEST: Selenium ile scraping test ediliyor...")
    
    all_products_data = scrape_all_products_page_selenium(all_products_url)
    print("📊 Tüm ürünler verisi:", all_products_data)
    
    # Test için sayfa arası bekleme
    time.sleep(random.uniform(5, 8))
    
    profile_data = scrape_seller_profile_page_selenium(seller_profile_url)
    print("📊 Profil verisi:", profile_data)
    
    return all_products_data, profile_data

# Chrome driver durumunu kontrol etme fonksiyonu
def check_chrome_driver():
    """Chrome driver'ın çalışıp çalışmadığını kontrol eder"""
    try:
        driver = setup_chrome_driver()
        driver.get("https://www.google.com")
        time.sleep(2)
        title = driver.title
        driver.quit()
        
        print(f"✅ SELLER DEBUG: Chrome driver test başarılı - Title: {title}")
        return True
    except Exception as e:
        print(f"❌ SELLER DEBUG: Chrome driver test başarısız: {str(e)}")
        return False

def scrape_all_sellers(specific_seller_id=None, username="scrape_all_sellers"):
    """
    Tüm aktif satıcıları scraping yapar
    specific_seller_id: Sadece belirli bir satıcı için scraping yapmak için
    """
    try:
        from seller_tracking import get_db_connection
        
        conn = get_db_connection()
        
        if specific_seller_id:
            # Sadece belirli satıcı için
            cursor = conn.execute('''
                SELECT id, all_products_url, seller_profile_url 
                FROM seller_links 
                WHERE id = ? AND is_active = 1
            ''', (specific_seller_id,))
            print(f"🎯 Tek satıcı scraping: ID {specific_seller_id}")
        else:
            # Tüm aktif satıcılar için
            cursor = conn.execute('''
                SELECT id, all_products_url, seller_profile_url 
                FROM seller_links 
                WHERE is_active = 1
            ''')
            print("🔄 Tüm aktif satıcılar için scraping başlatılıyor")
        
        sellers = cursor.fetchall()
        conn.close()
        
        if not sellers:
            print(f"⚠️ Scraping yapılacak satıcı bulunamadı")
            return False
        
        print(f"📊 {len(sellers)} satıcı için scraping başlatılıyor")
        
        success_count = 0
        failed_count = 0
        
        for seller in sellers:
            seller_id, all_products_url, seller_profile_url = seller
            
            try:
                print(f"🔄 Scraping başlatılıyor: Satıcı ID {seller_id}")
                
                # Tek satıcı scraping fonksiyonunu kullan
                success = scrape_single_seller(
                    seller_link_id=seller_id,
                    all_products_url=all_products_url,
                    seller_profile_url=seller_profile_url,
                    scraped_by=username
                )
                
                if success:
                    success_count += 1
                    print(f"✅ Satıcı {seller_id} scraping başarılı")
                else:
                    failed_count += 1
                    print(f"❌ Satıcı {seller_id} scraping başarısız")
                
                # Satıcılar arası bekleme (sadece birden fazla satıcı varsa)
                if len(sellers) > 1:
                    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
                    
            except Exception as e:
                failed_count += 1
                print(f"❌ Satıcı {seller_id} scraping exception: {str(e)}")
        
        print(f"📊 Scraping tamamlandı: {success_count} başarılı, {failed_count} başarısız")
        return success_count > 0
        
    except Exception as e:
        print(f"❌ scrape_all_sellers genel hatası: {str(e)}")
        logging.error(f"scrape_all_sellers hatası: {str(e)}")
        return False

print("✅ Selenium Seller scraper modülü yüklendi - Production/Local uyumlu Chrome WebDriver")