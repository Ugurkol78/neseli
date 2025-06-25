"""
Ürün İzleme ve Analiz Modülü - Web Scraping
Selenium ve BeautifulSoup ile Trendyol ürün verilerini çeker
3 günlük satış verisi için sepet işlemleri yapar
"""

import requests
from bs4 import BeautifulSoup
import time
import random
import logging
import threading
from typing import Dict, Optional, List
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
import re
# Mevcut import'ların altına bu satırı ekleyin:
from webdriver_manager.chrome import ChromeDriverManager

from product_tracking import (
    get_active_product_links, save_product_data, 
    get_product_statistics
)

# Scraping ayarları
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
]

REQUEST_DELAY_MIN = 3  # Minimum bekleme süresi (saniye)
REQUEST_DELAY_MAX = 7  # Maximum bekleme süresi (saniye)
REQUEST_TIMEOUT = 20   # İstek timeout süresi (saniye)

# Global scraping durumu
scraping_status = {
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

def setup_chrome_driver() -> webdriver.Chrome:
    """Chrome WebDriver'ı headless modda kuruluma hazırlar"""
    print(f"🔍 PROD DEBUG: Chrome driver kuruluyor...")
    
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument(f'--user-agent={random.choice(USER_AGENTS)}')
    
    # VPS için ayarlar
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    
    print(f"🔍 PROD DEBUG: Chrome options ayarlandı (headless mode)")
    
    try:
        # Manuel path kullan
        service = Service('/usr/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print(f"✅ PROD DEBUG: Chrome driver başarıyla kuruldu")
        return driver
    except Exception as e:
        print(f"❌ PROD DEBUG: Chrome driver kurulum hatası: {str(e)}")
        logging.error(f"PROD DEBUG: Chrome driver hatası: {str(e)}")
        raise

def scrape_product_basic_info(url: str) -> Optional[Dict[str, any]]:
    """
    BeautifulSoup ile temel ürün bilgilerini çeker (hızlı)
    Returns: {'title': str, 'seller': str, 'price': float, 'rating': float, 'comments': int, 'questions': int, 'image_url': str}
    """
    try:
        headers = get_random_headers()
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'lxml')
        
        # DEBUG: Sayfa içeriğini kontrol et
        print(f"🔍 DEBUG: Sayfa başlığı: {soup.title.string if soup.title else 'Başlık yok'}")
        print(f"🔍 DEBUG: Sayfa uzunluğu: {len(response.text)} karakter")

        result = {
            'title': None,
            'seller': None, 
            'price': None,
            'rating': None,
            'comments': 0,
            'questions': 0,
            'image_url': None,
            'seller_rating': None
        }
        
        # Ürün başlığı
        title_selectors = [
            'h1[data-testid="product-title"]',
            'h1.product-title',
            'h1.pr-new-br',
            'h1'
        ]
        for selector in title_selectors:
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                result['title'] = element.get_text(separator=' ', strip=True)
                break
        
# Fiyat - GÜNCELLENDİ: Yeni campaign price format'ı eklendi
        price_selectors = [
           '.campaign-price-container.default',  # YENİ: Dolu container
           '.campaign-price-box',               # YENİ: Alternatif
           '.campaign-price',  # YENİ: Campaign price (en yüksek öncelik)
           '.prc-dsc', 
           'span.price-view-discounted',                    # "2.789,07 TL" - Ana selector
           '.price-view-price-view span.price-view-discounted',  # Daha spesifik
           '[data-testid="price"] .price-view-discounted',  # Data-testid ile
           '.campaign-price-content .new-price',            # YENİ: Campaign price format
           '.campaign-price-content p.new-price',           # YENİ: Daha spesifik selector
           '[data-testid="price-current-price"]',
           '.prc-slg', 
           '.product-price .prc-dsc',
        ]
        for selector in price_selectors:
            element = soup.select_one(selector)
            if element:
                price_text = element.get_text(strip=True)
                print(f"🔍 DEBUG: Price element text ({selector}): '{price_text}'")
                
                # YENİ GELIŞMIŞ PRICE PARSING
                if price_text:
                    # Birden fazla satır varsa işle
                    lines = [line.strip() for line in price_text.split('\n') if line.strip()]
                    print(f"🔍 DEBUG: Price lines: {lines}")
                    
                    # TL içeren satırları bul ve sadece fiyat formatındakileri al
                    price_lines = []
                    for line in lines:
                        if 'TL' in line and any(char.isdigit() for char in line):
                            # "XXXX TL" formatında mı kontrol et (kampanya açıklaması değil)
                            if re.search(r'^\d+[.,]?\d*\s*TL$', line.strip()) or 'indirim' not in line.lower():
                                price_lines.append(line)
                    
                    print(f"🔍 DEBUG: Valid TL price lines: {price_lines}")
                    
                    if price_lines:
                        # Tüm geçerli fiyatları çıkar
                        valid_prices = []
                        for line in price_lines:
                            # Sadece rakam, nokta, virgül bırak
                            price_clean = re.sub(r'[^\d,.]', '', line)
                            if price_clean:
                                try:
                                    # Virgül ve nokta işleme
                                    if ',' in price_clean:
                                        if price_clean.count(',') == 1:
                                            test_price = float(price_clean.replace(',', '.'))
                                        else:
                                            parts = price_clean.split(',')
                                            if len(parts[-1]) == 2:
                                                integer_part = ''.join(parts[:-1])
                                                decimal_part = parts[-1]
                                                test_price = float(f"{integer_part}.{decimal_part}")
                                            else:
                                                test_price = float(price_clean.replace(',', ''))
                                    else:
                                        test_price = float(price_clean)
                                    
                                    # Makul fiyat aralığında mı?
                                    if 10 <= test_price <= 1000000:
                                        valid_prices.append(test_price)
                                        
                                except ValueError:
                                    continue
                        
                        if valid_prices:
                            # EN KÜÇÜK FİYATI AL (indirimli fiyat)
                            result['price'] = min(valid_prices)
                            print(f"🔍 DEBUG: Multiple prices found, selected minimum: {result['price']} from {valid_prices}")
                            break
                    else:
                        # Tek satır ise eski yöntemi kullan
                        price_clean = re.sub(r'[^\d,]', '', price_text)
                        if price_clean:
                            try:
                                if ',' in price_clean:
                                    if price_clean.count(',') == 1:
                                        result['price'] = float(price_clean.replace(',', '.'))
                                    else:
                                        parts = price_clean.split(',')
                                        if len(parts[-1]) == 2:
                                            integer_part = ''.join(parts[:-1])
                                            decimal_part = parts[-1]
                                            result['price'] = float(f"{integer_part}.{decimal_part}")
                                        else:
                                            result['price'] = float(price_clean.replace(',', ''))
                                else:
                                    result['price'] = float(price_clean)
                                
                                print(f"🔍 DEBUG: Single price found ({selector}): {result['price']}")
                                break
                            except ValueError:
                                print(f"🔍 DEBUG: Price parse hatası ({selector}): {price_clean}")
                                continue
        
        # Satıcı adı
        seller_selectors = [
            '.product-description-market-place',
            'span.product-description-market-place',
            '.merchant-name',
            '[class*="merchant-name"]'
        ]
        for selector in seller_selectors:
            element = soup.select_one(selector)
            if element and element.get_text(strip=True):
                result['seller'] = element.get_text(strip=True)
                break
        
        # Puan
        rating_selectors = [
            '.reviews-summary-average-rating',
            '.rating-line-count',
            '.stars-container .rating',
            '[data-testid="product-rating"]'
        ]
        for selector in rating_selectors:
            element = soup.select_one(selector)
            if element:
                rating_text = element.get_text(strip=True)
                rating_match = re.search(r'(\d+[,.]?\d*)', rating_text)
                if rating_match:
                    try:
                        result['rating'] = float(rating_match.group(1).replace(',', '.'))
                        break
                    except ValueError:
                        continue
        
        # Yorum sayısı  
        comment_selectors = [
            '.reviews-summary-reviews-summary a span:first-child',
            '.rating-line-count',
            '.comments-summary .rating-line-count',
            '[data-testid="reviews-count"]',
            'a[data-testid="questions-summary-link"] span'
        ]
        for selector in comment_selectors:
            element = soup.select_one(selector)
            if element:
                comment_text = element.get_text(strip=True)
                comment_match = re.search(r'(\d+)', comment_text.replace('.', '').replace(',', ''))
                if comment_match:
                    result['comments'] = int(comment_match.group(1))
                    break
        
        # Soru sayısı
        question_selectors = [
            'a[data-testid="questions-summary-link"] span:last-child',
            '.qa-section-header',
            '.questions-answers .header',
            '[data-testid="questions-count"]'
        ]
        for selector in question_selectors:
            element = soup.select_one(selector)
            if element:
                question_text = element.get_text(strip=True)
                question_match = re.search(r'(\d+)', question_text.replace('.', '').replace(',', ''))
                if question_match:
                    result['questions'] = int(question_match.group(1))
                    break
        
        # Ürün resmi
        image_selectors = [
            '.product-images img',
            '.gallery-modal img',
            'img[data-testid="product-image"]'
        ]
        for selector in image_selectors:
            element = soup.select_one(selector)
            if element and element.get('src'):
                result['image_url'] = element.get('src')
                break
        
        # Satıcı puanı (varsa)
        seller_rating_selectors = [
            '.score-badge',
            '._body_03c70b5',
            '.merchant-rating',
            '.seller-score',
            '[data-testid="seller-rating"]'
        ]
        for selector in seller_rating_selectors:
            element = soup.select_one(selector)
            if element:
                seller_rating_text = element.get_text(strip=True)
                seller_rating_match = re.search(r'(\d+[,.]?\d*)', seller_rating_text)
                if seller_rating_match:
                    try:
                        result['seller_rating'] = float(seller_rating_match.group(1).replace(',', '.'))
                        break
                    except ValueError:
                        continue
        
        return result
        
    except requests.exceptions.RequestException as e:
        logging.error(f"Request hatası - {url}: {str(e)}")
        return None
    except Exception as e:
        logging.error(f"Scraping hatası - {url}: {str(e)}")
        return None


def scrape_product_with_selenium(url: str) -> Optional[Dict[str, any]]:
    """
    Selenium ile JavaScript yüklü sayfadan veri çeker
    """
    driver = None
    try:
        driver = setup_chrome_driver()
        
        print(f"🔍 SELENIUM DEBUG: Sayfaya gidiliyor: {url}")
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
            'title': None,
            'seller': None, 
            'price': None,
            'rating': None,
            'comments': 0,
            'questions': 0,
            'image_url': None,
            'seller_rating': None
        }
        
        # Title çek (her zaman çalışıyor)
        try:
            title_selectors = ['h1', '[data-testid="product-title"]', '.product-title']
            for selector in title_selectors:
                try:
                    title_element = driver.find_element(By.CSS_SELECTOR, selector)
                    result['title'] = title_element.text.strip()
                    print(f"🔍 SELENIUM DEBUG: Title bulundu ({selector}): {result['title'][:50]}...")
                    break
                except:
                    continue
        except Exception as e:
            print(f"🔍 SELENIUM DEBUG: Title hatası: {str(e)}")
        
        # Rating çek - Çoklu selector deneme
        rating_selectors = [
            '.reviews-summary-average-rating',
            '.rating-score',
            '.product-rating-score',
            '[data-testid="product-rating"]',
            '.stars-container .rating',
            '.review-rating',
            'span[class*="rating"]',
            'div[class*="rating"]'
        ]
        
        for selector in rating_selectors:
            try:
                rating_element = driver.find_element(By.CSS_SELECTOR, selector)
                rating_text = rating_element.text.strip()
                rating_match = re.search(r'(\d+[,.]?\d*)', rating_text)
                if rating_match:
                    result['rating'] = float(rating_match.group(1).replace(',', '.'))
                    print(f"🔍 SELENIUM DEBUG: Rating bulundu ({selector}): {result['rating']}")
                    break
            except:
                continue
        
        if not result['rating']:
            print("🔍 SELENIUM DEBUG: Rating bulunamadı - tüm selector'lar denendi")
        
        # Comment count çek - Çoklu selector deneme
        comment_selectors = [
            '.reviews-summary-reviews-summary a span:first-child',
            '.review-count',
            '.comments-count',
            '[data-testid="reviews-count"]',
            '.rating-line-count',
            'a[href*="yorum"] span',
            'span[class*="review"]',
            'div[class*="review"] span'
        ]
        
        for selector in comment_selectors:
            try:
                comment_element = driver.find_element(By.CSS_SELECTOR, selector)
                comment_text = comment_element.text.strip()
                print(f"🔍 SELENIUM DEBUG: Comment element text ({selector}): '{comment_text}'")
                
                # Sayı çıkarma
                comment_match = re.search(r'(\d+)', comment_text.replace('.', '').replace(',', ''))
                if comment_match:
                    result['comments'] = int(comment_match.group(1))
                    print(f"🔍 SELENIUM DEBUG: Comment count bulundu ({selector}): {result['comments']}")
                    break
            except:
                continue
        
        if not result['comments']:
            print("🔍 SELENIUM DEBUG: Comment count bulunamadı - tüm selector'lar denendi")
        
        # Question count çek
        question_selectors = [
            'a[data-testid="questions-summary-link"] span',
            '.questions-summary-questions-summary span',
            'a[class*="questions-summary"] span',
            'a[href*="saticiya-sor"] span',
            'a[data-testid="questions-summary-link"] span:last-child',
            '.questions-summary-questions-summary span b',
            'a[data-testid="questions-summary-link"] b',
            'a[data-testid="questions-summary-link"] span:last-child',
            '.questions-count',
            '.qa-count',
            '[data-testid="questions-count"]',
            'a[href*="soru"] span'
        ]

        for selector in question_selectors:
            try:
                question_element = driver.find_element(By.CSS_SELECTOR, selector)
                question_text = question_element.text.strip()
                print(f"🔍 SELENIUM DEBUG: Question element text ({selector}): '{question_text}'")
                
                # Sayı çıkarma - "202 Soru-Cevap" formatından
                question_match = re.search(r'(\d+)', question_text.replace('.', '').replace(',', ''))
                if question_match:
                    result['questions'] = int(question_match.group(1))
                    print(f"🔍 SELENIUM DEBUG: Question count bulundu ({selector}): {result['questions']}")
                    break
            except:
                continue
        
        # Price çek - FULL DEBUG MODE
        price_selectors = [
            '.campaign-price-container.default',  # YENİ: Dolu container
            '.campaign-price-box',               # YENİ: Alternatif
            '.campaign-price',  # YENİ: Campaign price (en yüksek öncelik)
            '.prc-dsc',                                      # ✅ İNDİRİMLİ FİYAT
            'span.price-view-discounted',                    
            '.price-view-price-view span.price-view-discounted',  
            '[data-testid="price"] .price-view-discounted',  
            '.campaign-price-content .new-price',            
            '.campaign-price-content p.new-price',           
            'div.campaign-price-content p.new-price',        
            '[data-testid="price-current-price"]',
            '.price-current',
            '.prc-slg', 
            '.product-price .prc-dsc',
            '.prc-cntr .prc-dsc',
            '.price-container span',
            '.product-price span:last-child',
            'span[data-testid*="price"]',
        ]

        print(f"🔍 SELENIUM DEBUG: Price çekme başlıyor...")

        # ÖNCE SAYFADA HANGİ PRICE ELEMENT'LERİ VAR BAKALIM
        try:
            print(f"🔍 SELENIUM DEBUG: Sayfadaki tüm price-related elementler:")
            
            # Genel price element'lerini bul
            all_price_elements = driver.find_elements(By.CSS_SELECTOR, '*[class*="price"], *[class*="prc"], *[data-testid*="price"]')
            for i, elem in enumerate(all_price_elements[:10]):  # İlk 10 tanesi
                try:
                    elem_class = elem.get_attribute('class')
                    elem_text = elem.text.strip()
                    elem_tag = elem.tag_name
                    print(f"🔍 SELENIUM DEBUG: Element {i+1}: <{elem_tag} class='{elem_class}'>{elem_text}</tag>")
                except:
                    pass
                    
            print(f"🔍 SELENIUM DEBUG: Toplam price-related element sayısı: {len(all_price_elements)}")
            
        except Exception as e:
            print(f"🔍 SELENIUM DEBUG: Price element listesi hatası: {str(e)}")

        # ŞİMDİ SELECTOR'LARI TEK TEK DENE
        for selector in price_selectors:
            try:
                price_element = driver.find_element(By.CSS_SELECTOR, selector)
                price_text = price_element.text.strip()
                print(f"🔍 SELENIUM DEBUG: Price element BULUNDU ({selector}): '{price_text}'")
                
                # YENİ ADVANCED PRICE PARSING
                if price_text:
                    # Birden fazla satır varsa en son satırdaki fiyatı al
                    lines = [line.strip() for line in price_text.split('\n') if line.strip()]
                    print(f"🔍 SELENIUM DEBUG: Price lines: {lines}")
                    
                    # TL içeren satırları bul ve sadece fiyat formatındakileri al
                    price_lines = []
                    for line in lines:
                        if 'TL' in line and any(char.isdigit() for char in line):
                            # "XXXX TL" formatında mı kontrol et (kampanya açıklaması değil)
                            if re.search(r'^\d+[.,]?\d*\s*TL$', line.strip()) or 'indirim' not in line.lower():
                                price_lines.append(line)
                    
                    print(f"🔍 SELENIUM DEBUG: Valid TL price lines: {price_lines}")
                    
                    if price_lines:
                        # Tüm geçerli fiyatları çıkar
                        valid_prices = []
                        for line in price_lines:
                            # Sadı rakam, nokta, virgül ve TL'yi koru
                            clean_text = re.sub(r'[^\d.,TL\s]', '', line)
                            print(f"🔍 SELENIUM DEBUG: After regex clean: '{clean_text}'")
                            
                            # TL'yi kaldır ve sadece sayıları al
                            clean_text = clean_text.replace('TL', '').strip()
                            
                            # Sayıları bul
                            numbers = re.findall(r'\d+[.,]?\d*', clean_text)
                            print(f"🔍 SELENIUM DEBUG: Found numbers in line: {numbers}")
                            
                            for num_str in numbers:
                                try:
                                    # Virgülü noktaya çevir
                                    num_str = num_str.replace(',', '.')
                                    price = float(num_str)
                                    # Makul fiyat aralığında mı?
                                    if 10 <= price <= 1000000:
                                        valid_prices.append(price)
                                except ValueError:
                                    continue
                        
                        if valid_prices:
                            # EN KÜÇÜK FİYATI AL (indirimli fiyat)
                            result['price'] = min(valid_prices)
                            print(f"🔍 SELENIUM DEBUG: Multiple prices found, selected minimum: {result['price']} from {valid_prices}")
                            break
                    else:
                        print(f"🔍 SELENIUM DEBUG: Valid TL price lines bulunamadı")
                else:
                    print(f"🔍 SELENIUM DEBUG: Price element boş ({selector})")
                    
            except Exception as e:
                print(f"🔍 SELENIUM DEBUG: Price selector hatası ({selector}): {str(e)}")
                continue
        
        # Seller name çek
        seller_selectors = [
            '.product-description-market-place',
            'span.product-description-market-place',
            '.merchant-name',
            '[class*="merchant-name"]',
            '.seller-name'
        ]
        
        for selector in seller_selectors:
            try:
                seller_element = driver.find_element(By.CSS_SELECTOR, selector)
                result['seller'] = seller_element.text.strip()
                print(f"🔍 SELENIUM DEBUG: Seller bulundu ({selector}): {result['seller']}")
                break
            except:
                continue
        
        # Seller rating çek
        seller_rating_selectors = [
            '.score-badge',
            '._body_03c70b5',
            'div[class*="_body_03c70b5"]',
            'div.score-badge',
            '[class*="score-badge"]',
            'div[style*="background-color: rgb(4, 155, 36)"]',
            'div[style*="background-color: rgb"]',
            '.merchant-rating',
            '.seller-score',
            '[data-testid="seller-rating"]',
            'span[class*="rating"]',
            'div[class*="rating"]'
        ]

        for selector in seller_rating_selectors:
            try:
                seller_rating_element = driver.find_element(By.CSS_SELECTOR, selector)
                seller_rating_text = seller_rating_element.text.strip()
                print(f"🔍 SELENIUM DEBUG: Seller rating element text ({selector}): '{seller_rating_text}'")
                
                # Sayı çıkarma - "9.4" formatından
                seller_rating_match = re.search(r'(\d+[,.]?\d*)', seller_rating_text)
                if seller_rating_match:
                    try:
                        result['seller_rating'] = float(seller_rating_match.group(1).replace(',', '.'))
                        print(f"🔍 SELENIUM DEBUG: Seller rating bulundu ({selector}): {result['seller_rating']}")
                        break
                    except ValueError:
                        continue
            except Exception as e:
                print(f"🔍 SELENIUM DEBUG: Seller rating hatası ({selector}): {str(e)}")
                continue

        if not result['seller_rating']:
            print("🔍 SELENIUM DEBUG: Seller rating bulunamadı - tüm selector'lar denendi")
        
        # Image URL çek
        image_selectors = [
            '.product-images img',
            '.gallery-modal img',
            'img[data-testid="product-image"]',
            '.product-image img'
        ]

        print(f"🔍 PROD DEBUG: Image selector araması başlıyor...")
        logging.info(f"PROD DEBUG: Image URL çekme başlıyor - URL: {url}")

        for i, selector in enumerate(image_selectors):
            try:
                print(f"🔍 PROD DEBUG: Image selector {i+1}/{len(image_selectors)} deneniyor: {selector}")
                image_element = driver.find_element(By.CSS_SELECTOR, selector)
                image_url = image_element.get_attribute('src')
                print(f"🔍 PROD DEBUG: Element bulundu! Src attribute: {image_url}")
                
                if image_url:
                    result['image_url'] = image_url
                    print(f"✅ PROD DEBUG: Image URL başarıyla çekildi ({selector}): {image_url[:100]}...")
                    logging.info(f"PROD DEBUG: Image URL başarıyla elde edildi: {image_url}")
                    break
                else:
                    print(f"⚠️ PROD DEBUG: Element bulundu ama src attribute boş ({selector})")
            except Exception as selector_error:
                print(f"❌ PROD DEBUG: Image selector hatası ({selector}): {str(selector_error)}")
                continue

        if not result.get('image_url'):
            print(f"❌ PROD DEBUG: Hiçbir image selector çalışmadı! Tüm selector'lar denendi.")
            logging.warning(f"PROD DEBUG: Image URL bulunamadı - URL: {url}")
        
        # Sales data çek (sepet işlemi)
        if not result.get('sales_3day'):
            try:
                print(f"🔍 SELENIUM DEBUG: Sales data çekiliyor...")
                sales_data = scrape_cart_sales_data(url)
                if sales_data:
                    result['sales_3day'] = sales_data
                    result['daily_estimated_sales'] = sales_data / 3.0
                    print(f"🔍 SELENIUM DEBUG: Sales data eklendi: {sales_data}")
                else:
                    print(f"🔍 SELENIUM DEBUG: Sales data bulunamadı")
            except Exception as e:
                print(f"🔍 SELENIUM DEBUG: Sales data hatası: {str(e)}")
        
        print(f"🔍 SELENIUM DEBUG: Final result: {result}")
        return result
        
    except Exception as e:
        print(f"🔍 SELENIUM DEBUG: Genel hata: {str(e)}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def scrape_cart_sales_data(url: str, max_retries: int = 2) -> Optional[int]:
    """
    Selenium ile sepete ekleme işlemi yaparak 3 günlük satış verisini çeker
    Returns: sales_3day (int) or None
    """
    driver = None
    try:
        driver = setup_chrome_driver()
        
        for attempt in range(max_retries):
            try:
                logging.info(f"Sepet verisi çekiliyor (deneme {attempt + 1}): {url}")
                
                # Önce sepeti temizle
                try:
                    print(f"🔍 SELENIUM DEBUG: Sepet temizleniyor...")
                    cart_url = "https://www.trendyol.com/sepet"
                    driver.get(cart_url)
                    time.sleep(2)
                    
                    # Sepetteki ürünleri sil
                    delete_buttons = driver.find_elements(By.CSS_SELECTOR, '.remove-item, .delete-item, [class*="remove"], [class*="delete"]')
                    for btn in delete_buttons:
                        try:
                            driver.execute_script("arguments[0].click();", btn)
                            time.sleep(0.5)
                        except:
                            continue
                    
                    print(f"🔍 SELENIUM DEBUG: Sepet temizlendi")
                except Exception as e:
                    print(f"🔍 SELENIUM DEBUG: Sepet temizleme hatası: {str(e)}")
                
                # Ürün sayfasına git
                driver.get(url)
                
                # Sayfa yüklenene kadar bekle
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                
                time.sleep(random.uniform(2, 4))

                # Sayfayı scroll et (sepete ekle butonu görünür olsun)
                driver.execute_script("window.scrollTo(0, 800);")
                time.sleep(2)
                driver.execute_script("window.scrollTo(0, 400);")
                time.sleep(1)

                print(f"🔍 SELENIUM DEBUG: Sayfa scroll edildi, buton aranıyor...")

                # Sepete ekle butonunu bul ve tıkla
                cart_button = None
                
                # Ana buton selector'ı
                main_selector = 'button[data-testid="add-to-cart-button"]'
                
                try:
                    # Butonun var olmasını bekle
                    print(f"🔍 SELENIUM DEBUG: Sepete ekle butonu aranıyor...")
                    cart_button = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, main_selector))
                    )
                    print(f"🔍 SELENIUM DEBUG: Buton bulundu: {main_selector}")
                    
                    # Loading'in bitmesini bekle
                    try:
                        WebDriverWait(driver, 5).until_not(
                            EC.presence_of_element_located((By.CSS_SELECTOR, 'button[data-testid="add-to-cart-button"] [data-testid="loading"]'))
                        )
                        print(f"🔍 SELENIUM DEBUG: Loading bitti")
                    except:
                        print(f"🔍 SELENIUM DEBUG: Loading timeout - devam ediliyor")
                    
                    # Butonun durumunu kontrol et
                    time.sleep(1)
                    button_text = cart_button.text.strip()
                    print(f"🔍 SELENIUM DEBUG: Buton text: '{button_text}'")
                    
                    # Eğer "Sepete Eklendi" ise yenile ve bekle
                    if "Eklendi" in button_text:
                        print(f"🔍 SELENIUM DEBUG: Buton eklendi durumunda - sayfa yenileniyor...")
                        driver.refresh()
                        time.sleep(3)
                        
                        # Yeniden buton ara
                        cart_button = WebDriverWait(driver, 10).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, main_selector))
                        )
                        
                        # Loading bekle
                        try:
                            WebDriverWait(driver, 5).until_not(
                                EC.presence_of_element_located((By.CSS_SELECTOR, 'button[data-testid="add-to-cart-button"] [data-testid="loading"]'))
                            )
                        except:
                            pass
                        
                        time.sleep(1)
                        button_text = cart_button.text.strip()
                        print(f"🔍 SELENIUM DEBUG: Yenileme sonrası buton text: '{button_text}'")
                    
                    # Buton durumunu final kontrol
                    if cart_button and cart_button.is_enabled() and "Eklendi" not in button_text:
                        print(f"🔍 SELENIUM DEBUG: Sepete ekle butonu hazır!")
                    else:
                        print(f"🔍 SELENIUM DEBUG: Buton hala kullanılamaz durumda")
                        cart_button = None
                        
                except Exception as e:
                    print(f"🔍 SELENIUM DEBUG: Buton bulma hatası: {str(e)}")
                    cart_button = None

                # Buton bulunamazsa döngüyü devam ettir
                if not cart_button:
                    logging.warning(f"Sepete ekle butonu bulunamadı veya zaten eklendi: {url}")
                    continue
                
                # Sepete ekle
                driver.execute_script("arguments[0].click();", cart_button)
                print(f"🔍 SELENIUM DEBUG: Sepete ekleme butonu tıklandı")
                time.sleep(random.uniform(2, 3))
                
                # Sepet sayfasına git
                cart_url = "https://www.trendyol.com/sepet"
                driver.get(cart_url)
                print(f"🔍 SELENIUM DEBUG: Sepet sayfasına gidildi")
                
                # Sepet yüklenene kadar bekle - satış verisi odaklı
                cart_loaded = False
                cart_wait_selectors = [
                    ".order-count-text",  # Direkt satış verisi
                    ".social-proof-label",  # Satış container'ı
                    "[class*='social-proof']",
                    ".cart-item",
                    ".basket-item", 
                    "[class*='cart']",
                    "body"  # fallback
                ]
                
                for selector in cart_wait_selectors:
                    try:
                        element = WebDriverWait(driver, 3).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        print(f"🔍 SELENIUM DEBUG: Sepet yüklendi ({selector}): {element.text[:50] if element.text else 'boş'}")
                        cart_loaded = True
                        break
                    except:
                        print(f"🔍 SELENIUM DEBUG: Sepet selector bulunamadı: {selector}")
                        continue
                
                if not cart_loaded:
                    print(f"🔍 SELENIUM DEBUG: Sepet yüklenemedi, devam ediliyor...")
                
                time.sleep(2)  # Extra wait
                
                time.sleep(random.uniform(1, 2))
                
                # 3 günlük satış verisini ara
                sales_selectors = [
                    '.order-count-text',  # YENİ: "24 tanesi satıldı" için
                    'p[class*="order-count"]',  # YENİ
                    '.social-proof-label p',  # YENİ
                    'div[class*="social-proof"] p',  # YENİ
                    '*[class*="order-count-text"]',  # YENİ
                    '.sales-count',
                    '.last-sold',
                    '[data-testid="sales-info"]',
                    '*[class*="sold"]',
                    '*[class*="sales"]'
                ]
                
                sales_3day = None
                page_source = driver.page_source
                
                # Sayfada satış pattern'lerini ara
                sales_patterns = [
                    r'(\d+)\s*tanesi\s*satıldı',  # YENİ: "24 tanesi satıldı"
                    r'(\d+)\s*adet\s*satıldı',   # YENİ: "24 adet satıldı"
                    r'(\d+)\s*adet.*?3.*?gün',
                    r'3.*?gün.*?(\d+)\s*adet',
                    r'Son.*?3.*?gün.*?(\d+)',
                    r'(\d+).*?satıldı.*?3.*?gün'
                ]
                
                print(f"🔍 SELENIUM DEBUG: Sepet sayfasında satış verisi aranıyor...")
                
                # Debug: Sayfa source'unu kontrol et
                page_source = driver.page_source
                if "tanesi satıldı" in page_source:
                    print(f"🔍 SELENIUM DEBUG: 'tanesi satıldı' metni sayfada var!")
                elif "adet satıldı" in page_source:
                    print(f"🔍 SELENIUM DEBUG: 'adet satıldı' metni sayfada var!")
                elif "satıldı" in page_source:
                    print(f"🔍 SELENIUM DEBUG: 'satıldı' metni sayfada var!")
                else:
                    print(f"🔍 SELENIUM DEBUG: Satış metni bulunamadı, sayfa içeriği:")
                    print(f"🔍 SELENIUM DEBUG: İlk 1000 karakter: {page_source[:1000]}")
                
                for pattern in sales_patterns:
                    match = re.search(pattern, page_source, re.IGNORECASE | re.DOTALL)
                    if match:
                        try:
                            sales_3day = int(match.group(1))
                            print(f"🔍 SELENIUM DEBUG: Pattern ile satış bulundu ({pattern}): {sales_3day}")
                            break
                        except (ValueError, IndexError):
                            continue
                
                # Eğer pattern match yapmadıysa, DOM elementlerini kontrol et
                if sales_3day is None:
                    print(f"🔍 SELENIUM DEBUG: Pattern bulunamadı, DOM elementleri kontrol ediliyor...")
                    for selector in sales_selectors:
                        try:
                            elements = driver.find_elements(By.CSS_SELECTOR, selector)
                            for element in elements:
                                text = element.text.strip()
                                print(f"🔍 SELENIUM DEBUG: Element text ({selector}): '{text}'")
                                
                                # "tanesi satıldı" veya "adet satıldı" ara
                                if 'tanesi satıldı' in text or 'adet satıldı' in text:
                                    numbers = re.findall(r'\d+', text)
                                    if numbers:
                                        sales_3day = int(numbers[0])
                                        print(f"🔍 SELENIUM DEBUG: DOM'dan satış bulundu ({selector}): {sales_3day}")
                                        break
                                # Eski format: "3 gün" ara
                                elif '3' in text and 'gün' in text:
                                    numbers = re.findall(r'\d+', text)
                                    if numbers:
                                        sales_3day = int(numbers[0])
                                        print(f"🔍 SELENIUM DEBUG: DOM'dan 3 günlük satış bulundu ({selector}): {sales_3day}")
                                        break
                            if sales_3day:
                                break
                        except Exception as e:
                            print(f"🔍 SELENIUM DEBUG: DOM element hatası ({selector}): {str(e)}")
                            continue
                
                if sales_3day is not None:
                    print(f"🔍 SELENIUM DEBUG: Başarılı! Satış verisi: {sales_3day}")
                    return sales_3day
                else:
                    print(f"🔍 SELENIUM DEBUG: Satış verisi bulunamadı")
                    logging.warning(f"3 günlük satış verisi bulunamadı: {url}")
                    
            except TimeoutException:
                logging.warning(f"Timeout - sepet verisi alınamadı (deneme {attempt + 1}): {url}")
                continue
            except Exception as e:
                logging.error(f"Sepet verisi hatası (deneme {attempt + 1}) - {url}: {str(e)}")
                continue
        
        return None
        
    except Exception as e:
        logging.error(f"Selenium sepet verisi hatası - {url}: {str(e)}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

def scrape_single_product(product_link_id: int, product_url: str, scraped_by: str) -> bool:
    """
    Tek bir ürün için complete scraping yapar
    """
    try:
        print(f"🔍 DEBUG: Ürün scraping başlatılıyor: {product_url}")
        
        # Selenium ile veri çek
        basic_info = scrape_product_with_selenium(product_url)
        if not basic_info:
            print(f"🔍 DEBUG: Selenium veri çekme başarısız: {product_url}")
            return False
        
        print(f"🔍 DEBUG: Kaydedilecek veri: {basic_info}")
        
        # Veri kaydet - SALES ALANLARI EKLENDİ
        print(f"🔍 PROD DEBUG: save_product_data çağrılıyor...")
        print(f"🔍 PROD DEBUG: product_link_id: {product_link_id}")
        print(f"🔍 PROD DEBUG: product_image_url: {basic_info.get('image_url', 'None')}")
        logging.info(f"PROD DEBUG: Veri kaydediliyor - Link ID: {product_link_id}, Image URL var mı: {bool(basic_info.get('image_url'))}")


        success = save_product_data(
            product_link_id=product_link_id,
            seller_name=basic_info.get('seller', 'Bilinmiyor'),
            product_title=basic_info.get('title', ''),
            price=basic_info.get('price', 0.0),
            comment_count=basic_info.get('comments', 0),
            question_count=basic_info.get('questions', 0), 
            rating=basic_info.get('rating', 0.0),
            sales_3day=basic_info.get('sales_3day'),  # ✅ Bu yeterli
            seller_rating=basic_info.get('seller_rating', 0.0),
            scraped_by=scraped_by,
            product_image_url=basic_info.get('image_url')
            # daily_estimated_sales KALDIRDIK - zaten fonksiyonda hesaplanıyor!
        )
        
        # Debug çıktıları SONRA ekle
        if success:
            print(f"✅ PROD DEBUG: save_product_data başarılı - Image URL kaydedildi mi: {bool(basic_info.get('image_url'))}")
            logging.info(f"PROD DEBUG: Veri kaydetme başarılı - Link ID: {product_link_id}")
            print(f"🔍 DEBUG: Ürün scraping başarılı: {product_url}")
            return True
        else:
            print(f"❌ PROD DEBUG: save_product_data başarısız!")
            logging.error(f"PROD DEBUG: Veri kaydetme başarısız - Link ID: {product_link_id}")
            print(f"🔍 DEBUG: Veri kaydetme hatası: {product_url}")
            return False
            
    except Exception as e:
        print(f"🔍 DEBUG: Ürün scraping exception: {product_url} - {str(e)}")
        return False

def update_scraping_status(is_running: bool = None, progress: int = None, 
                          total: int = None, current_item: str = None,
                          started_by: str = None, error: str = None,
                          success_count: int = None, failed_count: int = None):
    """Scraping durumunu günceller"""
    global scraping_status
    
    with scraping_lock:
        if is_running is not None:
            scraping_status['is_running'] = is_running
            if is_running:
                scraping_status['start_time'] = time.time()
                scraping_status['errors'] = []
                scraping_status['success_count'] = 0
                scraping_status['failed_count'] = 0
            
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
            
        if success_count is not None:
            scraping_status['success_count'] = success_count
            
        if failed_count is not None:
            scraping_status['failed_count'] = failed_count

def get_scraping_status() -> Dict:
    """Mevcut scraping durumunu döndürür"""
    with scraping_lock:
        status = scraping_status.copy()
        if status['start_time']:
            status['elapsed_time'] = time.time() - status['start_time']
        else:
            status['elapsed_time'] = 0
        return status

def start_single_product_scraping(product_link_id: int, product_url: str, scraped_by: str):
    """
    Tek ürün için arka planda scraping başlatır (yeni ürün eklendiğinde)
    """
    def scrape_worker():
        try:
            success = scrape_single_product(product_link_id, product_url, scraped_by)
            if success:
                logging.info(f"Tek ürün scraping tamamlandı: {product_url}")
            else:
                logging.error(f"Tek ürün scraping başarısız: {product_url}")
        except Exception as e:
            logging.error(f"Tek ürün scraping worker hatası: {str(e)}")
    
    # Arka planda çalıştır
    thread = threading.Thread(target=scrape_worker)
    thread.daemon = True
    thread.start()

def start_manual_update(username: str) -> bool:
    """
    Manuel güncelleme başlatır (Admin yetkisi gerekli)
    Tüm aktif ürünler için scraping yapar
    """
    def manual_update_worker():
        try:
            update_scraping_status(is_running=True, started_by=username)
            
            # Tüm aktif ürünleri al
            products = get_active_product_links()
            total_products = len(products)
            
            update_scraping_status(total=total_products, progress=0)
            
            logging.info(f"Manuel güncelleme başlatıldı: {total_products} ürün")
            
            success_count = 0
            failed_count = 0
            
            for i, product in enumerate(products):
                product_id = product['id']
                product_url = product['product_url']
                
                # Durumu güncelle
                update_scraping_status(
                    progress=i + 1,
                    current_item=f"Ürün {i+1}/{total_products}: {product_url[:50]}..."
                )
                
                # Scraping yap
                success = scrape_single_product(product_id, product_url, username)
                
                if success:
                    success_count += 1
                    update_scraping_status(success_count=success_count)
                else:
                    failed_count += 1
                    error_msg = f"Scraping hatası: {product_url}"
                    update_scraping_status(error=error_msg, failed_count=failed_count)
                
                # Bekleme süresi
                time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            
            logging.info(f"Manuel güncelleme tamamlandı: {success_count}/{total_products} başarılı")
            
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

def start_scheduled_update(username: str = "scheduler") -> bool:
    """
    Zamanlanmış güncelleme başlatır
    """
    def scheduled_update_worker():
        try:
            update_scraping_status(is_running=True, started_by=username)
            
            # Tüm aktif ürünleri al
            products = get_active_product_links()
            total_products = len(products)
            
            update_scraping_status(total=total_products, progress=0)
            
            logging.info(f"Otomatik güncelleme başlatıldı: {total_products} ürün")
            
            success_count = 0
            failed_count = 0
            
            for i, product in enumerate(products):
                product_id = product['id']
                product_url = product['product_url']
                
                # Durumu güncelle
                update_scraping_status(
                    progress=i + 1,
                    current_item=f"Otomatik: Ürün {i+1}/{total_products}"
                )
                
                # Scraping yap
                success = scrape_single_product(product_id, product_url, username)
                
                if success:
                    success_count += 1
                    update_scraping_status(success_count=success_count)
                else:
                    failed_count += 1
                    error_msg = f"Otomatik scraping hatası: {product_url}"
                    update_scraping_status(error=error_msg, failed_count=failed_count)
                
                # Otomatik güncellemede daha uzun bekleme
                time.sleep(random.uniform(REQUEST_DELAY_MIN + 2, REQUEST_DELAY_MAX + 3))
            
            logging.info(f"Otomatik güncelleme tamamlandı: {success_count}/{total_products} başarılı")
            
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

def get_scraping_statistics() -> Dict:
    """
    Scraping istatistiklerini getirir
    """
    try:
        product_stats = get_product_statistics()
        scraping_stats = get_scraping_status()
        
        return {
            'product_stats': product_stats,
            'scraping_status': scraping_stats
        }
        
    except Exception as e:
        logging.error(f"Scraping istatistik hatası: {str(e)}")
        return {}

# Test fonksiyonu
def test_single_scraping(url: str):
    """Tek bir URL için test scraping"""
    logging.info(f"Test scraping başlatılıyor: {url}")
    
    basic_info = scrape_product_basic_info(url)
    print("Temel bilgiler:", basic_info)
    
    sales_data = scrape_cart_sales_data(url)
    print("3 günlük satış:", sales_data)
    
    return basic_info, sales_data