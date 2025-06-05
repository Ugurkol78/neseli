"""
Kar Takip ve Maliyet Yönetimi Modülü
Trendyol ürünleri için maliyet hesaplama ve kar analizi
"""

import json
import os
from datetime import datetime
import logging

# Dosya yolları
COSTS_FILE = 'costs.json'

def load_costs():
    """Maliyet verilerini yükle"""
    if os.path.exists(COSTS_FILE):
        try:
            with open(COSTS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Maliyet verisi okuma hatası: {e}")
            return {}
    return {}

def save_costs(costs_data):
    """Maliyet verilerini kaydet"""
    try:
        costs_data['last_updated'] = datetime.now().isoformat()
        with open(COSTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(costs_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logging.error(f"Maliyet verisi kayıt hatası: {e}")
        return False

def get_product_cost_data(barcode):
    """Belirli bir ürünün maliyet verilerini getir"""
    costs = load_costs()
    return costs.get(barcode, get_default_cost_structure())

def get_default_cost_structure():
    """Varsayılan maliyet yapısı"""
    return {
        'production_costs': [],  # Üretim giderleri listesi
        'cargo_cost': '',       # Kargo gideri (boş)
        'commission_rate': '',  # Komisyon oranı (boş)
        'withholding_rate': '',  # Stopaj oranı (boş)
        'other_expenses_rate': '',  # Diğer giderler oranı (boş)
        'platform_fee': '',     # Platform bedeli (boş)
        'sale_price': '',       # Satış fiyatı (boş)
        'last_updated': datetime.now().isoformat()
    }

def calculate_vat(amount, vat_rate=20):
    """KDV hesaplama"""
    return amount * (vat_rate / 100)

def calculate_vat_exclusive(amount_including_vat, vat_rate=20):
    """KDV dahil tutardan KDV hariç tutarı hesapla"""
    return amount_including_vat / (1 + vat_rate / 100)

def calculate_profit_analysis(barcode, sale_price, cost_data):
    """Kar analizi hesaplama"""
    try:

        # Güvenli float dönüştürme fonksiyonu
        def safe_float(value, default=0):
            if value == '' or value is None:
                return default
            try:
                return float(value)
            except (ValueError, TypeError):
                return default

        # Üretim giderleri toplamı (KDV dahil)
        production_total = 0.0
        production_vat_total = 0.0
        
        for cost_item in cost_data.get('production_costs', []):
            if cost_item.get('amount', 0) > 0:
                amount = safe_float(cost_item['amount'])
                vat = calculate_vat(amount)
                production_total += amount + vat
                production_vat_total += vat
        
        # Kargo gideri hesaplama (düzeltildi - işlem önceliği)
        cargo_amount = safe_float(cost_data.get('cargo_cost', 0))  # Kargo bedeli (KDV dahil)
        cargo_vat = cargo_amount - (cargo_amount / 1.2)  # Kargo KDV'si
        cargo_exclusive = cargo_amount - cargo_vat  # KDV'siz kargo (sadece bilgi amaçlı)
        cargo_total = cargo_amount  # Toplam gidere KDV dahil tutar dahil edilir
        
        # Komisyon hesaplama (düzeltildi)
        commission_rate = safe_float(cost_data.get('commission_rate', 0)) / 100
        commission_amount = sale_price * commission_rate  # Komisyon tutarı (toplam gidere dahil)
        commission_vat = commission_amount - (commission_amount / 1.2)  # Komisyon KDV'si
        commission_total = commission_amount + commission_vat  # Toplam komisyon
        
        # Stopaj hesaplama
        withholding_rate = safe_float(cost_data.get('withholding_rate', 0)) / 100
        withholding_amount = sale_price * withholding_rate
        
        # Diğer giderler hesaplama
        other_rate = safe_float(cost_data.get('other_expenses_rate', 0)) / 100
        other_expenses = sale_price * other_rate
        
        # Platform bedeli
        platform_fee = safe_float(cost_data.get('platform_fee', 6.6))
        
        # Hesaplanan KDV (satış fiyatının %20'si)
        calculated_vat = calculate_vat(sale_price)
        
        # Toplam giderler (komisyon KDV hariç)
        total_expenses = (production_total + cargo_amount + commission_amount + 
                         withholding_amount + other_expenses + platform_fee)
        
        # Net KDV yükümlülüğü
        net_vat = calculated_vat - production_vat_total - cargo_vat - commission_vat
        
        # Kar hesaplama
        profit_amount = sale_price - total_expenses - net_vat
        profit_rate = (profit_amount / sale_price * 100) if sale_price > 0 else 0
        
        return {
            'production_total': production_total,
            'production_vat_total': production_vat_total,
            'cargo_total': cargo_total,
            'cargo_vat': cargo_vat,
            'commission_amount': commission_amount,  # KDV hariç komisyon
            'commission_vat': commission_vat,        # Komisyon KDV'si
            'commission_total': commission_total,    # KDV dahil komisyon
            'withholding_amount': withholding_amount,
            'other_expenses': other_expenses,
            'platform_fee': platform_fee,
            'calculated_vat': calculated_vat,
            'net_vat': net_vat,
            'total_expenses': total_expenses,
            'profit_amount': profit_amount,
            'profit_rate': profit_rate
        }
        
    except Exception as e:
        logging.error(f"Kar analizi hesaplama hatası: {e}")
        return None

def save_product_cost_data(barcode, cost_data):
    """Ürün maliyet verilerini kaydet"""
    costs = load_costs()
    costs[barcode] = cost_data
    costs[barcode]['last_updated'] = datetime.now().isoformat()
    return save_costs(costs)

def get_all_products_with_costs(products_list):
    """Tüm ürünlerin maliyet analiziyle birlikte listesini getir"""
    costs = load_costs()
    result = []
    
    for product in products_list:
        barcode = product.get('barcode', '')
        cost_data = costs.get(barcode, get_default_cost_structure())
        sale_price = float(product.get('ty_price', 0))
        
        # Kar analizi hesapla
        analysis = calculate_profit_analysis(barcode, sale_price, cost_data)
        
        # Ürün verisini genişlet
        product_with_costs = product.copy()
        product_with_costs['cost_data'] = cost_data
        product_with_costs['profit_analysis'] = analysis
        
        result.append(product_with_costs)
    
    return result