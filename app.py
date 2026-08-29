
# -*- coding: utf-8 -*-
"""
Kuwait Stock Smart Scanner v8.0 Full Market Forecast
برنامج تحليلي لبورصة الكويت:
- يجلب قائمة الشركات من تقرير الأسهم الحرة الرسمي لبورصة الكويت.
- يجلب الإفصاحات الرسمية من RSS بورصة الكويت (عربي + إنجليزي).
- يجلب الأسعار اليومية من Yahoo Finance عبر yfinance (قد تكون مؤخرة).
- يحلل الاتجاه، الزخم، السيولة، الأخبار، ويصنف أفضل فرص الشراء.
- لا ينفذ أوامر شراء/بيع.
"""

import re
import math
import time
import html
import urllib.parse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import feedparser
import yfinance as yf
import streamlit as st


APP_NAME = "Kuwait Stock Smart Scanner v8.0 Full Market Forecast"
BOURSA_RSS_EN = "https://rss.boursakuwait.com.kw/rss/FeedFull.aspx?T=4"
BOURSA_RSS_AR = "https://rss.boursakuwait.com.kw/A/rss/FeedFull.aspx?T=4"
USER_AGENT = "Mozilla/5.0 (KuwaitStockSmartScanner/1.0)"

# قائمة احتياطية لأكبر أسهم السوق الأول في حال تعذر قراءة تقرير البورصة.

# أسماء عربية احتياطية لأسهم السوق الأول.
ARABIC_NAMES = {
    "NBK": "بنك الكويت الوطني",
    "GBK": "بنك الخليج",
    "ABK": "البنك الأهلي الكويتي",
    "KIB": "بنك الكويت الدولي",
    "BURG": "بنك برقان",
    "KFH": "بيت التمويل الكويتي",
    "BOUBYAN": "بنك بوبيان",
    "KINV": "شركة الكويت للاستثمار",
    "IFA": "الاستشارات المالية الدولية القابضة",
    "NINV": "شركة الاستثمارات الوطنية",
    "KPROJ": "شركة مشاريع الكويت القابضة",
    "ARZAN": "مجموعة أرزان المالية للتمويل والاستثمار",
    "AAYAN": "أعيان للإجارة والاستثمار",
    "KRE": "شركة الكويت العقارية",
    "URC": "الشركة المتحدة العقارية",
    "SRE": "شركة الصالحية العقارية",
    "MABANEE": "شركة المباني",
    "ALTIJARIA": "الشركة التجارية العقارية",
    "NIND": "مجموعة الصناعات الوطنية القابضة",
    "CABLE": "الخليج للكابلات والصناعات الكهربائية",
    "SHIP": "الصناعات الهندسية الثقيلة وبناء السفن",
    "BPCC": "بوبيان للبتروكيماويات",
    "MKHZN": "أجيليتي للمخازن العمومية",
    "ZAIN": "شركة الاتصالات المتنقلة - زين",
    "HUMANSOFT": "هيومن سوفت القابضة",
    "IFAHR": "إيفا للفنادق والمنتجعات",
    "CGC": "المجموعة المشتركة للمقاولات",
    "OULAFUEL": "الأولى للتسويق المحلي للوقود",
    "JAZEERA": "طيران الجزيرة",
    "GFH": "مجموعة جي إف إتش المالية",
    "WARBABANK": "بنك وربة",
    "STC": "شركة الاتصالات الكويتية - stc",
    "MEZZAN": "ميزان القابضة",
    "INTEGRATED": "المتكاملة القابضة",
    "BOURSA": "بورصة الكويت للأوراق المالية",
    "ALG": "علي الغانم وأولاده للسيارات",
    "BEYOUT": "بيوت القابضة",
    "ALFTAQA": "أكشن إنرجي",
    "TROLLEY": "ترولي للتجارة العامة",
}

def has_arabic(s):
    return bool(re.search(r"[\u0600-\u06FF]", str(s or "")))

def try_load_arabic_names(report_url, expected_count):
    """
    يحاول قراءة النسخة العربية من تقرير بورصة الكويت.
    إذا تطابق ترتيب الصفوف، يستخدم عمود أسماء الشركات العربية تلقائياً.
    """
    try:
        ar_url = report_url.replace("/en/", "/ar/")
        html_text = requests.get(ar_url, headers={"User-Agent": USER_AGENT}, timeout=20).text
        tables = pd.read_html(html_text)
        best_names = None
        best_score = -1
        for df in tables:
            if len(df) < max(10, int(expected_count * 0.75)):
                continue
            for col in df.columns:
                vals = df[col].astype(str).str.strip()
                sample = vals.head(min(expected_count, len(vals)))
                arabic_count = sum(len(re.findall(r"[\u0600-\u06FF]", v)) for v in sample)
                unique_ratio = sample.nunique(dropna=True) / max(1, len(sample))
                avg_len = sample.map(len).mean()
                score = arabic_count * unique_ratio
                # أسماء الشركات عادةً عربية، متنوعة، وأطول من اسم السوق.
                if arabic_count > 40 and unique_ratio > 0.35 and avg_len > 5 and score > best_score:
                    best_score = score
                    best_names = vals.reset_index(drop=True)
        if best_names is not None and len(best_names) >= expected_count:
            return best_names.iloc[:expected_count].tolist()
    except Exception:
        pass
    return None

FALLBACK = [('Premier', '101', 'NBK', 'NATIONAL BANK OF KUWAIT', 'بنك الكويت الوطني'), ('Premier', '102', 'GBK', 'GULF BANK', 'بنك الخليج'), ('Premier', '104', 'ABK', 'AL-AHLI BANK OF KUWAIT', 'البنك الأهلي الكويتي'), ('Premier', '106', 'KIB', 'KUWAIT INTERNATIONAL BANK', 'بنك الكويت الدولي'), ('Premier', '107', 'BURG', 'BURGAN BANK', 'بنك برقان'), ('Premier', '108', 'KFH', 'KUWAIT FINANCE HOUSE', 'بيت التمويل الكويتي'), ('Premier', '109', 'BOUBYAN', 'BOUBYAN BANK', 'بنك بوبيان'), ('Premier', '201', 'KINV', 'KUWAIT INVESTMENT COMPANY', 'الشركة الكويتية للاستثمار'), ('Premier', '203', 'IFA', 'INTERNATIONAL FINANCIAL ADVISERS HOLDING', 'شركة الاستشارات المالية الدولية القابضة'), ('Premier', '204', 'NINV', 'NATIONAL INVESTMENTS COMPANY', 'شركة الاستثمارات الوطنية'), ('Premier', '205', 'KPROJ', 'KUWAIT PROJECTS COMPANY (HOLDING)', 'شركة مشاريع الكويت القابضة'), ('Premier', '212', 'ARZAN', 'ARZAN FINANCIAL GROUP FOR FINANCING AND INVESTMENT', 'مجموعة أرزان الماليه للتمويل و الاستثمار'), ('Premier', '222', 'AAYAN', 'AAYAN LEASING & INVESTMENT CO.', 'شركة أعيان للاجارة والاستثمار'), ('Premier', '401', 'KRE', 'KUWAIT REAL ESTATE COMPANY', 'شركة عقارات الكويت'), ('Premier', '402', 'URC', 'UNITED REAL ESTATE COMPANY', 'شركة العقارات المتحدة'), ('Premier', '404', 'SRE', 'SALHIA REAL ESTATE COMPANY', 'شركة الصالحية العقارية'), ('Premier', '413', 'MABANEE', 'MABANEE COMPANY', 'شركة المباني'), ('Premier', '418', 'ALTIJARIA', 'THE COMMERCIAL REAL ESTATE CO.', 'الشركة التجارية العقارية'), ('Premier', '501', 'NIND', 'NATIONAL INDUSTRIES GROUP (HOLDING)', 'مجموعة الصناعات الوطنية (القابضة)'), ('Premier', '505', 'CABLE', 'GULF CABLES AND ELECTRICAL INDUSTRIES GROUP CO.', 'شركة مجموعة الخليج للكابلات و الصناعات الكهربائية'), ('Premier', '506', 'SHIP', 'HEAVY ENGINEERING INDUSTRIES AND SHIP BUILDING CO.', 'شركة الصناعات الهندسية الثقيلة وبناء السفن'), ('Premier', '514', 'BPCC', 'BOUBYAN PETROCHEMICAL CO.', 'شركة بوبيان للبتروكيماويات'), ('Premier', '603', 'MKHZN', 'AGILITY PUBLIC WAREHOUSING COMPANY', 'شركة أجيليتي للمخازن العمومية'), ('Premier', '605', 'ZAIN', 'MOBILE TELECOMMUNICATIONS COMPANY', 'شركة الاتصالات المتنقلة'), ('Premier', '623', 'HUMANSOFT', 'HUMANSOFT HOLDING CO.', 'شركة هيومن سوفت القابضة'), ('Premier', '634', 'IFAHR', 'IFA HOTELS & RESORTS CO.', 'شركة ايفا للفنادق والمنتجعات'), ('Premier', '635', 'CGC', 'COMBINED GROUP CONTRACTING CO.', 'شركة المجموعة المشتركة للمقاولات'), ('Premier', '645', 'OULAFUEL', 'OULA FUEL MARKETING CO.', 'الشركة الأولى للتسويق المحلي للوقود'), ('Premier', '654', 'JAZEERA', 'JAZEERA AIRWAYS CO.', 'شركة طيران الجزيرة'), ('Premier', '813', 'GFH', 'GFH BANK B.S.C.', 'بنك جي اف اتش ش.م.ب.'), ('Premier', '821', 'WARBABANK', 'WARBA BANK', 'بنك وربة'), ('Premier', '822', 'STC', 'KUWAIT TELECOMMUNICATIONS CO.', 'شركة الاتصالات الكويتية'), ('Premier', '823', 'MEZZAN', 'MEZZAN HOLDING CO', 'شركة ميزان القابضة'), ('Premier', '824', 'INTEGRATED', 'INTEGRATED HOLDING COMPANY', 'الشركة المتكاملة القابضة'), ('Premier', '827', 'BOURSA', 'BOURSA KUWAIT SECURITIES COMPANY', 'شركة بورصة الكويت للأوراق المالية'), ('Premier', '830', 'ALG', 'ALI ALGHANIM SONS AUTOMOTIVE COMPANY', 'شركة أولاد علي الغانم للسيارات'), ('Premier', '831', 'BEYOUT', 'BEYOUT HOLDING COMPANY', 'شركة بيوت القابضة'), ('Premier', '832', 'ALFTAQA', 'ACTION ENERGY COMPANY', 'الشركة العملية للطاقة'), ('Premier', '833', 'TROLLEY', 'TROLLEY GENERAL TRADING COMPANY', 'شركة ترولي للتجارة العامة'), ('Main', '103', 'CBK', 'COMMERCIAL BANK OF KUWAIT', 'البنك التجاري الكويتي'), ('Main', '2010', 'SPEC', 'SPECIALITIES GROUP HOLDING CO.', 'شركة مجموعة الخصوصية القابضة'), ('Main', '2011', 'MASAKEN', 'AL MASAKEN INTERNATIONAL REAL ESTATE DEVELOPMENT CO.', 'شركة المساكن الدولية للتطوير العقاري'), ('Main', '2012', 'DALQANRE', 'DALQAN REAL ESTATE CO.', 'شركة دلقان العقارية'), ('Main', '2014', 'MIDAN', 'AL-MAIDAN CLINIC FOR ORAL HEALTH SERVICES CO.', 'شركة عيادة الميدان لخدمات طب الأسنان'), ('Main', '2017', 'THURAYA', 'DAR AL THURAYA REAL ESTATE CO.', 'شركة دار الثريا العقارية'), ('Main', '2019', 'AMAR', 'AMAR FOR FINANCE AND LEASING CO.', 'شركة عمار للتمويل والاجارة'), ('Main', '202', 'FACIL', 'COMMERCIAL FACILITIES COMPANY', 'شركة التسهيلات التجارية'), ('Main', '207', 'COAST', 'COAST INVESTMENT & DEVELOPMENT COMPANY', 'شركة الساحل للتنمية و الاستثمار'), ('Main', '209', 'SECH', 'THE SECURITIES HOUSE CO.', 'شركة بيت الأوراق المالية'), ('Main', '213', 'MARKAZ', 'KUWAIT FINANCIAL CENTRE', 'المركز المالي الكويتي'), ('Main', '214', 'KMEFIC', 'KUWAIT AND MIDDLE EAST FINANCIAL INVESTMENT CO.', 'شركة الكويت و الشرق الأوسط للاستثمار المالي'), ('Main', '219', 'ALOLA', 'FIRST INVESTMENT COMPANY', 'الشركة الأولى للاستثمار'), ('Main', '221', 'GIH', 'GULF INVESTMENT HOUSE', 'شركة بيت الاستثمار الخليجي'), ('Main', '223', 'BAYANINV', 'BAYAN INVESTMENT HOLDING CO.', 'شركة بيان للاستثمار القابضة'), ('Main', '225', 'OSOUL', 'OSOUL INVESTMENT CO.', 'شركة أصول للاستثمار'), ('Main', '227', 'KFIC', 'KFIC INVEST', 'شركة كفيك للإستثمار'), ('Main', '228', 'KAMCO', 'KAMCO INVESTMENT COMPANY', 'شركة كامكو للاستثمار'), ('Main', '231', 'NIH', 'NATIONAL INTERNATIONAL HOLDING CO.', 'الشركة الوطنية الدولية القابضة'), ('Main', '232', 'UNICAP', 'UNICAP INVESTMENT AND FINANCE', 'يونيكاب للإستثمار و التمويل'), ('Main', '233', 'MADAR', 'AL MADAR KUWAIT HOLDING CO.', 'شركة المدار الكويتية القابضة'), ('Main', '234', 'ALDEERA', 'AL-DEERA HOLDING CO.', 'شركة الديرة القابضة'), ('Main', '235', 'ALSAFAT', 'ALSAFAT INVESTMENT COMPANY', 'شركة الصفاة للاستثمار'), ('Main', '237', 'EKTTITAB', 'EKTTITAB HOLDING CO.', 'شركة اكتتاب القابضة'), ('Main', '239', 'SOKOUK', 'SOKOUK HOLDING CO.', 'شركة صكوك القابضة'), ('Main', '241', 'NOOR', 'NOOR FINANCIAL INVESTMENT', 'شركة نور للاستثمار المالي'), ('Main', '242', 'TAMINV', 'TAMDEEN INVESTMENT CO.', 'شركة التمدين الاستثمارية'), ('Main', '245', 'EMIRATES', 'KUWAIT EMIRATES HOLDING COMPANY', 'الشركة الكويتية الإماراتية القابضة'), ('Main', '247', 'ASIYA', 'ASIYA CAPITAL INVESTMENT COMPANY', 'شركة آسيا كابيتال الاستثمارية'), ('Main', '249', 'RASIYAT', 'RASIYAT HOLDING COMPANY', 'شركة راسيات القابضة'), ('Main', '252', 'ALIMTIAZ', 'ALIMTIAZ GROUP HOLDING COMPANY', 'شركة مجموعة الامتياز القابضة'), ('Main', '301', 'KINS', 'KUWAIT INSURANCE COMPANY', 'شركة الكويت للتأمين'), ('Main', '302', 'GINS', 'GULF INSURANCE GROUP', 'مجموعة الخليج للتامين'), ('Main', '303', 'AINS', 'AL-AHLEIA INSURANCE COMPANY', 'الشركة الأهلية للتأمين'), ('Main', '304', 'WINSRE', 'WARBA INSURANCE AND REINSURANCE COMPANY', 'شركة وربة للتأمين و إعادة التأمين'), ('Main', '305', 'KUWAITRE', 'KUWAIT REINSURANCE COMPANY', 'شركة إعادة التأمين الكويتية'), ('Main', '306', 'FTI', 'FIRST TAKAFUL INSURANCE COMPANY', 'الشركة الأولي للتأمين التكافلي'), ('Main', '307', 'WETHAQ', 'WETHAQ TAKAFUL INSURANCE COMPANY', 'شركة وثاق للتأمين التكافلي'), ('Main', '403', 'NRE', 'THE NATIONAL REAL ESTATE COMPANY', 'الشركة الوطنية العقارية'), ('Main', '406', 'TAM', 'TAMDEEN REAL ESTATE COMPANY', 'شركة التمدين العقارية'), ('Main', '408', 'AREEC', 'AJIAL REAL ESTATE ENTERTAINMENT CO.', 'شركة أجيال العقارية الترفيهية'), ('Main', '410', 'ARABREC', 'AL-ARABIYA REAL ESTATE CO.', 'الشركة العربية العقارية'), ('Main', '412', 'ALENMA', 'ALENMA REAL ESTATE CO.', 'شركة الإنماء العقارية'), ('Main', '414', 'INJAZZAT', 'INJAZZAT REAL ESTATE DEV. CO.', 'شركة إنجازات للتنمية العقاريه'), ('Main', '419', 'SANAM', 'SANAM GROUP HOLDING COMPANY', 'شركة مجموعة سنام القابضة'), ('Main', '420', 'AAYANRE', 'AAYAN REAL ESTATE CO.', 'شركة أعيان العقارية'), ('Main', '421', 'AQAR', 'AQAR REAL ESTATE INVESTMENTS CO.', 'شركة عقار للاستثمارات العقارية'), ('Main', '422', 'ALAQARIA', 'KUWAIT REAL ESTATE HOLDING CO.', 'الشركة الكويتية العقارية القابضة'), ('Main', '423', 'MAZAYA', 'AL-MAZAYA HOLDING CO.', 'شركة المزايا القابضة'), ('Main', '427', 'TIJARA', 'TIJARA & REAL ESTATE INVESTMENT CO.', 'شركة التجارة والاستثمار العقاري'), ('Main', '429', 'ARKAN', 'ARKAN AL-KUWAIT REAL ESTATE CO.', 'شركة أركان الكويت العقارية'), ('Main', '431', 'ARGAN', 'ALARGAN INTERNATIONAL REAL ESTATE CO.', 'شركة الأرجان العالمية العقارية'), ('Main', '433', 'MUNSHAAT', 'MUNSHAAT REAL ESTATE PROJECTS CO.', 'شركة منشات للمشاريع العقارية'), ('Main', '435', 'KBT', 'KUWAIT BUSINESS TOWN REAL ESTATE CO.', 'شركة مدينة الاعمال الكويتية العقارية'), ('Main', '436', 'MANAZEL', 'MANAZEL HOLDING CO.', 'شركة منازل القابضة'), ('Main', '438', 'MENA', 'MENA REAL ESTATE COMPANY', 'شركة مينا العقارية'), ('Main', '440', 'MARAKEZ', 'MARAKEZ REAL ESTATE DEVELOPMENT COMPANY', 'شركة مراكز للتطوير العقاري'), ('Main', '503', 'KCEM', 'KUWAIT CEMENT COMPANY', 'شركة أسمنت الكويت'), ('Main', '508', 'PCEM', 'KUWAIT PORTLAND CEMENT COMPANY', 'شركة أسمنت بورتلاند كويت'), ('Main', '509', 'SHUAIBA', 'SHUAIBA INDUSTRIAL CO.', 'شركة الشعيبة الصناعية'), ('Main', '510', 'MRC', 'METAL & RECYCLING CO.', 'شركة المعادن والصناعات التحويلية'), ('Main', '511', 'KFOUC', 'KUWAIT FOUNDRY CO.', 'شركة السكب الكويتية'), ('Main', '512', 'ACICO', 'ACICO INDUSTRIES CO.', 'شركة أسيكو للصناعات'), ('Main', '517', 'ALKOUT', 'ALKOUT INDUSTRIAL PROJECTS CO.', 'شركة الكوت للمشاريع الصناعية'), ('Main', '520', 'NICBM', 'NATIONAL INDUSTRIES COMPANY', 'شركة الصناعات الوطنية'), ('Main', '522', 'EQUIPMENT', 'EQUIPMENT HOLDING CO.', 'شركة المعدات القابضة'), ('Main', '524', 'NCCI', 'NATIONAL CONSUMER HOLDING CO.', 'الشركة الوطنية الاستهلاكية القابضة'), ('Main', '529', 'WARBACAP', 'WARBA CAPITAL HOLDING CO.', 'شركة وربة كابيتال القابضة'), ('Main', '601', 'KCIN', 'KUWAIT NATIONAL CINEMA', 'شركة السينما الكويتية الوطنية'), ('Main', '602', 'KHOT', 'KUWAIT HOTELS COMPANY', 'شركة الفنادق الكويتية'), ('Main', '606', 'SENERGY', 'SENERGY HOLDING COMPANY', 'شركة سنرجي القابضة'), ('Main', '608', 'IPG', 'INDEPENDENT PETROLEUM GROUP', 'شركة المجموعة البترولية المستقلة'), ('Main', '609', 'CLEANING', 'NATIONAL CLEANING CO.', 'الشركة الوطنية للتنظيف'), ('Main', '613', 'OOREDOO', 'NATIONAL MOBILE TELECOMMUNICATIONS CO.', 'الشركة الوطنية للإتصالات المتنقلة'), ('Main', '616', 'ASC', 'AUTOMATED SYSTEMS COMPANY', 'شركة الأنظمة الآلية'), ('Main', '617', 'NAPESCO', 'NATIONAL PETROLEUM SERVICES COMPANY', 'الشركة الوطنية للخدمات البترولية'), ('Main', '618', 'KCPC', 'KUWAIT COMPANY FOR PROCESS PLANT CONSTRUCTION & CONTRACTING', 'الشركة الكويتية لبناء المعامل والمقاولات'), ('Main', '624', 'KPPC', 'PRIVATIZATION HOLDING CO.', 'شركة التخصيص القابضة'), ('Main', '627', 'ENERGYH', 'THE ENERGY HOUSE CO.', 'شركة بيت الطاقة القابضة'), ('Main', '630', 'GFC', 'GULF FRANCHISING HOLDING CO.', 'شركة الامتيازات الخليجية القابضة'), ('Main', '631', 'TAHSSILAT', 'CREDIT RATING & COLLECTION', 'شركة تصنيف وتحصيل الأموال'), ('Main', '633', 'ABAR', 'BURGAN CO. FOR WELL DRILLING', 'شركة برقان لحفر الآبار'), ('Main', '637', 'PAPCO', 'PALMS AGRO PRODUCTION CO.', 'شركة النخيل للانتاج الزراعي'), ('Main', '638', 'OSOS', 'OSOS HOLDING GROUP COMPANY', 'شركة مجموعة أسس القابضة'), ('Main', '640', 'UPAC', 'UNITED PROJECTS CO.', 'شركة المشاريع المتحدة للخدمات الجوية'), ('Main', '644', 'MASHAER', 'MASHAER HOLDING COMPANY', 'شركة مشاعر القابضة'), ('Main', '649', 'DIGITUS', 'DIGITUS GROUP FOR DIGITAL INFRASTRUCTURE', 'شركة مجموعة ديجتس ديجيتال انفراستراكتشر لمراكز المعلومات والاتصالات'), ('Main', '650', 'MUBARRAD', 'MUBARRAD HOLDING COMPANY', 'شركة مبرد القابضة'), ('Main', '651', 'MUNTAZAHAT', 'KUWAIT RESORTS COMPANY', 'الشركة الكويتية للمنتزهات'), ('Main', '652', 'ATC', 'ADVANCED TECHNOLOGY COMPANY', 'شركة التقدم التكنولوجي'), ('Main', '655', 'SOOR', 'SOOR FUEL MARKETING COMPANY', 'شركة السور لتسويق الوقود'), ('Main', '657', 'FUTUREKID', 'FUTURE KID ENTERTAINMENT & REAL ESTATE', 'شركة طفل المستقبل الترفيهية العقارية'), ('Main', '701', 'CATTL', 'LIVESTOCK TRANSPORT & TRADING CO.', 'شركة نقل و تجارة المواشي'), ('Main', '806', 'QIC', 'UMM AL QAIWAIN GENERAL INVESTMENTS COMPANY', 'شركة أم القيوين للإستثمارات العامة'), ('Main', '811', 'VALMORE', 'VALMORE HOLDING', 'فالمور القابضة للاستثمار'), ('Main', '812', 'BKIKWT', 'BAHRAIN KUWAIT INSURANCE CO.', 'الشركة البحرينية الكويتية للتأمين'), ('Main', '817', 'INOVEST', 'INOVEST', 'شركة إنوفست'), ('Main', '825', 'ALMANAR', 'AL-MANAR FINANCING AND LEASING CO.', 'شركة المنار للتمويل والإجارة'), ('Main', '826', 'AZNOULA', 'SHAMAL AZ-ZOUR AL-OULA POWER AND WATER COMPANY', 'شركة شمال الزور الأولى للطاقة والمياه'), ('Main', '829', 'JTC', 'JTC LOGISTICS TRANSPORTATION & STEVEDORING COMPANY', 'شركة جي تي سي لوجستيك للنقليات والمناولة')]

# تحديث قاموس الأسماء العربية من القائمة الرسمية الكاملة
ARABIC_NAMES.update({row[2]: row[4] for row in FALLBACK})

POSITIVE_WORDS = {
    "profit": 1.5, "profits": 1.5, "growth": 1.2, "increase": 0.7, "increased": 0.7,
    "dividend": 1.5, "distribution": 0.8, "contract": 1.0, "award": 1.0,
    "upgrade": 1.5, "acquisition": 0.8, "approval": 0.6, "record": 0.5,
    "positive": 0.8, "expansion": 0.7, "partnership": 0.6,
    "ربح": 1.5, "أرباح": 1.5, "الارباح": 1.5, "الأرباح": 1.5, "نمو": 1.2,
    "ارتفاع": 0.7, "زيادة": 0.7, "توزيع": 1.0, "توزيعات": 1.2, "منحة": 1.0,
    "عقد": 1.0, "ترسية": 1.2, "مناقصة": 0.7, "رفع التصنيف": 1.5,
    "استحواذ": 0.8, "موافقة": 0.6, "توسع": 0.7, "شراكة": 0.6
}
NEGATIVE_WORDS = {
    "loss": -1.8, "losses": -1.8, "decline": -0.9, "decrease": -0.8,
    "suspension": -2.0, "suspended": -2.0, "lawsuit": -1.4, "court": -0.7,
    "downgrade": -1.8, "default": -2.5, "warning": -1.0, "impairment": -1.2,
    "خسارة": -1.8, "خسائر": -1.8, "انخفاض": -0.9, "تراجع": -0.9,
    "إيقاف": -2.0, "ايقاف": -2.0, "وقف التداول": -2.0, "دعوى": -1.4,
    "دعاوى": -1.4, "حكم": -0.8, "خفض التصنيف": -1.8, "تعثر": -2.5,
    "تحذير": -1.0, "مخصصات": -0.7
}

st.set_page_config(page_title=APP_NAME, page_icon="📊", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
html, body { direction: rtl; text-align: right; }
[data-testid="stAppViewContainer"] { direction: rtl; }
[data-testid="stMarkdownContainer"] { text-align: right; }
.small-note {font-size:0.9rem; opacity:0.8;}
.good {font-weight:700;}
.block-container { padding-top: .75rem; padding-left: .65rem; padding-right: .65rem; max-width: 100%; }
h1 { font-size: 1.55rem !important; line-height: 1.3 !important; }
h2 { font-size: 1.22rem !important; }
h3 { font-size: 1.08rem !important; }
[data-testid="stMetricValue"] { font-size: 1.28rem !important; }
.stButton button { min-height: 46px; }
[data-testid="stDataFrame"] { direction: ltr; }
[data-testid="stDataFrame"] * { white-space: nowrap; }
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"], svg { direction: ltr !important; }
@media (max-width: 700px) {
  .block-container { padding-top: .55rem; padding-left: .45rem; padding-right: .45rem; }
  h1 { font-size: 1.38rem !important; }
  [data-testid="column"] { min-width: 0 !important; }
  div[data-testid="stMetric"] { padding: .35rem .2rem; }
  .stButton button { width: 100%; min-height: 48px; }
  [data-testid="stDataFrame"] { font-size: .80rem; }
}
</style>
""", unsafe_allow_html=True)


def safe_float(x, default=np.nan):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def normalize_text(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.lower()
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)
    s = s.replace("أ","ا").replace("إ","ا").replace("آ","ا")
    s = re.sub(r"[^0-9a-z\u0600-\u06FF]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def news_sentiment(text):
    t = normalize_text(text)
    score = 0.0
    for k, v in POSITIVE_WORDS.items():
        if normalize_text(k) in t:
            score += v
    for k, v in NEGATIVE_WORDS.items():
        if normalize_text(k) in t:
            score += v
    return max(-6.0, min(6.0, score))


def latest_completed_quarter_url():
    now = datetime.now()
    q_month = ((now.month - 1) // 3) * 3
    year = now.year
    if q_month == 0:
        q_month = 12
        year -= 1
    return f"https://reports.boursakuwait.com.kw/en/indicativefreefloat/{q_month}/{year}"


@st.cache_data(ttl=86400, show_spinner=False)
def load_universe():
    url = latest_completed_quarter_url()
    try:
        headers = {"User-Agent": USER_AGENT}
        tables = pd.read_html(requests.get(url, headers=headers, timeout=20).text)
        table = None
        for df in tables:
            cols = [str(c).strip().lower() for c in df.columns]
            if "ticker" in cols and "name" in cols:
                table = df.copy()
                break
        if table is None:
            raise RuntimeError("لم يتم العثور على جدول الشركات")
        # توحيد الأعمدة
        cmap = {str(c).strip().lower(): c for c in table.columns}
        out = pd.DataFrame({
            "Market": table[cmap["market"]].astype(str),
            "SecCode": table[cmap["seccode"]].astype(str),
            "Ticker": table[cmap["ticker"]].astype(str).str.strip(),
            "Name": table[cmap["name"]].astype(str).str.strip(),
        })
        ff_pct_col = next((c for c in table.columns if "free float %" in str(c).lower()), None)
        ff_val_col = next((c for c in table.columns if "free float value" in str(c).lower()), None)
        out["FreeFloatPct"] = pd.to_numeric(
            table[ff_pct_col].astype(str).str.replace("%","",regex=False).str.replace(",","",regex=False),
            errors="coerce") if ff_pct_col is not None else np.nan
        out["FreeFloatValue"] = pd.to_numeric(
            table[ff_val_col].astype(str).str.replace(",","",regex=False),
            errors="coerce") if ff_val_col is not None else np.nan
        out = out.dropna(subset=["Ticker"]).drop_duplicates("Ticker").reset_index(drop=True)

        # محاولة جلب الأسماء العربية من النسخة العربية من نفس التقرير.
        ar_names = try_load_arabic_names(url, len(out))
        if ar_names and len(ar_names) == len(out):
            out["NameAR"] = ar_names
        else:
            out["NameAR"] = out["Ticker"].map(ARABIC_NAMES).fillna(out["Name"])

        return out, url, False
    except Exception:
        out = pd.DataFrame(FALLBACK, columns=["Market","SecCode","Ticker","Name","NameAR"])
        out["FreeFloatPct"] = np.nan
        out["FreeFloatValue"] = np.nan
        return out, url, True


@st.cache_data(ttl=600, show_spinner=False)
def load_boursa_rss():
    items = []
    for lang, url in [("EN", BOURSA_RSS_EN), ("AR", BOURSA_RSS_AR)]:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            feed = feedparser.parse(r.content)
            for e in feed.entries[:600]:
                title = str(getattr(e, "title", ""))
                summary = str(getattr(e, "summary", getattr(e, "description", "")))
                link = str(getattr(e, "link", ""))
                published = str(getattr(e, "published", getattr(e, "updated", "")))
                items.append({
                    "lang": lang, "title": title, "summary": summary,
                    "link": link, "published": published,
                    "text_norm": normalize_text(title + " " + summary),
                    "sentiment": news_sentiment(title + " " + summary)
                })
        except Exception:
            pass
    # إزالة التكرار
    uniq = {}
    for x in items:
        key = (normalize_text(x["title"]), x["link"])
        uniq[key] = x
    return list(uniq.values())


def match_official_news(ticker, name, rss_items, max_items=12):
    tick = normalize_text(ticker)
    name_norm = normalize_text(name)
    name_tokens = [x for x in name_norm.split() if len(x) >= 4]
    matched = []
    for x in rss_items:
        tx = x["text_norm"]
        ticker_hit = re.search(rf"(^|\s){re.escape(tick)}(\s|$)", tx) is not None
        token_hits = sum(1 for tok in name_tokens[:6] if tok in tx)
        name_hit = token_hits >= min(2, max(1, len(name_tokens)))
        if ticker_hit or name_hit:
            matched.append(x)
    return matched[:max_items]


@st.cache_data(ttl=900, show_spinner=False)
def google_news(company_name, ticker, max_items=6):
    q = f'"{company_name}" OR "{ticker}" Kuwait stock'
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en&gl=KW&ceid=KW:en"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        feed = feedparser.parse(r.content)
        out = []
        for e in feed.entries[:max_items]:
            title = str(getattr(e, "title", ""))
            summary = str(getattr(e, "summary", ""))
            out.append({
                "title": title,
                "summary": summary,
                "link": str(getattr(e, "link", "")),
                "published": str(getattr(e, "published", "")),
                "sentiment": news_sentiment(title + " " + summary),
            })
        return out
    except Exception:
        return []


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out


def atr(df, period=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    prev = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_indicators(df):
    df = df.dropna().copy()
    if len(df) < 80:
        return None
    c = pd.to_numeric(df["Close"], errors="coerce")
    v = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    h = pd.to_numeric(df["High"], errors="coerce")
    l = pd.to_numeric(df["Low"], errors="coerce")

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    r = rsi(c)
    a = atr(pd.DataFrame({"High": h, "Low": l, "Close": c}))
    last = float(c.iloc[-1])

    low20 = float(l.tail(20).min())
    high20 = float(h.tail(20).max())
    low60 = float(l.tail(60).min())
    high60 = float(h.tail(60).max())
    high252 = float(h.tail(min(252, len(h))).max())
    low252 = float(l.tail(min(252, len(l))).min())

    mom5 = (last / float(c.iloc[-6]) - 1) * 100 if len(c) > 6 and c.iloc[-6] else 0.0
    mom20 = (last / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 and c.iloc[-21] else 0.0
    mom60 = (last / float(c.iloc[-61]) - 1) * 100 if len(c) > 61 and c.iloc[-61] else 0.0

    vol20 = float(v.tail(20).mean()) if len(v) >= 20 else float(v.mean())
    vol60 = float(v.tail(60).mean()) if len(v) >= 60 else vol20
    vol_ratio = (float(v.iloc[-1]) / vol20) if vol20 and not np.isnan(vol20) else 1.0
    value20 = last * vol20 if vol20 and not np.isnan(vol20) else np.nan

    return {
        "price": last,
        "ema20": float(ema20.iloc[-1]), "ema50": float(ema50.iloc[-1]), "ema200": float(ema200.iloc[-1]),
        "rsi": float(r.iloc[-1]) if pd.notna(r.iloc[-1]) else 50.0,
        "macd": float(macd.iloc[-1]), "macd_signal": float(macd_sig.iloc[-1]),
        "atr": float(a.iloc[-1]) if pd.notna(a.iloc[-1]) else max(last * 0.02, 0.001),
        "mom5": float(mom5), "mom20": float(mom20), "mom60": float(mom60),
        "vol_ratio": float(vol_ratio),
        "support20": low20, "support60": low60, "res20": high20, "res60": high60,
        "high52": high252, "low52": low252,
        "avg_vol20": vol20, "avg_vol60": vol60,
        "daily_value_est": float(value20) if pd.notna(value20) else np.nan,
        "history_len": len(df),
    }


def technical_score(ind):
    """0..25 — اتجاه + زخم + توقيت، بدون تضخيم وزن المؤشرات."""
    if not ind:
        return 0.0, []
    s = 0.0
    reasons = []
    p, e20, e50, e200 = ind["price"], ind["ema20"], ind["ema50"], ind["ema200"]

    if p > e20 > e50:
        s += 7.0; reasons.append("اتجاه قصير صاعد")
    elif p > e20:
        s += 4.5; reasons.append("السعر فوق EMA20")
    elif p > e50:
        s += 2.5
    else:
        reasons.append("السعر دون المتوسطات القصيرة")

    if e50 > e200:
        s += 4.0; reasons.append("الاتجاه المتوسط إيجابي")
    elif p > e200:
        s += 2.0

    rr = ind["rsi"]
    if 48 <= rr <= 67:
        s += 4.0; reasons.append("RSI صحي")
    elif 42 <= rr < 48 or 67 < rr <= 72:
        s += 2.5
    elif rr > 78:
        s -= 1.0; reasons.append("تشبع شرائي مرتفع")
    elif rr < 32:
        s += 1.0; reasons.append("تشبع بيعي محتمل")

    if ind["macd"] > ind["macd_signal"]:
        s += 3.0; reasons.append("MACD إيجابي")

    if ind["mom20"] > 0:
        s += 2.0
        if ind["mom60"] > 0:
            s += 1.5; reasons.append("زخم 20 و60 يوم موجب")
        else:
            reasons.append("زخم 20 يوم موجب")
    elif ind["mom60"] > 0:
        s += 0.8

    if ind["vol_ratio"] >= 1.25:
        s += 2.5; reasons.append("حجم تداول أعلى بوضوح من المتوسط")
    elif ind["vol_ratio"] >= 1.05:
        s += 1.0

    return float(np.clip(s, 0, 25)), reasons


def liquidity_scores(universe):
    ff = universe["FreeFloatValue"].astype(float)
    if ff.notna().sum() < 5:
        return pd.Series(7.5, index=universe.index)
    rank = ff.rank(pct=True).fillna(0.5)
    return (rank * 15).clip(0, 15)


def _news_age_days(published):
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(str(published))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400.0)
    except Exception:
        return 30.0


def _disclosure_type(text):
    t = normalize_text(text)
    mapping = [
        ("نتائج مالية", ["نتائج", "ارباح", "خسائر", "financial result", "profit", "earnings"]),
        ("توزيعات", ["توزيعات", "ارباح نقدية", "منحة", "dividend", "distribution"]),
        ("عقد/مناقصة", ["عقد", "ترسية", "مناقصة", "contract", "award", "tender"]),
        ("تصنيف ائتماني", ["تصنيف", "credit rating", "upgrade", "downgrade"]),
        ("دعوى/حكم", ["دعوى", "حكم", "قضية", "lawsuit", "court"]),
        ("استحواذ/تخارج", ["استحواذ", "تخارج", "acquisition", "disposal", "sale"]),
        ("معلومة جوهرية", ["جوهرية", "material information", "material"]),
    ]
    for name, keys in mapping:
        if any(normalize_text(k) in t for k in keys):
            return name
    return "إفصاح عام"


def official_news_analysis(items):
    """0..15 مع وزن أعلى للخبر الأحدث ونوع الإفصاح."""
    if not items:
        return 7.5, [], []
    raw = 0.0
    reasons = []
    categories = []
    cat_bonus = {
        "نتائج مالية": 1.25, "توزيعات": 1.20, "عقد/مناقصة": 1.10,
        "تصنيف ائتماني": 1.10, "دعوى/حكم": 1.15,
        "استحواذ/تخارج": 1.05, "معلومة جوهرية": 1.00, "إفصاح عام": 0.85
    }
    for i, x in enumerate(items[:15]):
        age = _news_age_days(x.get("published", ""))
        freshness = math.exp(-age / 28.0)
        cat = _disclosure_type(x.get("title", "") + " " + x.get("summary", ""))
        categories.append(cat)
        raw += float(x.get("sentiment", 0.0)) * freshness * cat_bonus.get(cat, 1.0)
    score = float(np.clip(7.5 + raw * 1.35, 0, 15))
    if score >= 10:
        reasons.append("الإفصاحات الرسمية داعمة")
    elif score <= 5:
        reasons.append("الإفصاحات الرسمية سلبية/حذرة")
    if categories:
        top_cat = pd.Series(categories).value_counts().index[0]
        reasons.append(f"أبرز نوع إفصاح: {top_cat}")
    return score, reasons, categories


def official_news_score(items):
    return official_news_analysis(items)[0]


def web_news_analysis(items):
    """0..10 — وزن زمني للأخبار العامة."""
    if not items:
        return 5.0, []
    raw = 0.0
    for x in items[:10]:
        age = _news_age_days(x.get("published", ""))
        freshness = math.exp(-age / 21.0)
        raw += float(x.get("sentiment", 0.0)) * freshness
    score = float(np.clip(5.0 + raw * 0.9, 0, 10))
    reasons = []
    if score >= 6.8:
        reasons.append("الأخبار العامة إيجابية")
    elif score <= 3.5:
        reasons.append("الأخبار العامة سلبية")
    return score, reasons


def web_news_score(items):
    return web_news_analysis(items)[0]


@st.cache_data(ttl=900, show_spinner=False)
def download_prices(symbols):
    """
    تحميل الأسعار على دفعات صغيرة حتى لا يفشل طلب واحد كبير عند فحص 139 شركة.
    """
    if not symbols:
        return {}

    out = {}
    batch_size = 20

    for start in range(0, len(symbols), batch_size):
        batch_symbols = symbols[start:start + batch_size]
        ys = [s + ".KW" for s in batch_symbols]
        try:
            data = yf.download(
                tickers=ys, period="2y", interval="1d",
                auto_adjust=False, progress=False, threads=True,
                group_by="ticker", timeout=35
            )
        except Exception:
            continue

        if len(ys) == 1:
            if data is not None and not data.empty:
                out[batch_symbols[0]] = data.rename(columns=lambda x: str(x)).dropna(how="all")
            continue

        for t, y in zip(batch_symbols, ys):
            try:
                d = data[y].copy()
                if not d.dropna(how="all").empty:
                    out[t] = d.dropna(how="all")
            except Exception:
                pass

    return out


@st.cache_data(ttl=3600, show_spinner=False)
def fundamental_info(ticker):
    """
    أساسيات + توصيات محللين + بيانات مالية ربع سنوية عند توفرها.
    بعض الشركات الكويتية قد لا تتوفر لها كل الحقول من Yahoo Finance.
    """
    symbol = ticker + ".KW"
    try:
        tk = yf.Ticker(symbol)
        info = tk.get_info() or {}

        out = {
            "sector": info.get("sector") or "",
            "industry": info.get("industry") or "",
            "marketCap": safe_float(info.get("marketCap")),
            "trailingPE": safe_float(info.get("trailingPE")),
            "forwardPE": safe_float(info.get("forwardPE")),
            "priceToBook": safe_float(info.get("priceToBook")),
            "returnOnEquity": safe_float(info.get("returnOnEquity")),
            "returnOnAssets": safe_float(info.get("returnOnAssets")),
            "revenueGrowth": safe_float(info.get("revenueGrowth")),
            "earningsGrowth": safe_float(info.get("earningsGrowth")),
            "dividendYield": safe_float(info.get("dividendYield")),
            "debtToEquity": safe_float(info.get("debtToEquity")),
            "currentRatio": safe_float(info.get("currentRatio")),
            "profitMargins": safe_float(info.get("profitMargins")),
            "operatingMargins": safe_float(info.get("operatingMargins")),
            "freeCashflow": safe_float(info.get("freeCashflow")),
            "operatingCashflow": safe_float(info.get("operatingCashflow")),

            "recommendationKey": str(info.get("recommendationKey") or ""),
            "recommendationMean": safe_float(info.get("recommendationMean")),
            "numberOfAnalystOpinions": safe_float(info.get("numberOfAnalystOpinions"), 0.0),
            "targetMeanPrice": safe_float(info.get("targetMeanPrice")),
            "targetMedianPrice": safe_float(info.get("targetMedianPrice")),
            "targetHighPrice": safe_float(info.get("targetHighPrice")),
            "targetLowPrice": safe_float(info.get("targetLowPrice")),
            "analystStrongBuy": 0,
            "analystBuy": 0,
            "analystHold": 0,
            "analystSell": 0,
            "analystStrongSell": 0,

            # Quarterly metrics
            "qRevenueLatest": np.nan,
            "qRevenueYoY": np.nan,
            "qNetIncomeLatest": np.nan,
            "qNetIncomeYoY": np.nan,
            "qEPSLatest": np.nan,
            "qEPSYoY": np.nan,
            "qOperatingCashflowLatest": np.nan,
            "qOperatingCashflowYoY": np.nan,
            "qTotalDebtLatest": np.nan,
            "qCashLatest": np.nan,
            "quarterlyDataPoints": 0,
        }

        def _row_series(df, candidates):
            if not isinstance(df, pd.DataFrame) or df.empty:
                return None
            for name in candidates:
                if name in df.index:
                    s = pd.to_numeric(df.loc[name], errors="coerce").dropna()
                    if len(s):
                        return s
            # fuzzy
            norm = {normalize_text(i): i for i in df.index}
            for cand in candidates:
                nc = normalize_text(cand)
                for ni, orig in norm.items():
                    if nc in ni or ni in nc:
                        s = pd.to_numeric(df.loc[orig], errors="coerce").dropna()
                        if len(s):
                            return s
            return None

        def _latest_yoy(s):
            if s is None or len(s) == 0:
                return np.nan, np.nan
            vals = list(s.values)
            latest = safe_float(vals[0])
            yoy = np.nan
            # quarterly statements typically ordered newest->oldest.
            if len(vals) >= 5:
                prev = safe_float(vals[4])
                if pd.notna(latest) and pd.notna(prev) and prev != 0:
                    yoy = (latest / prev - 1.0) * 100.0
            return latest, yoy

        try:
            qinc = tk.quarterly_income_stmt
        except Exception:
            qinc = pd.DataFrame()
        try:
            qcf = tk.quarterly_cashflow
        except Exception:
            qcf = pd.DataFrame()
        try:
            qbs = tk.quarterly_balance_sheet
        except Exception:
            qbs = pd.DataFrame()

        rev = _row_series(qinc, ["Total Revenue", "Operating Revenue", "Revenue"])
        ni = _row_series(qinc, ["Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations"])
        eps = _row_series(qinc, ["Diluted EPS", "Basic EPS"])
        ocf = _row_series(qcf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        debt = _row_series(qbs, ["Total Debt", "Long Term Debt And Capital Lease Obligation", "Long Term Debt"])
        cash = _row_series(qbs, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"])

        out["qRevenueLatest"], out["qRevenueYoY"] = _latest_yoy(rev)
        out["qNetIncomeLatest"], out["qNetIncomeYoY"] = _latest_yoy(ni)
        out["qEPSLatest"], out["qEPSYoY"] = _latest_yoy(eps)
        out["qOperatingCashflowLatest"], out["qOperatingCashflowYoY"] = _latest_yoy(ocf)
        out["qTotalDebtLatest"], _ = _latest_yoy(debt)
        out["qCashLatest"], _ = _latest_yoy(cash)

        q_fields = ["qRevenueLatest","qNetIncomeLatest","qEPSLatest","qOperatingCashflowLatest","qTotalDebtLatest","qCashLatest"]
        out["quarterlyDataPoints"] = sum(1 for k in q_fields if pd.notna(out.get(k, np.nan)))

        # Analyst summary
        try:
            rs = tk.recommendations_summary
            if isinstance(rs, pd.DataFrame) and not rs.empty:
                row = None
                if "period" in rs.columns:
                    cur = rs[rs["period"].astype(str).str.lower().eq("0m")]
                    if not cur.empty:
                        row = cur.iloc[0]
                if row is None:
                    row = rs.iloc[0]

                def _ival(name):
                    try:
                        v = row.get(name, 0)
                        return int(float(v)) if pd.notna(v) else 0
                    except Exception:
                        return 0

                out["analystStrongBuy"] = _ival("strongBuy")
                out["analystBuy"] = _ival("buy")
                out["analystHold"] = _ival("hold")
                out["analystSell"] = _ival("sell")
                out["analystStrongSell"] = _ival("strongSell")
        except Exception:
            pass

        if not pd.notna(out["targetMeanPrice"]):
            try:
                targets = tk.analyst_price_targets
                if isinstance(targets, dict):
                    out["targetMeanPrice"] = safe_float(targets.get("mean"))
                    out["targetMedianPrice"] = safe_float(targets.get("median"))
                    out["targetHighPrice"] = safe_float(targets.get("high"))
                    out["targetLowPrice"] = safe_float(targets.get("low"))
            except Exception:
                pass

        detailed_count = (
            out["analystStrongBuy"] + out["analystBuy"] + out["analystHold"] +
            out["analystSell"] + out["analystStrongSell"]
        )
        if detailed_count > 0:
            out["numberOfAnalystOpinions"] = max(
                safe_float(out.get("numberOfAnalystOpinions"), 0.0),
                float(detailed_count)
            )

        return out
    except Exception:
        return {}


def fundamental_score(info):
    """0..15 — جودة ونمو وتقييم وتوزيعات. 7.5 نقطة محايدة عند نقص البيانات."""
    if not info:
        return 7.5, []
    s = 7.5
    rs = []
    pe = info.get("trailingPE", np.nan)
    pb = info.get("priceToBook", np.nan)
    roe = info.get("returnOnEquity", np.nan)
    rg = info.get("revenueGrowth", np.nan)
    eg = info.get("earningsGrowth", np.nan)
    dy = info.get("dividendYield", np.nan)

    if not np.isnan(pe):
        if 0 < pe <= 14:
            s += 1.3; rs.append("P/E جذاب")
        elif 14 < pe <= 22:
            s += 0.5
        elif pe > 35:
            s -= 1.0; rs.append("P/E مرتفع")
        elif pe <= 0:
            s -= 1.0

    if not np.isnan(pb):
        if 0 < pb <= 1.8:
            s += 0.8
        elif pb > 5:
            s -= 0.6

    if not np.isnan(roe):
        if roe >= 0.16:
            s += 1.7; rs.append("ROE قوي")
        elif roe >= 0.10:
            s += 0.8
        elif roe < 0:
            s -= 1.5; rs.append("ROE سلبي")

    if not np.isnan(rg):
        if rg >= 0.10:
            s += 1.2; rs.append("نمو إيرادات قوي")
        elif rg > 0.03:
            s += 0.5
        elif rg < -0.08:
            s -= 1.0; rs.append("تراجع إيرادات")

    if not np.isnan(eg):
        if eg >= 0.12:
            s += 1.5; rs.append("نمو أرباح قوي")
        elif eg > 0.03:
            s += 0.6
        elif eg < -0.12:
            s -= 1.2; rs.append("تراجع أرباح")

    if not np.isnan(dy):
        if dy >= 0.04:
            s += 1.0; rs.append("عائد توزيعات جيد")
        elif dy >= 0.02:
            s += 0.5

    return float(np.clip(s, 0, 15)), rs




def sector_group(info, ticker=""):
    sec = normalize_text(info.get("sector", "") if info else "")
    ind = normalize_text(info.get("industry", "") if info else "")
    t = str(ticker).upper()

    bank_tickers = {"NBK","GBK","ABK","KIB","BURG","KFH","BOUBYAN","WARBABANK"}
    if t in bank_tickers or "bank" in sec or "bank" in ind or "بنك" in sec:
        return "بنوك"
    if "real estate" in sec or "real estate" in ind or "reit" in ind:
        return "عقار"
    if "telecom" in sec or "communication" in sec or "telecom" in ind:
        return "اتصالات"
    if "industrial" in sec or "industrial" in ind or "manufactur" in ind:
        return "صناعة"
    if "energy" in sec or "oil" in ind or "gas" in ind:
        return "طاقة"
    if "financial" in sec or "capital markets" in ind or "asset management" in ind:
        return "استثمار وخدمات مالية"
    if "consumer" in sec or "retail" in ind or "food" in ind:
        return "استهلاكي"
    return "عام"


def quarterly_financial_score(info, sector="عام"):
    """0..10 — نمو الربع وجودة الربح/الكاش. 5 محايد عند نقص البيانات."""
    if not info:
        return 5.0, [], 0

    s = 5.0
    reasons = []
    points = int(info.get("quarterlyDataPoints", 0) or 0)

    rev_yoy = safe_float(info.get("qRevenueYoY"))
    ni_yoy = safe_float(info.get("qNetIncomeYoY"))
    eps_yoy = safe_float(info.get("qEPSYoY"))
    ocf_yoy = safe_float(info.get("qOperatingCashflowYoY"))
    debt = safe_float(info.get("qTotalDebtLatest"))
    cash = safe_float(info.get("qCashLatest"))

    # Banks: revenue less meaningful than earnings/ROE/book valuation.
    if sector != "بنوك" and pd.notna(rev_yoy):
        if rev_yoy >= 15:
            s += 1.2; reasons.append(f"نمو إيرادات ربعي قوي {rev_yoy:.0f}%")
        elif rev_yoy >= 5:
            s += 0.6
        elif rev_yoy <= -10:
            s -= 1.0; reasons.append(f"تراجع إيرادات ربعية {abs(rev_yoy):.0f}%")

    if pd.notna(ni_yoy):
        if ni_yoy >= 20:
            s += 1.8; reasons.append(f"نمو صافي الربح ربعي {ni_yoy:.0f}%")
        elif ni_yoy >= 7:
            s += 0.9
        elif ni_yoy <= -20:
            s -= 1.6; reasons.append(f"تراجع صافي الربح ربعي {abs(ni_yoy):.0f}%")

    if pd.notna(eps_yoy):
        if eps_yoy >= 15:
            s += 1.0; reasons.append("نمو ربحية السهم")
        elif eps_yoy <= -15:
            s -= 0.8

    if sector != "بنوك" and pd.notna(ocf_yoy):
        if ocf_yoy >= 15:
            s += 0.8; reasons.append("تحسن التدفق النقدي التشغيلي")
        elif ocf_yoy <= -25:
            s -= 0.7

    if sector != "بنوك" and pd.notna(debt) and pd.notna(cash) and cash > 0:
        ratio = debt / cash
        if ratio <= 1.0:
            s += 0.5
        elif ratio >= 5.0:
            s -= 0.6; reasons.append("مديونية مرتفعة مقابل النقد")

    # Reduce influence when quarterly data is sparse.
    if points <= 1:
        s = 5.0 + (s - 5.0) * 0.35
    elif points <= 3:
        s = 5.0 + (s - 5.0) * 0.70

    return float(np.clip(s, 0, 10)), reasons, points


def sector_quality_score(info, sector):
    """0..10 — معايير تختلف حسب القطاع."""
    if not info:
        return 5.0, []

    s = 5.0
    reasons = []
    pe = safe_float(info.get("trailingPE"))
    pb = safe_float(info.get("priceToBook"))
    roe = safe_float(info.get("returnOnEquity"))
    dy = safe_float(info.get("dividendYield"))
    margin = safe_float(info.get("profitMargins"))
    de = safe_float(info.get("debtToEquity"))
    rg = safe_float(info.get("revenueGrowth"))
    eg = safe_float(info.get("earningsGrowth"))

    if sector == "بنوك":
        if pd.notna(roe):
            if roe >= 0.15: s += 1.8; reasons.append("ROE قوي للبنوك")
            elif roe >= 0.10: s += 0.8
            elif roe < 0.06: s -= 0.8
        if pd.notna(pb):
            if 0 < pb <= 1.8: s += 1.0; reasons.append("P/B مناسب")
            elif pb >= 4: s -= 0.8
        if pd.notna(dy):
            if dy >= 0.04: s += 0.8; reasons.append("توزيعات جيدة")
        if pd.notna(eg):
            if eg > 0.08: s += 0.8
            elif eg < -0.10: s -= 0.8
    elif sector == "عقار":
        if pd.notna(pb):
            if 0 < pb <= 1.5: s += 1.0
            elif pb > 4: s -= 0.6
        if pd.notna(de):
            if de < 100: s += 0.7
            elif de > 250: s -= 1.0; reasons.append("مديونية مرتفعة للعقار")
        if pd.notna(dy) and dy >= 0.04:
            s += 0.7
    elif sector == "اتصالات":
        if pd.notna(margin):
            if margin >= 0.12: s += 1.0; reasons.append("هامش ربح جيد")
            elif margin < 0.05: s -= 0.7
        if pd.notna(dy) and dy >= 0.04:
            s += 1.0; reasons.append("توزيعات قوية")
        if pd.notna(eg) and eg > 0.05:
            s += 0.7
    elif sector in {"صناعة","طاقة"}:
        if pd.notna(margin) and margin >= 0.10:
            s += 0.8
        if pd.notna(de):
            if de < 120: s += 0.6
            elif de > 250: s -= 0.9
        if pd.notna(rg):
            if rg > 0.08: s += 0.8
            elif rg < -0.10: s -= 0.7
    else:
        if pd.notna(roe):
            if roe >= 0.14: s += 1.0
            elif roe < 0: s -= 1.0
        if pd.notna(pe):
            if 0 < pe <= 16: s += 0.7
            elif pe > 35: s -= 0.7
        if pd.notna(rg):
            if rg > 0.08: s += 0.7
            elif rg < -0.08: s -= 0.7
        if pd.notna(dy) and dy >= 0.035:
            s += 0.5

    return float(np.clip(s, 0, 10)), reasons



def forecast_horizons(df, ind):
    """
    Forecast PRO الخفيف:
    يقارن حالة السهم الحالية بحالات تاريخية مشابهة بدون شروط صارمة،
    ثم يقيس عائد 5/10/20/60 جلسة بعد تلك الحالات.

    النتيجة احتمالية/تاريخية وليست ضمانًا.
    """
    empty = {
        "score": 50.0, "best_horizon": 10, "horizon_name": "10 جلسات (~أسبوعين)",
        "prob_up": 50.0, "expected": 0.0, "median": 0.0,
        "range_low": np.nan, "range_high": np.nan, "samples": 0,
        "target_price": ind.get("price", np.nan),
        "range_price_low": np.nan, "range_price_high": np.nan,
        "label": "مراقبة", "action": "مراقبة",
        "all": {}
    }
    try:
        d = df.dropna().copy()
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(-1)
        if len(d) < 150:
            return empty

        c = pd.to_numeric(d["Close"], errors="coerce")
        v = pd.to_numeric(d["Volume"], errors="coerce").fillna(0)
        h = pd.to_numeric(d["High"], errors="coerce")
        l = pd.to_numeric(d["Low"], errors="coerce")

        e20 = c.ewm(span=20, adjust=False).mean()
        e50 = c.ewm(span=50, adjust=False).mean()
        rrsi = rsi(c)
        mom20 = c.pct_change(20) * 100
        vol20 = v.rolling(20).mean()
        vr = (v / vol20.replace(0, np.nan)).clip(lower=0.05, upper=10)

        prev = c.shift(1)
        tr = pd.concat([(h-l).abs(), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
        aa = tr.rolling(14).mean().replace(0, np.nan)
        dist20 = ((c - e20) / aa).clip(-6, 6)

        cur_rsi = float(ind.get("rsi", 50.0))
        cur_mom = float(ind.get("mom20", 0.0))
        cur_vr = max(0.05, float(ind.get("vol_ratio", 1.0)))
        cur_dist = (float(ind.get("price", 0.0)) - float(ind.get("ema20", 0.0))) / max(float(ind.get("atr", 1.0)), 1e-9)
        cur_t1 = bool(ind.get("price", 0) > ind.get("ema20", 0))
        cur_t2 = bool(ind.get("ema20", 0) > ind.get("ema50", 0))

        max_h = 60
        candidates = []
        # لا نستخدم شروط Buy صارمة؛ فقط نستبعد الأطراف شديدة الشذوذ.
        for i in range(60, len(c) - max_h):
            if not all(pd.notna(x) for x in [c.iloc[i], e20.iloc[i], e50.iloc[i], rrsi.iloc[i], mom20.iloc[i], vr.iloc[i], dist20.iloc[i]]):
                continue
            if rrsi.iloc[i] < 22 or rrsi.iloc[i] > 88:
                continue

            t1 = bool(c.iloc[i] > e20.iloc[i])
            t2 = bool(e20.iloc[i] > e50.iloc[i])

            # Similarity 0..100. Trend only part of the score; it is not a hard gate.
            sim = 0.0
            sim += 16.0 if t1 == cur_t1 else 6.0
            sim += 14.0 if t2 == cur_t2 else 5.0
            sim += 24.0 * math.exp(-abs(float(rrsi.iloc[i]) - cur_rsi) / 15.0)
            sim += 22.0 * math.exp(-abs(float(mom20.iloc[i]) - cur_mom) / 11.0)
            sim += 12.0 * math.exp(-abs(float(dist20.iloc[i]) - cur_dist) / 1.8)
            try:
                sim += 12.0 * math.exp(-abs(math.log(max(float(vr.iloc[i]),0.05) / cur_vr)) / 0.85)
            except Exception:
                sim += 6.0
            candidates.append((sim, i))

        if len(candidates) < 8:
            return empty

        # Top similar setups, de-clustered so one rally does not dominate.
        candidates.sort(reverse=True, key=lambda x: x[0])
        chosen = []
        for sim, i in candidates:
            if all(abs(i-j) >= 4 for _, j in chosen):
                chosen.append((sim, i))
            if len(chosen) >= 36:
                break

        horizons = [5, 10, 20, 60]
        names = {
            5: "5 جلسات (~أسبوع)",
            10: "10 جلسات (~أسبوعين)",
            20: "20 جلسة (~شهر)",
            60: "60 جلسة (~3 أشهر)"
        }
        all_stats = {}

        for hz in horizons:
            vals = []
            weights = []
            for sim, i in chosen:
                if i + hz >= len(c) or not pd.notna(c.iloc[i+hz]) or c.iloc[i] == 0:
                    continue
                vals.append(float((c.iloc[i+hz] / c.iloc[i] - 1.0) * 100.0))
                weights.append(max(sim, 1.0))

            if len(vals) < 5:
                all_stats[hz] = {
                    "samples": len(vals), "prob_up": 50.0, "expected": 0.0, "median": 0.0,
                    "low": np.nan, "high": np.nan, "quality": 50.0
                }
                continue

            arr = np.array(vals, dtype=float)
            w = np.array(weights, dtype=float)
            w = w / w.sum()

            prob_up = float((w * (arr > 0).astype(float)).sum() * 100.0)
            expected = float((w * arr).sum())
            median = float(np.median(arr))
            low = float(np.percentile(arr, 25))
            high = float(np.percentile(arr, 75))

            # quality 0..100. Sample count only softens confidence, never blocks.
            quality = 0.62 * prob_up + 0.38 * float(np.clip(50 + expected * 5.0, 0, 100))
            sample_factor = min(1.0, len(arr) / 16.0)
            quality = 50.0 + (quality - 50.0) * (0.55 + 0.45 * sample_factor)

            all_stats[hz] = {
                "samples": len(arr), "prob_up": prob_up, "expected": expected, "median": median,
                "low": low, "high": high, "quality": float(np.clip(quality, 0, 100))
            }

        # Prefer a horizon that has positive expected return, but do not require it.
        def utility(item):
            hz, s = item
            pos_bonus = 5.0 if s["expected"] > 0 else 0.0
            # Mild preference for 10/20 days so very long horizon does not always win.
            horizon_bonus = {5:0.0, 10:1.5, 20:1.0, 60:0.0}[hz]
            return s["quality"] + pos_bonus + horizon_bonus

        best_hz, best = max(all_stats.items(), key=utility)
        p = float(ind.get("price", np.nan))
        target_ret = max(0.0, best["median"])
        target_price = p * (1.0 + target_ret/100.0) if pd.notna(p) else np.nan
        range_price_low = p * (1.0 + best["low"]/100.0) if pd.notna(p) and pd.notna(best["low"]) else np.nan
        range_price_high = p * (1.0 + best["high"]/100.0) if pd.notna(p) and pd.notna(best["high"]) else np.nan

        prob = best["prob_up"]
        exp = best["expected"]
        if prob >= 67 and exp >= 2.5:
            action = "مرشح شراء"
        elif prob >= 59 and exp >= 0.8:
            action = "فرصة مبكرة"
        elif prob >= 53 and exp > -0.3:
            action = "مراقبة إيجابية"
        else:
            action = "مراقبة"

        if best_hz <= 5:
            label_txt = "قصير جدًا"
        elif best_hz <= 10:
            label_txt = "قصير"
        elif best_hz <= 20:
            label_txt = "متوسط"
        else:
            label_txt = "استثمار 1–3 أشهر"

        return {
            "score": float(best["quality"]),
            "best_horizon": int(best_hz),
            "horizon_name": names[best_hz],
            "prob_up": float(prob),
            "expected": float(exp),
            "median": float(best["median"]),
            "range_low": float(best["low"]) if pd.notna(best["low"]) else np.nan,
            "range_high": float(best["high"]) if pd.notna(best["high"]) else np.nan,
            "samples": int(best["samples"]),
            "target_price": float(target_price) if pd.notna(target_price) else np.nan,
            "range_price_low": float(range_price_low) if pd.notna(range_price_low) else np.nan,
            "range_price_high": float(range_price_high) if pd.notna(range_price_high) else np.nan,
            "label": label_txt,
            "action": action,
            "all": all_stats
        }
    except Exception:
        return empty


def forecast_action(score, forecast_prob, forecast_expected, horizon):
    """
    قرار خفيف: لا يشترط اكتمال كل التحاليل.
    """
    if forecast_prob >= 67 and forecast_expected >= 2.0 and score >= 54:
        return "مرشح شراء"
    if forecast_prob >= 59 and forecast_expected >= 0.5 and score >= 46:
        return "فرصة مبكرة"
    if score >= 52 or (forecast_prob >= 54 and forecast_expected >= 0):
        return "مراقبة إيجابية"
    return "مراقبة"


def professional_backtest(df):
    """
    اختبار تاريخي لثلاث مدد 5/10/20 جلسة.
    يعيد: score 0..10 + win rates + avg returns + profit factor + max drawdown + samples.
    """
    try:
        d = df.dropna().copy()
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(-1)
        if len(d) < 150:
            return {
                "score": 5.0, "samples": 0,
                "win5": np.nan, "win10": np.nan, "win20": np.nan,
                "avg5": np.nan, "avg10": np.nan, "avg20": np.nan,
                "profit_factor": np.nan, "max_drawdown": np.nan
            }

        c = pd.to_numeric(d["Close"], errors="coerce")
        v = pd.to_numeric(d["Volume"], errors="coerce").fillna(0)
        e20 = c.ewm(span=20, adjust=False).mean()
        e50 = c.ewm(span=50, adjust=False).mean()
        macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        ms = macd.ewm(span=9, adjust=False).mean()
        rrsi = rsi(c)
        mom20 = c.pct_change(20) * 100
        vol20 = v.rolling(20).mean()
        vr = v / vol20.replace(0, np.nan)

        sig = (c > e20) & (e20 > e50) & (macd > ms) & rrsi.between(44, 73) & (mom20 > 0) & (vr >= 0.7)
        idxs = np.where(sig.fillna(False).values)[0]

        # De-cluster signals: one sample every >=5 bars.
        selected = []
        last_i = -999
        for i in idxs:
            if i - last_i >= 5:
                selected.append(i)
                last_i = i

        rets = {5: [], 10: [], 20: []}
        dd_samples = []
        for i in selected:
            if i + 20 >= len(c) or not pd.notna(c.iloc[i]) or c.iloc[i] == 0:
                continue
            entry = float(c.iloc[i])
            for h in (5,10,20):
                if pd.notna(c.iloc[i+h]):
                    rets[h].append(float(c.iloc[i+h] / entry - 1.0))
            path = c.iloc[i:i+21].astype(float) / entry - 1.0
            if len(path):
                dd_samples.append(float(path.min()))

        n = min(len(rets[5]), len(rets[10]), len(rets[20]))
        if n < 5:
            return {
                "score": 5.0, "samples": n,
                "win5": np.nan, "win10": np.nan, "win20": np.nan,
                "avg5": np.nan, "avg10": np.nan, "avg20": np.nan,
                "profit_factor": np.nan, "max_drawdown": np.nan
            }

        def wr(xs): return float(np.mean([x > 0 for x in xs]) * 100)
        def av(xs): return float(np.mean(xs) * 100)

        wins = [x for x in rets[10] if x > 0]
        losses = [-x for x in rets[10] if x < 0]
        pf = (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else (3.0 if wins else np.nan)
        mdd = float(min(dd_samples) * 100) if dd_samples else np.nan

        win10 = wr(rets[10])
        avg10 = av(rets[10])
        score = 5.0
        score += np.clip((win10 - 50.0) / 10.0, -2.0, 2.0)
        score += np.clip(avg10 / 2.0, -1.5, 1.5)
        if pd.notna(pf):
            if pf >= 1.8: score += 1.0
            elif pf >= 1.3: score += 0.5
            elif pf < 0.8: score -= 0.8
        if pd.notna(mdd):
            if mdd < -12: score -= 0.8
            elif mdd > -5: score += 0.3

        return {
            "score": float(np.clip(score, 0, 10)),
            "samples": n,
            "win5": wr(rets[5]), "win10": win10, "win20": wr(rets[20]),
            "avg5": av(rets[5]), "avg10": avg10, "avg20": av(rets[20]),
            "profit_factor": float(pf) if pd.notna(pf) else np.nan,
            "max_drawdown": mdd
        }
    except Exception:
        return {
            "score": 5.0, "samples": 0,
            "win5": np.nan, "win10": np.nan, "win20": np.nan,
            "avg5": np.nan, "avg10": np.nan, "avg20": np.nan,
            "profit_factor": np.nan, "max_drawdown": np.nan
        }


def analyst_consensus_label(info):
    key = str(info.get("recommendationKey", "") or "").lower().strip()
    mapping = {
        "strong_buy": "شراء قوي",
        "strongbuy": "شراء قوي",
        "buy": "شراء",
        "outperform": "تفوق / شراء",
        "overweight": "زيادة وزن",
        "hold": "احتفاظ",
        "neutral": "محايد",
        "underperform": "أداء أقل",
        "underweight": "خفض وزن",
        "sell": "بيع",
        "strong_sell": "بيع قوي",
        "strongsell": "بيع قوي",
    }
    if key in mapping:
        return mapping[key]

    sb = int(info.get("analystStrongBuy", 0) or 0)
    b = int(info.get("analystBuy", 0) or 0)
    h = int(info.get("analystHold", 0) or 0)
    s = int(info.get("analystSell", 0) or 0)
    ss = int(info.get("analystStrongSell", 0) or 0)
    total = sb + b + h + s + ss
    if total <= 0:
        return "لا توجد تغطية كافية"

    weighted = (2*sb + b - s - 2*ss) / max(total, 1)
    if weighted >= 1.1: return "شراء قوي"
    if weighted >= 0.35: return "شراء"
    if weighted > -0.35: return "احتفاظ"
    if weighted > -1.1: return "بيع"
    return "بيع قوي"


def analyst_score(info, current_price):
    """
    0..10، 5 = محايد/لا توجد بيانات.
    يستخدم إجماع المحللين + السعر المستهدف، لكن تأثيره النهائي محدود +/-5 نقاط.
    """
    if not info:
        return 5.0, [], {
            "label": "لا توجد تغطية كافية", "count": 0,
            "buy": 0, "hold": 0, "sell": 0,
            "target_mean": np.nan, "target_high": np.nan, "target_low": np.nan,
            "upside_pct": np.nan
        }

    sb = int(info.get("analystStrongBuy", 0) or 0)
    b = int(info.get("analystBuy", 0) or 0)
    h = int(info.get("analystHold", 0) or 0)
    s = int(info.get("analystSell", 0) or 0)
    ss = int(info.get("analystStrongSell", 0) or 0)
    detailed_total = sb + b + h + s + ss
    stated_count = int(max(0, safe_float(info.get("numberOfAnalystOpinions"), 0.0)))
    count = max(detailed_total, stated_count)

    target_mean = safe_float(info.get("targetMeanPrice"))
    target_high = safe_float(info.get("targetHighPrice"))
    target_low = safe_float(info.get("targetLowPrice"))

    has_target = pd.notna(target_mean) and current_price and current_price > 0
    has_consensus = detailed_total > 0 or bool(str(info.get("recommendationKey", "") or "").strip()) \
                    or pd.notna(safe_float(info.get("recommendationMean")))

    if not has_target and not has_consensus:
        return 5.0, [], {
            "label": "لا توجد تغطية كافية", "count": count,
            "buy": sb + b, "hold": h, "sell": s + ss,
            "target_mean": target_mean, "target_high": target_high, "target_low": target_low,
            "upside_pct": np.nan
        }

    score = 5.0
    reasons = []

    # 1) إجماع Buy/Hold/Sell
    if detailed_total > 0:
        consensus_raw = (2*sb + b - s - 2*ss) / max(detailed_total, 1)  # -2..+2
        score += np.clip(consensus_raw * 1.25, -2.5, 2.5)
        buy_pct = (sb + b) / detailed_total * 100
        sell_pct = (s + ss) / detailed_total * 100
        if buy_pct >= 65:
            reasons.append(f"{buy_pct:.0f}% من التوصيات شراء")
        elif sell_pct >= 45:
            reasons.append(f"{sell_pct:.0f}% من التوصيات بيع")
    else:
        key = str(info.get("recommendationKey", "") or "").lower().strip()
        key_adj = {
            "strong_buy": 2.3, "strongbuy": 2.3, "buy": 1.6,
            "outperform": 1.3, "overweight": 1.0,
            "hold": 0.0, "neutral": 0.0,
            "underperform": -1.0, "underweight": -1.0,
            "sell": -1.7, "strong_sell": -2.3, "strongsell": -2.3
        }
        score += key_adj.get(key, 0.0)

    # 2) السعر المستهدف
    upside = np.nan
    if has_target:
        upside = (target_mean / current_price - 1.0) * 100.0
        target_adj = np.clip(upside / 12.0, -2.5, 2.5)
        score += target_adj
        if upside >= 12:
            reasons.append(f"السعر المستهدف أعلى بنحو {upside:.1f}%")
        elif upside <= -8:
            reasons.append(f"السعر المستهدف أقل بنحو {abs(upside):.1f}%")

    # لا نرفع الثقة كثيرًا إذا كانت التغطية من محلل أو اثنين فقط.
    if count == 1:
        score = 5.0 + (score - 5.0) * 0.55
        reasons.append("تغطية محلل واحد فقط")
    elif count == 2:
        score = 5.0 + (score - 5.0) * 0.75

    meta = {
        "label": analyst_consensus_label(info),
        "count": count,
        "buy": sb + b,
        "hold": h,
        "sell": s + ss,
        "strong_buy": sb,
        "strong_sell": ss,
        "target_mean": target_mean,
        "target_high": target_high,
        "target_low": target_low,
        "upside_pct": upside,
    }
    return float(np.clip(score, 0, 10)), reasons, meta


def trade_levels(ind):
    p = ind["price"]; a = ind["atr"]
    if np.isnan(a) or a <= 0:
        a = max(p * 0.02, 0.001)

    support = max(ind["support20"], ind["ema50"] if ind["ema50"] < p else ind["support20"])
    resistance_near = max(ind["res20"], p)
    resistance_major = max(ind["res60"], resistance_near)

    entry_low = max(support, ind["ema20"] - 0.25 * a)
    entry_high = min(max(p, entry_low), ind["ema20"] + 0.45 * a)
    if entry_high < entry_low:
        entry_high = p

    stop = max(0.001, min(support - 0.45 * a, p - 1.30 * a))
    target1 = max(resistance_major, p + 1.65 * a)
    target2 = max(target1 + 0.85 * a, p + 2.60 * a)

    risk = max(p - stop, 1e-9)
    reward1 = max(target1 - p, 0.0)
    rr = reward1 / risk
    risk_pct = (risk / p) * 100 if p else np.nan
    resistance_distance_atr = max(resistance_near - p, 0.0) / a if a else 0.0
    return entry_low, entry_high, stop, target1, target2, risk_pct, rr, resistance_distance_atr


def setup_score(ind, rr, resistance_distance_atr):
    """0..10 — جودة مكان الدخول وليس اتجاه السوق فقط."""
    s = 5.0
    reasons = []

    if rr >= 2.3:
        s += 2.5; reasons.append("عائد/مخاطرة ممتاز")
    elif rr >= 1.8:
        s += 1.7; reasons.append("عائد/مخاطرة جيد")
    elif rr >= 1.35:
        s += 0.7
    elif rr < 1.0:
        s -= 2.5; reasons.append("عائد/مخاطرة ضعيف")

    if resistance_distance_atr < 0.35:
        s -= 1.8; reasons.append("مقاومة قريبة جدًا")
    elif resistance_distance_atr > 1.0:
        s += 0.8

    p = ind["price"]
    a = max(ind["atr"], 1e-9)
    dist_ema20 = abs(p - ind["ema20"]) / a
    if dist_ema20 <= 0.7:
        s += 0.8; reasons.append("السعر قريب من منطقة دخول منطقية")
    elif dist_ema20 >= 2.0:
        s -= 1.0; reasons.append("السعر ممتد بعيدًا عن المتوسط")

    return float(np.clip(s, 0, 10)), reasons


def market_breadth(indicators_by_ticker):
    vals = list(indicators_by_ticker.values())
    if not vals:
        return {"score": 2.5, "above20": 50.0, "above50": 50.0, "positive20": 50.0,
                "median20": 0.0, "median60": 0.0, "label": "محايد"}
    above20 = np.mean([x["price"] > x["ema20"] for x in vals]) * 100
    above50 = np.mean([x["price"] > x["ema50"] for x in vals]) * 100
    positive20 = np.mean([x["mom20"] > 0 for x in vals]) * 100
    med20 = float(np.nanmedian([x["mom20"] for x in vals]))
    med60 = float(np.nanmedian([x["mom60"] for x in vals]))
    composite = 0.45 * above20 + 0.35 * above50 + 0.20 * positive20
    if composite >= 68:
        score, lab = 5.0, "قوي"
    elif composite >= 58:
        score, lab = 4.2, "إيجابي"
    elif composite >= 47:
        score, lab = 2.8, "محايد"
    elif composite >= 38:
        score, lab = 1.5, "ضعيف"
    else:
        score, lab = 0.5, "ضعيف جدًا"
    return {"score": score, "above20": above20, "above50": above50, "positive20": positive20,
            "median20": med20, "median60": med60, "label": lab}


def relative_strength_score(ind, market):
    """0..10 — أداء السهم مقارنة بوسيط السوق."""
    d20 = ind["mom20"] - market["median20"]
    d60 = ind["mom60"] - market["median60"]
    s = 5.0 + np.clip(d20 / 4.0, -2.0, 2.0) + np.clip(d60 / 8.0, -2.0, 2.0)
    reasons = []
    if d20 > 3 and d60 > 5:
        reasons.append("يتفوق على السوق في 20 و60 يوم")
    elif d20 > 2:
        reasons.append("قوة نسبية أعلى من السوق")
    elif d20 < -3 and d60 < -5:
        reasons.append("أضعف من السوق نسبيًا")
    return float(np.clip(s, 0, 10)), reasons


def quick_backtest(df):
    """
    اختبار walk-forward بسيط بلا تسريب للمستقبل:
    يبحث تاريخيًا عن حالات اتجاه/زخم مشابهة ويقيس عائد 10 جلسات التالية.
    """
    try:
        d = df.dropna().copy()
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(-1)
        if len(d) < 120:
            return 5.0, np.nan, np.nan, 0

        c = pd.to_numeric(d["Close"], errors="coerce")
        v = pd.to_numeric(d["Volume"], errors="coerce").fillna(0)
        e20 = c.ewm(span=20, adjust=False).mean()
        e50 = c.ewm(span=50, adjust=False).mean()
        macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
        ms = macd.ewm(span=9, adjust=False).mean()
        rrsi = rsi(c)
        mom20 = c.pct_change(20) * 100
        vol20 = v.rolling(20).mean()
        vr = v / vol20.replace(0, np.nan)
        fwd = c.shift(-10) / c - 1

        sig = (c > e20) & (e20 > e50) & (macd > ms) & (rrsi.between(44, 73)) & (mom20 > 0)
        # أخذ عينات متباعدة لتقليل تكرار نفس الحركة
        idxs = np.where(sig.fillna(False).values)[0]
        idxs = [i for j, i in enumerate(idxs) if j == 0 or i - idxs[j-1] >= 4]
        rets = [float(fwd.iloc[i]) for i in idxs if i < len(fwd)-10 and pd.notna(fwd.iloc[i])]
        if len(rets) < 5:
            return 5.0, np.nan, np.nan, len(rets)

        win = float(np.mean([r > 0 for r in rets]))
        avg = float(np.mean(rets))
        # 50% فوز و0% متوسط = 5/10
        score = 5.0 + (win - 0.50) * 8.0 + np.clip(avg * 35.0, -2.0, 2.0)
        return float(np.clip(score, 0, 10)), win * 100.0, avg * 100.0, len(rets)
    except Exception:
        return 5.0, np.nan, np.nan, 0


def confidence_score(ind, fi, official_items, web_items, component_scores, bt_samples):
    """ثقة البيانات واتفاق العوامل — ليست احتمال ربح."""
    score = 0.0

    # جودة البيانات السعرية
    hist_len = ind.get("history_len", 0)
    score += 15 if hist_len >= 220 else (11 if hist_len >= 140 else 7)

    # اكتمال الأساسيات
    if fi:
        fields = ["trailingPE","priceToBook","returnOnEquity","revenueGrowth","earningsGrowth","dividendYield"]
        present = sum(1 for k in fields if pd.notna(fi.get(k, np.nan)))
        score += min(20, present / len(fields) * 20)

    # الأخبار والإفصاحات
    score += min(10, len(official_items) * 2.0)
    score += min(8, len(web_items) * 1.3)

    # تغطية المحللين - نقاط ثقة إضافية فقط عند وجود أكثر من رأي
    analyst_count = int(max(0, safe_float(fi.get("numberOfAnalystOpinions"), 0.0))) if fi else 0
    score += min(8, analyst_count * 1.5)

    # الاختبار التاريخي
    score += min(15, bt_samples * 1.0)

    # اتفاق العوامل
    thresholds = {
        "Technical": 12.5, "OfficialNews": 7.5, "WebNews": 5.0,
        "Fundamental": 7.5, "Liquidity10": 5.0, "RelativeStrength": 5.0,
        "Setup": 5.0, "MarketScore": 2.5, "Backtest": 5.0, "Analyst": 5.0, "Quarterly": 5.0, "Sector": 5.0
    }
    positives = 0
    negatives = 0
    for k, th in thresholds.items():
        val = component_scores.get(k, th)
        if val > th + 0.35:
            positives += 1
        elif val < th - 0.35:
            negatives += 1
    agreement = max(0, positives - negatives)
    score += min(30, agreement / len(thresholds) * 45)

    return float(np.clip(score, 0, 100))


def label(score):
    # حدود أخف في V8 حتى لا تختفي الفرص المبكرة.
    if score >= 80: return "شراء قوي"
    if score >= 72: return "فرصة ممتازة"
    if score >= 64: return "فرصة جيدة جداً"
    if score >= 56: return "فرصة محتملة"
    if score >= 48: return "مراقبة إيجابية"
    return "مراقبة"


def final_decision(score, forecast_prob, forecast_expected, technical_score_value):
    """قرار V8 المبسط: شراء / مراقبة / بيع.
    يعتمد على الدرجة العامة + التوقع التاريخي + التحليل الفني.
    """
    try:
        score = float(score)
        prob = float(forecast_prob)
        exp = float(forecast_expected)
        tech = float(technical_score_value)
    except Exception:
        return "مراقبة"

    if score >= 64 and prob >= 55 and exp > 0 and tech >= 11:
        return "شراء"
    if score <= 42 and prob <= 45 and exp < 0 and tech <= 11:
        return "بيع"
    return "مراقبة"


def suggested_action(decision, entry_low=np.nan, entry_high=np.nan, stop=np.nan):
    if decision == "شراء":
        if pd.notna(entry_low) and pd.notna(entry_high):
            return f"دخول تدريجي بين {entry_low:.3f} و {entry_high:.3f} مع الالتزام بالوقف"
        return "دخول تدريجي بعد تأكيد السعر مع الالتزام بالوقف"
    if decision == "بيع":
        return "تجنب شراء جديد؛ للمراقبة فقط حتى تتحسن الإشارة"
    return "انتظار تأكيد أقوى قبل الدخول"


def fmt_num(x, dec=3):
    try:
        if x is None or np.isnan(float(x)): return "-"
        return f"{float(x):,.{dec}f}"
    except Exception:
        return "-"


def fmt_pct(x, multiply=False):
    try:
        if x is None or np.isnan(float(x)): return "-"
        v = float(x) * (100 if multiply else 1)
        return f"{v:.2f}%"
    except Exception:
        return "-"


st.title("📊 محلل بورصة الكويت الذكي")
st.caption("V8 Forecast PRO — فرص أكثر + توقع 5/10/20/60 جلسة + مدة متوقعة + نطاق سعري + 139 شركة")
st.caption("يحمّل جميع أسهم بورصة الكويت تلقائيًا، ويستخدم قائمة رسمية مدمجة من 139 شركة عند تعذر التقرير المباشر.")

with st.expander("⚙️ إعدادات الفحص", expanded=False):
    market_filter = st.selectbox("السوق", ["الكل", "Premier", "Main"], index=0)
    top_n = st.slider("عدد أفضل الفرص", 5, 30, 10, 1)
    min_score = st.slider("أقل درجة للعرض", 0, 100, 48, 1)
    web_news_mode = st.selectbox(
        "الأخبار العامة",
        ["لأفضل 30 مرشح", "السوق كامل", "إيقاف الأخبار العامة"],
        index=1
    )
    deep_count = st.slider("التحليل العميق الإضافي لأفضل عدد من الأسهم", 20, 70, 40, 5)
    portfolio_capital = st.number_input("رأس مال المحفظة الاختياري (د.ك)", min_value=0.0, value=10000.0, step=500.0)
    portfolio_count = st.slider("عدد أسهم المحفظة المقترحة", 3, 8, 5, 1)
    st.caption("الأسعار من Yahoo Finance وقد تكون مؤخرة. الإفصاحات من RSS الرسمي لبورصة الكويت.")
    run = st.button("🔄 تحليل السوق الآن", type="primary", use_container_width=True)

if "scan_result" not in st.session_state:
    st.session_state.scan_result = None
    st.session_state.details = {}

# عند الانتقال إلى V8 لا نستخدم DataFrame قديم لا يحتوي حقول Forecast.
if st.session_state.scan_result is not None:
    try:
        if "ForecastProb" not in st.session_state.scan_result.columns:
            st.session_state.scan_result = None
            st.session_state.details = {}
    except Exception:
        st.session_state.scan_result = None
        st.session_state.details = {}

HISTORY_FILE = Path("recommendation_history_v6.csv")

def load_local_history():
    try:
        if HISTORY_FILE.exists():
            return pd.read_csv(HISTORY_FILE)
    except Exception:
        pass
    return pd.DataFrame()

def append_local_history(rows):
    """
    يحفظ على قرص Streamlit المحلي عند الإمكان.
    Community Cloud قد يعيد تهيئة القرص بعد reboot/deploy؛ لذلك يوجد تنزيل CSV احتياطي.
    """
    try:
        old = load_local_history()
        new = pd.DataFrame(rows)
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.tail(3000)
        merged.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
        return True
    except Exception:
        return False

if "recommendation_history" not in st.session_state:
    st.session_state.recommendation_history = []
if "previous_scores" not in st.session_state:
    st.session_state.previous_scores = {}

if run or st.session_state.scan_result is None:
    with st.status("جاري قراءة السوق والإفصاحات وتحليل الأسهم...", expanded=True) as status:
        universe, report_url, fallback_used = load_universe()
        st.write(f"الشركات التي تم تحميلها: {len(universe)}")
        if market_filter != "الكل":
            universe = universe[universe["Market"].str.lower() == market_filter.lower()].copy()
        universe = universe.reset_index(drop=True)
        rss_items = load_boursa_rss()
        st.write(f"الإفصاحات الرسمية المقروءة: {len(rss_items)}")
        price_map = download_prices(universe["Ticker"].tolist())
        st.write(f"أسهم لديها بيانات سعرية: {len(price_map)} من {len(universe)}")

        if fallback_used:
            # عند استخدام القائمة المدمجة لا توجد قيمة Free Float محفوظة؛
            # لذلك نحسب السيولة من متوسط قيمة التداول اليومية عندما تتوفر الأسعار.
            daily_values = {}
            for _t, _d in price_map.items():
                try:
                    if isinstance(_d.columns, pd.MultiIndex):
                        _d = _d.copy()
                        _d.columns = _d.columns.get_level_values(-1)
                    _c = pd.to_numeric(_d["Close"], errors="coerce").dropna()
                    _v = pd.to_numeric(_d["Volume"], errors="coerce").dropna()
                    if len(_c) >= 20 and len(_v) >= 20:
                        daily_values[_t] = float(_c.tail(20).mean() * _v.tail(20).mean())
                except Exception:
                    pass
            if daily_values:
                _liq_s = pd.Series(daily_values, dtype=float)
                _liq_rank = (_liq_s.rank(pct=True) * 15).clip(0, 15)
                universe["LiquidityScore"] = universe["Ticker"].map(_liq_rank).fillna(7.5)
            else:
                universe["LiquidityScore"] = 7.5
        else:
            universe["LiquidityScore"] = liquidity_scores(universe)

        rows = []
        details = {}
        indicators_by_ticker = {}

        # المرحلة 1: تحليل كل الأسهم سعريًا + الإفصاحات الرسمية + السيولة
        scan_progress = st.progress(0.0)
        scan_note = st.empty()
        total_scan = max(1, len(universe))
        for scan_pos, (i, r) in enumerate(universe.iterrows(), start=1):
            scan_note.caption(f"تحليل السهم {scan_pos} من {len(universe)}")
            tick = r["Ticker"]
            scan_progress.progress(min(scan_pos / total_scan, 1.0))
            time.sleep(0.01)
            d = price_map.get(tick)
            if d is None or d.empty:
                continue
            if isinstance(d.columns, pd.MultiIndex):
                try:
                    d = d.copy()
                    d.columns = d.columns.get_level_values(-1)
                except Exception:
                    continue
            needed = {"Open","High","Low","Close","Volume"}
            if not needed.issubset(set(map(str, d.columns))):
                continue

            ind = compute_indicators(d)
            if not ind:
                continue
            indicators_by_ticker[tick] = ind

        scan_progress.empty()
        scan_note.empty()
        market = market_breadth(indicators_by_ticker)

        for i, r in universe.iterrows():
            tick = r["Ticker"]
            ind = indicators_by_ticker.get(tick)
            d = price_map.get(tick)
            if ind is None or d is None:
                continue

            ts, treasons = technical_score(ind)
            off = match_official_news(tick, f"{r.get('NameAR', '')} {r['Name']}", rss_items)
            ons, oreasons, ocats = official_news_analysis(off)
            liq10 = float(r["LiquidityScore"]) * (10.0 / 15.0)
            rs_score, rs_reasons = relative_strength_score(ind, market)
            e1,e2,sl,t1,t2,risk,rr_value,res_atr = trade_levels(ind)
            setup, setup_reasons = setup_score(ind, rr_value, res_atr)

            # Forecast خفيف لكل سهم: لا يحتاج بيانات مالية أو محللين.
            fc = forecast_horizons(d, ind)

            # درجات محايدة مؤقتة إلى أن يكتمل التحليل العميق
            web_neutral = 5.0
            fund_neutral = 7.5
            backtest_neutral = 5.0

            base = (
                ts + ons + web_neutral + fund_neutral + liq10 +
                rs_score + setup + float(market["score"])
            )
            initial_decision = final_decision(base, fc["prob_up"], fc["expected"], ts)
            rows.append({
                "Ticker": tick, "NameAR": r.get("NameAR", r["Name"]), "Name": r["Name"], "Market": r["Market"],
                "Price": ind["price"],
                "Technical": ts, "OfficialNews": ons, "WebNews": web_neutral,
                "Fundamental": fund_neutral, "Liquidity10": liq10,
                "RelativeStrength": rs_score, "Setup": setup, "MarketScore": float(market["score"]),
                "Backtest": backtest_neutral,
                "BacktestWin5": np.nan, "BacktestWin10": np.nan, "BacktestWin20": np.nan,
                "BacktestAvg5": np.nan, "BacktestAvg10": np.nan, "BacktestAvg20": np.nan,
                "BacktestPF": np.nan, "BacktestMDD": np.nan,
                "Quarterly": 5.0, "QuarterlyPoints": 0, "SectorScore": 5.0, "SectorGroup": "عام",
                "ForecastScore": fc["score"], "ForecastHorizon": fc["best_horizon"],
                "ForecastPeriod": fc["horizon_name"], "ForecastProb": fc["prob_up"],
                "ForecastExpected": fc["expected"], "ForecastMedian": fc["median"],
                "ForecastRangeLow": fc["range_low"], "ForecastRangeHigh": fc["range_high"],
                "ForecastTarget": fc["target_price"], "ForecastPriceLow": fc["range_price_low"],
                "ForecastPriceHigh": fc["range_price_high"], "ForecastSamples": fc["samples"],
                "ForecastStyle": fc["label"], "ForecastAction": fc["action"],
                "FinalDecision": initial_decision,
                "SuggestedAction": suggested_action(initial_decision, e1, e2, sl),
                "Analyst": 5.0,
                "AnalystLabel": "لا توجد تغطية كافية", "AnalystCount": 0,
                "AnalystBuy": 0, "AnalystHold": 0, "AnalystSell": 0,
                "TargetMean": np.nan, "TargetHigh": np.nan, "TargetLow": np.nan, "TargetUpside": np.nan,
                "Score": float(np.clip(base, 0, 100)), "Confidence": 0.0, "Signal": label(base),
                "RSI": ind["rsi"], "Momentum20": ind["mom20"], "Momentum60": ind["mom60"],
                "EntryLow": e1, "EntryHigh": e2, "Stop": sl,
                "Target1": t1, "Target2": t2, "RiskPct": risk, "RR": rr_value,
                "OfficialCount": len(off), "WebCount": 0,
                "BacktestWinRate": np.nan, "BacktestAvgReturn": np.nan, "BacktestSamples": 0,
                "DeltaScore": 0.0
            })
            details[tick] = {
                "ind": ind, "price_df": d, "official": off, "web": [],
                "tech_reasons": treasons, "official_reasons": oreasons,
                "relative_reasons": rs_reasons, "setup_reasons": setup_reasons,
                "fund_reasons": [], "web_reasons": [], "analyst_reasons": [],
                "analyst_meta": {}, "fund_info": {},
                "forecast": fc,
                "market": market, "official_categories": ocats
            }

        result = pd.DataFrame(rows)
        if result.empty:
            st.error("لم أتمكن من تكوين نتائج. تحقق من اتصال الإنترنت ثم أعد المحاولة.")
            st.stop()

        # المرحلة 2: الأخبار العامة للأسهم المطلوبة
        pre = result.sort_values("Score", ascending=False)
        if web_news_mode == "السوق كامل":
            news_targets = pre["Ticker"].tolist()
        elif web_news_mode == "لأفضل 30 مرشح":
            news_targets = pre.head(30)["Ticker"].tolist()
        else:
            news_targets = []

        # التحليل العميق: أفضل N مرشح مبدئيًا
        deep_targets = pre.head(min(deep_count, len(pre)))["Ticker"].tolist()
        fund_targets = deep_targets
        bt_targets = pre.head(min(30, len(pre)))["Ticker"].tolist()

        name_map = {r["Ticker"]: f"{r.get('NameAR', '')} {r['Name']}" for _, r in result.iterrows()}

        web_results = {}
        if news_targets:
            with ThreadPoolExecutor(max_workers=5) as ex:
                futs = {ex.submit(google_news, name_map[t], t): t for t in news_targets}
                for fut in as_completed(futs):
                    t = futs[fut]
                    try:
                        web_results[t] = fut.result()
                    except Exception:
                        web_results[t] = []

        fund_results = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(fundamental_info, t): t for t in fund_targets}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    fund_results[t] = fut.result()
                except Exception:
                    fund_results[t] = {}

        bt_results = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(professional_backtest, details[t]["price_df"]): t for t in bt_targets}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    bt_results[t] = fut.result()
                except Exception:
                    bt_results[t] = {"score":5.0,"samples":0,"win5":np.nan,"win10":np.nan,"win20":np.nan,"avg5":np.nan,"avg10":np.nan,"avg20":np.nan,"profit_factor":np.nan,"max_drawdown":np.nan}

        # المرحلة 3: الدرجة النهائية 100 + تعديل تاريخي محدود +/-5
        for idx, row in result.iterrows():
            t = row["Ticker"]
            web_items = web_results.get(t, [])
            ws, wreasons = web_news_analysis(web_items) if t in news_targets else (5.0, [])

            fi = fund_results.get(t, {})
            fs, freasons = fundamental_score(fi) if t in fund_targets else (7.5, [])

            analyst_s, analyst_reasons, analyst_meta = analyst_score(fi, float(row["Price"])) if t in fund_targets else (
                5.0, [], {
                    "label": "لا توجد تغطية كافية", "count": 0, "buy": 0, "hold": 0, "sell": 0,
                    "target_mean": np.nan, "target_high": np.nan, "target_low": np.nan, "upside_pct": np.nan
                }
            )

            sector = sector_group(fi, t)
            qscore, qreasons, qpoints = quarterly_financial_score(fi, sector) if t in fund_targets else (5.0, [], 0)
            sector_score, sector_reasons = sector_quality_score(fi, sector) if t in fund_targets else (5.0, [])

            btobj = bt_results.get(t, {
                "score":5.0,"samples":0,"win5":np.nan,"win10":np.nan,"win20":np.nan,
                "avg5":np.nan,"avg10":np.nan,"avg20":np.nan,"profit_factor":np.nan,"max_drawdown":np.nan
            })
            bt = float(btobj.get("score", 5.0))
            bt_win = btobj.get("win10", np.nan)
            bt_avg = btobj.get("avg10", np.nan)
            bt_n = int(btobj.get("samples", 0) or 0)

            component_scores = {
                "Technical": float(row["Technical"]),
                "OfficialNews": float(row["OfficialNews"]),
                "WebNews": ws,
                "Fundamental": fs,
                "Liquidity10": float(row["Liquidity10"]),
                "RelativeStrength": float(row["RelativeStrength"]),
                "Setup": float(row["Setup"]),
                "MarketScore": float(row["MarketScore"]),
                "Backtest": bt,
                "Analyst": analyst_s,
                "Quarterly": qscore,
                "Sector": sector_score,
            }

            raw_score = (
                component_scores["Technical"] +
                component_scores["OfficialNews"] +
                component_scores["WebNews"] +
                component_scores["Fundamental"] +
                component_scores["Liquidity10"] +
                component_scores["RelativeStrength"] +
                component_scores["Setup"] +
                component_scores["MarketScore"]
            )
            # الاختبار التاريخي وتوصيات المحللين لا يسيطران على النتيجة:
            # كل منهما تعديل محدود من -5 إلى +5 حول الدرجة الأساسية.
            # V8: التحاليل العميقة أصبحت تعديلات خفيفة وليست بوابة تمنع الفرص.
            # Forecast الإيجابي يستطيع رفع فرصة متوسطة قليلًا، بينما التوقع السلبي لا يخفضها بقوة.
            fc_score = float(row.get("ForecastScore", 50.0))
            fc_adjust = float(np.clip((fc_score - 50.0) / 10.0, -1.5, 4.0))
            final_score = float(np.clip(
                raw_score
                + np.clip((bt - 5.0) * 0.55, -2.5, 2.5)
                + np.clip((analyst_s - 5.0) * 0.45, -2.0, 2.0)
                + np.clip((qscore - 5.0) * 0.40, -2.0, 2.0)
                + np.clip((sector_score - 5.0) * 0.40, -2.0, 2.0)
                + fc_adjust,
                0, 100
            ))

            conf = confidence_score(
                details[t]["ind"], fi, details[t]["official"], web_items,
                component_scores, bt_n
            )

            prev = st.session_state.previous_scores.get(t, np.nan)
            delta = final_score - prev if pd.notna(prev) else 0.0

            result.at[idx, "WebNews"] = ws
            result.at[idx, "Fundamental"] = fs
            result.at[idx, "Backtest"] = bt
            result.at[idx, "BacktestWin5"] = btobj.get("win5", np.nan)
            result.at[idx, "BacktestWin10"] = btobj.get("win10", np.nan)
            result.at[idx, "BacktestWin20"] = btobj.get("win20", np.nan)
            result.at[idx, "BacktestAvg5"] = btobj.get("avg5", np.nan)
            result.at[idx, "BacktestAvg10"] = btobj.get("avg10", np.nan)
            result.at[idx, "BacktestAvg20"] = btobj.get("avg20", np.nan)
            result.at[idx, "BacktestPF"] = btobj.get("profit_factor", np.nan)
            result.at[idx, "BacktestMDD"] = btobj.get("max_drawdown", np.nan)
            result.at[idx, "Quarterly"] = qscore
            result.at[idx, "QuarterlyPoints"] = qpoints
            result.at[idx, "SectorScore"] = sector_score
            result.at[idx, "SectorGroup"] = sector
            result.at[idx, "Analyst"] = analyst_s
            result.at[idx, "AnalystLabel"] = analyst_meta.get("label", "لا توجد تغطية كافية")
            result.at[idx, "AnalystCount"] = analyst_meta.get("count", 0)
            result.at[idx, "AnalystBuy"] = analyst_meta.get("buy", 0)
            result.at[idx, "AnalystHold"] = analyst_meta.get("hold", 0)
            result.at[idx, "AnalystSell"] = analyst_meta.get("sell", 0)
            result.at[idx, "TargetMean"] = analyst_meta.get("target_mean", np.nan)
            result.at[idx, "TargetHigh"] = analyst_meta.get("target_high", np.nan)
            result.at[idx, "TargetLow"] = analyst_meta.get("target_low", np.nan)
            result.at[idx, "TargetUpside"] = analyst_meta.get("upside_pct", np.nan)
            result.at[idx, "BacktestWinRate"] = bt_win
            result.at[idx, "BacktestAvgReturn"] = bt_avg
            result.at[idx, "BacktestSamples"] = bt_n
            result.at[idx, "WebCount"] = len(web_items)
            result.at[idx, "Score"] = round(final_score, 1)
            result.at[idx, "Confidence"] = round(conf, 1)
            result.at[idx, "DeltaScore"] = round(delta, 1)
            result.at[idx, "Signal"] = label(final_score)
            result.at[idx, "ForecastAction"] = forecast_action(
                final_score,
                float(row.get("ForecastProb", 50.0)),
                float(row.get("ForecastExpected", 0.0)),
                int(row.get("ForecastHorizon", 10))
            )
            decision_v8 = final_decision(
                final_score,
                float(row.get("ForecastProb", 50.0)),
                float(row.get("ForecastExpected", 0.0)),
                float(row.get("Technical", 0.0))
            )
            result.at[idx, "FinalDecision"] = decision_v8
            result.at[idx, "SuggestedAction"] = suggested_action(
                decision_v8,
                float(row.get("EntryLow", np.nan)),
                float(row.get("EntryHigh", np.nan)),
                float(row.get("Stop", np.nan))
            )
            # ترتيب الفرصة يمزج التحليل العام والتوقع الزمني.
            result.at[idx, "OpportunityRank"] = round(
                0.58 * final_score + 0.42 * float(row.get("ForecastScore", 50.0)),
                1
            )

            details[t]["web"] = web_items
            details[t]["fund_info"] = fi
            details[t]["fund_reasons"] = freasons
            details[t]["web_reasons"] = wreasons
            details[t]["analyst_reasons"] = analyst_reasons
            details[t]["analyst_meta"] = analyst_meta
            details[t]["quarterly_reasons"] = qreasons
            details[t]["sector_reasons"] = sector_reasons
            details[t]["sector_group"] = sector
            details[t]["backtest"] = btobj
            details[t]["component_scores"] = component_scores

        result = result.sort_values(
            ["OpportunityRank", "Score", "ForecastProb", "Confidence"],
            ascending=False
        ).reset_index(drop=True)

        # تحديث المقارنة مع الفحص التالي
        st.session_state.previous_scores = dict(zip(result["Ticker"], result["Score"]))

        # سجل توصيات الجلسة
        now_txt = datetime.now().strftime("%Y-%m-%d %H:%M")
        for _, rrw in result.head(10).iterrows():
            st.session_state.recommendation_history.append({
                "وقت الفحص": now_txt,
                "الرمز": rrw["Ticker"],
                "الشركة": rrw["NameAR"],
                "الدرجة": rrw["Score"],
                "الثقة": rrw["Confidence"],
                "السعر": rrw["Price"],
                "التقييم": rrw["Signal"],
                "قرار V8": rrw.get("FinalDecision", "مراقبة"),
                "إجراء مقترح": rrw.get("SuggestedAction", ""),
                "المدة": rrw.get("ForecastPeriod", ""),
                "احتمال ارتفاع %": rrw.get("ForecastProb", np.nan),
                "عائد متوقع %": rrw.get("ForecastExpected", np.nan),
            })
        st.session_state.recommendation_history = st.session_state.recommendation_history[-200:]
        append_local_history(st.session_state.recommendation_history[-10:])
        st.session_state.scan_result = result
        st.session_state.details = details
        st.session_state.report_url = report_url
        st.session_state.fallback_used = fallback_used
        st.session_state.universe_count = len(universe)
        st.session_state.price_coverage = len(price_map)
        status.update(label="اكتمل تحليل السوق", state="complete", expanded=False)

result = st.session_state.scan_result.copy()

c1,c2,c3,c4 = st.columns(4)
c1.metric("شركات السوق", st.session_state.get("universe_count", len(result)))
c2.metric("تحليل فني", len(result))
c3.metric("فرص 74+", int((result["Score"] >= 74).sum()))
c4.metric("متوسط السوق", f'{result["Score"].mean():.1f}/100')
st.caption(f"تم تحميل {st.session_state.get('universe_count', len(result))} شركة؛ وتوفرت بيانات سعرية/فنية لـ {st.session_state.get('price_coverage', len(result))} شركة.")
market_now = st.session_state.details.get(result.iloc[0]["Ticker"], {}).get("market", {})
if market_now:
    st.info(
        f"حالة السوق: **{market_now.get('label','-')}** | "
        f"فوق EMA20: {market_now.get('above20',0):.0f}% | "
        f"فوق EMA50: {market_now.get('above50',0):.0f}% | "
        f"زخم 20 يوم موجب: {market_now.get('positive20',0):.0f}%"
    )

if st.session_state.get("fallback_used"):
    st.info("تعذر الاتصال المباشر بتقرير البورصة، لذلك استخدم البرنامج النسخة الرسمية المدمجة للربع الثاني 2026: 139 شركة (39 سوق أول + 100 سوق رئيسي).")

st.subheader(f"🏆 أفضل {top_n} فرص حاليًا")
top = result[
    (result["Score"] >= min_score) |
    ((result["ForecastProb"] >= 55) & (result["ForecastExpected"] >= 0))
].head(top_n).copy()
top["Market"] = top["Market"].replace({"Premier":"السوق الأول", "Main":"السوق الرئيسي"})
display_cols = [
    "Ticker","NameAR","Market","Price","OpportunityRank","Score","FinalDecision","SuggestedAction",
    "ForecastPeriod","ForecastProb","ForecastExpected","ForecastPriceLow","ForecastPriceHigh",
    "ForecastTarget","Confidence","Signal","DeltaScore",
    "RR","SectorGroup","Quarterly","SectorScore",
    "AnalystLabel","AnalystCount","TargetMean","TargetUpside",
    "RelativeStrength","Technical","Fundamental","OfficialNews","WebNews",
    "Liquidity10","Setup","BacktestWinRate","EntryLow","EntryHigh","Stop","Target1"
]
disp = top[display_cols].rename(columns={
    "Ticker":"الرمز","NameAR":"الشركة","Market":"السوق","Price":"السعر",
    "OpportunityRank":"ترتيب الفرصة","Score":"الدرجة","FinalDecision":"القرار النهائي",
    "SuggestedAction":"الإجراء المقترح","ForecastPeriod":"المدة المتوقعة","ForecastProb":"احتمال ارتفاع %",
    "ForecastExpected":"عائد متوقع %","ForecastPriceLow":"نطاق سعري أدنى",
    "ForecastPriceHigh":"نطاق سعري أعلى","ForecastTarget":"سعر متوقع",
    "Confidence":"ثقة العوامل %","Signal":"التقييم","DeltaScore":"تغير الدرجة",
    "RR":"R/R","SectorGroup":"القطاع","Quarterly":"مالي ربعي/10","SectorScore":"قطاع/10",
    "AnalystLabel":"رأي المحللين","AnalystCount":"عدد المحللين",
    "TargetMean":"السعر المستهدف","TargetUpside":"فرق الهدف %",
    "RelativeStrength":"قوة نسبية/10","Technical":"فني/25",
    "Fundamental":"أساسي/15","OfficialNews":"إفصاحات/15","WebNews":"أخبار/10",
    "Liquidity10":"سيولة/10","Setup":"توقيت/10","BacktestWinRate":"نجاح تاريخي %",
    "EntryLow":"دخول من","EntryHigh":"دخول إلى","Stop":"وقف","Target1":"هدف 1"
})
for col in ["السعر","السعر المستهدف","سعر متوقع","نطاق سعري أدنى","نطاق سعري أعلى","دخول من","دخول إلى","وقف","هدف 1"]:
    if col in disp.columns:
        disp[col] = disp[col].map(lambda x: round(float(x), 3) if pd.notna(x) else np.nan)
for col in ["ترتيب الفرصة","الدرجة","ثقة العوامل %","تغير الدرجة","R/R","قوة نسبية/10","فني/25",
            "أساسي/15","إفصاحات/15","أخبار/10","سيولة/10","توقيت/10",
            "نجاح تاريخي %","فرق الهدف %","مالي ربعي/10","قطاع/10",
            "احتمال ارتفاع %","عائد متوقع %"]:
    if col in disp.columns:
        disp[col] = disp[col].map(lambda x: round(float(x), 1) if pd.notna(x) else np.nan)
st.dataframe(disp, use_container_width=True, hide_index=True)

with st.expander("📋 جميع الأسهم المحللة + القرار + مدة الصفقة", expanded=False):
    all_market_cols = [
        "Ticker","NameAR","Market","Price","FinalDecision","SuggestedAction",
        "ForecastPeriod","ForecastProb","ForecastExpected","ForecastTarget","Score","Confidence"
    ]
    all_market = result[all_market_cols].copy()
    all_market["Market"] = all_market["Market"].replace({"Premier":"السوق الأول", "Main":"السوق الرئيسي"})
    all_market = all_market.rename(columns={
        "Ticker":"الرمز","NameAR":"الشركة","Market":"السوق","Price":"السعر",
        "FinalDecision":"القرار النهائي","SuggestedAction":"الإجراء المقترح",
        "ForecastPeriod":"المدة المتوقعة","ForecastProb":"احتمال ارتفاع %",
        "ForecastExpected":"عائد متوقع %","ForecastTarget":"سعر متوقع",
        "Score":"الدرجة","Confidence":"الثقة %"
    })
    st.dataframe(all_market.round(2), use_container_width=True, hide_index=True)
    st.caption(f"المعروض هنا {len(all_market)} سهمًا لديها بيانات كافية للتحليل من أصل {st.session_state.get('universe_count', len(all_market))} شركة تم تحميلها.")

with st.expander("🌱 فرص مبكرة — شروط أخف", expanded=False):
    early = result[
        (result["Score"] >= 44) &
        (result["Score"] < 64) &
        (result["ForecastProb"] >= 56) &
        (result["ForecastExpected"] > 0)
    ].head(12).copy()
    if not early.empty:
        ed = early[[
            "Ticker","NameAR","Price","ForecastAction","ForecastPeriod",
            "ForecastProb","ForecastExpected","ForecastTarget","Score","Confidence"
        ]].rename(columns={
            "Ticker":"الرمز","NameAR":"الشركة","Price":"السعر","ForecastAction":"توقع زمني",
            "ForecastPeriod":"المدة","ForecastProb":"احتمال ارتفاع %",
            "ForecastExpected":"عائد متوقع %","ForecastTarget":"السعر المتوقع",
            "Score":"الدرجة","Confidence":"الثقة %"
        }).round(2)
        st.dataframe(ed, use_container_width=True, hide_index=True)
        st.caption("هذه فرص مبكرة وليست بنفس قوة أعلى القائمة؛ وضعت هنا لأنك طلبت فلترة أخف ووجود فرص أكثر.")
    else:
        st.caption("لا توجد فرص مبكرة مطابقة في الفحص الحالي.")

# أسهم تحتاج حذر
with st.expander("⚠️ أسهم تحتاج حذر الآن", expanded=False):
    weak = result.sort_values(["Score","Confidence"], ascending=True).head(8).copy()
    weak_disp = weak[["Ticker","NameAR","Score","Confidence","RSI","Momentum20","RR"]].rename(columns={
        "Ticker":"الرمز","NameAR":"الشركة","Score":"الدرجة","Confidence":"الثقة %",
        "RSI":"RSI","Momentum20":"زخم20%","RR":"R/R"
    })
    st.dataframe(weak_disp, use_container_width=True, hide_index=True)


st.subheader("💼 محفظة استثمار مقترحة")
if portfolio_capital > 0:
    candidates = result[
        (
            (result["Score"] >= 58) |
            ((result["ForecastProb"] >= 60) & (result["ForecastExpected"] > 0))
        ) &
        (result["Confidence"] >= 30) &
        (result["RR"] >= 0.9)
    ].copy().head(max(portfolio_count * 2, portfolio_count))

    if len(candidates) >= 2:
        # Score/risk weighted allocation, with 30% cap per stock.
        risk = candidates["RiskPct"].clip(lower=0.8, upper=8.0)
        quality = (
            candidates["Score"].clip(lower=1) *
            candidates["Confidence"].clip(lower=20) *
            (0.7 + candidates["ForecastProb"].clip(lower=40, upper=80) / 100.0)
        ) / risk
        weights = quality / quality.sum()

        # iterative cap at 30%, then renormalize the rest
        for _ in range(5):
            over = weights > 0.30
            if not over.any():
                break
            excess = float((weights[over] - 0.30).sum())
            weights[over] = 0.30
            under = ~over
            if under.any() and weights[under].sum() > 0:
                weights[under] += excess * (weights[under] / weights[under].sum())

        candidates = candidates.head(portfolio_count).copy()
        weights = weights.loc[candidates.index]
        weights = weights / weights.sum()

        candidates["وزن %"] = weights.values * 100
        candidates["مبلغ مقترح د.ك"] = weights.values * float(portfolio_capital)
        candidates["مخاطرة تقريبية د.ك"] = candidates["مبلغ مقترح د.ك"] * (candidates["RiskPct"] / 100.0)

        pdf = candidates[[
            "Ticker","NameAR","SectorGroup","ForecastAction","ForecastPeriod",
            "ForecastProb","ForecastExpected","Score","Confidence","RR",
            "وزن %","مبلغ مقترح د.ك","مخاطرة تقريبية د.ك"
        ]].rename(columns={
            "Ticker":"الرمز","NameAR":"الشركة","SectorGroup":"القطاع",
            "ForecastAction":"توقع زمني","ForecastPeriod":"المدة",
            "ForecastProb":"احتمال ارتفاع %","ForecastExpected":"عائد متوقع %",
            "Score":"الدرجة","Confidence":"الثقة %","RR":"R/R"
        }).round(2)
        st.dataframe(pdf, use_container_width=True, hide_index=True)
        st.caption("التوزيع تعليمي مبني على جودة الفرصة والمخاطرة؛ ليس أمر شراء ولا يراعي ظروفك المالية الشخصية.")
    else:
        st.info("لا توجد حالياً فرص كافية تستوفي شروط المحفظة المقترحة.")
else:
    st.caption("أدخل رأس مال أكبر من صفر في إعدادات الفحص لعرض توزيع محفظة مقترح.")

st.subheader("🔎 تحليل سهم بالتفصيل")
selected = st.selectbox(
    "اختر سهمًا",
    result["Ticker"].tolist(),
    format_func=lambda t: f"{result.loc[result['Ticker']==t,'NameAR'].iloc[0]} — {t}"
)
row = result[result["Ticker"] == selected].iloc[0]
st.markdown(f"### {row['NameAR']}  ·  `{row['Ticker']}`")
st.caption(str(row["Name"]))
det = st.session_state.details.get(selected, {})
ind = det.get("ind", {})
fc = det.get("forecast", {})
if fc:
    st.markdown("### 🔮 التوقع الزمني V8")
    fc1, fc2, fc3 = st.columns(3)
    fc1.metric("القرار النهائي V8", str(row.get("FinalDecision", "مراقبة")))
    fc2.metric("أفضل مدة", str(row.get("ForecastPeriod", "-")))
    fc3.metric("احتمال ارتفاع تاريخي", f"{row.get('ForecastProb',50):.1f}%")

    fc4, fc5, fc6 = st.columns(3)
    fc4.metric("العائد المتوقع", f"{row.get('ForecastExpected',0):.2f}%")
    fc5.metric("السعر المتوقع", fmt_num(row.get("ForecastTarget", np.nan)))
    fc6.metric("عينات مشابهة", int(row.get("ForecastSamples", 0) or 0))

    fcrange = pd.DataFrame([{
        "النطاق الأدنى": fmt_num(row.get("ForecastPriceLow", np.nan)),
        "السعر الحالي": fmt_num(row["Price"]),
        "السعر المتوقع": fmt_num(row.get("ForecastTarget", np.nan)),
        "النطاق الأعلى": fmt_num(row.get("ForecastPriceHigh", np.nan)),
        "إلغاء الفكرة/الستوب": fmt_num(row["Stop"]),
    }])
    st.dataframe(fcrange, use_container_width=True, hide_index=True)

    all_fc = fc.get("all", {})
    if all_fc:
        rows_fc = []
        horizon_names = {5:"5 جلسات",10:"10 جلسات",20:"20 جلسة",60:"60 جلسة"}
        for hz in [5,10,20,60]:
            s = all_fc.get(hz, {})
            rows_fc.append({
                "المدة": horizon_names[hz],
                "احتمال ارتفاع %": s.get("prob_up", np.nan),
                "عائد متوقع %": s.get("expected", np.nan),
                "متوسط/Median %": s.get("median", np.nan),
                "نطاق أدنى %": s.get("low", np.nan),
                "نطاق أعلى %": s.get("high", np.nan),
                "العينات": s.get("samples", 0),
            })
        st.dataframe(pd.DataFrame(rows_fc).round(2), use_container_width=True, hide_index=True)
    st.caption("المدة والاحتمال مبنيان على حالات سعرية تاريخية مشابهة، وليسا ضمانًا بأن السهم سيرتفع في نفس المدة.")

left, mid, right = st.columns(3)
left.metric("الدرجة", f"{row['Score']:.1f}/100", row["Signal"])
mid.metric("ثقة العوامل", f"{row['Confidence']:.0f}%")
right.metric("R/R", f"{row['RR']:.2f}")

left2, mid2, right2 = st.columns(3)
left2.metric("السعر", fmt_num(row["Price"]))
mid2.metric("مخاطرة الستوب", fmt_pct(row["RiskPct"]))
right2.metric("القوة النسبية", f"{row['RelativeStrength']:.1f}/10")

reasons = (
    det.get("tech_reasons", []) +
    det.get("relative_reasons", []) +
    det.get("setup_reasons", []) +
    det.get("official_reasons", []) +
    det.get("web_reasons", []) +
    det.get("analyst_reasons", []) +
    det.get("quarterly_reasons", []) +
    det.get("sector_reasons", []) +
    det.get("fund_reasons", [])
)
if reasons:
    st.success("أسباب التقييم: " + " • ".join(reasons[:12]))
else:
    st.info("العوامل الحالية متوازنة ولا توجد إشارة تفوق واضحة.")

levels = pd.DataFrame([{
    "منطقة الدخول": f"{fmt_num(row['EntryLow'])} - {fmt_num(row['EntryHigh'])}",
    "وقف مقترح": fmt_num(row["Stop"]),
    "الهدف الأول": fmt_num(row["Target1"]),
    "الهدف الثاني": fmt_num(row["Target2"]),
    "R/R": f"{row['RR']:.2f}",
    "RSI": f"{row['RSI']:.1f}",
    "زخم 20 يوم": fmt_pct(row["Momentum20"]),
    "زخم 60 يوم": fmt_pct(row["Momentum60"]),
}])
st.dataframe(levels, use_container_width=True, hide_index=True)

st.markdown("### تفصيل الدرجة")
breakdown = pd.DataFrame([{
    "فني /25": row["Technical"],
    "إفصاحات /15": row["OfficialNews"],
    "أخبار /10": row["WebNews"],
    "أساسي /15": row["Fundamental"],
    "سيولة /10": row["Liquidity10"],
    "قوة نسبية /10": row["RelativeStrength"],
    "توقيت /10": row["Setup"],
    "السوق /5": row["MarketScore"],
    "اختبار تاريخي /10": row["Backtest"],
    "مالي ربعي /10": row["Quarterly"],
    "تقييم قطاع /10": row["SectorScore"],
    "محللون /10": row["Analyst"],
}])
st.dataframe(breakdown.round(1), use_container_width=True, hide_index=True)

bt = det.get("backtest", {})
if bt:
    bn = int(bt.get("samples", 0) or 0)
    if bn >= 5 and pd.notna(bt.get("win10", np.nan)):
        st.markdown("### 🧪 Backtest احترافي")
        btdf = pd.DataFrame([{
            "العينات": bn,
            "نجاح 5 جلسات %": bt.get("win5", np.nan),
            "نجاح 10 جلسات %": bt.get("win10", np.nan),
            "نجاح 20 جلسة %": bt.get("win20", np.nan),
            "متوسط 5 جلسات %": bt.get("avg5", np.nan),
            "متوسط 10 جلسات %": bt.get("avg10", np.nan),
            "متوسط 20 جلسة %": bt.get("avg20", np.nan),
            "Profit Factor": bt.get("profit_factor", np.nan),
            "أقصى هبوط تاريخي %": bt.get("max_drawdown", np.nan),
        }]).round(2)
        st.dataframe(btdf, use_container_width=True, hide_index=True)
    else:
        st.caption("الاختبار التاريخي: العينات غير كافية لإعطاء إحصاء قوي.")

price_df = det.get("price_df")
if isinstance(price_df, pd.DataFrame) and not price_df.empty:
    chart = price_df[["Close"]].tail(120).rename(columns={"Close":"Price"})
    st.line_chart(chart)

st.markdown("### الإفصاحات الرسمية المرتبطة")
official = det.get("official", [])
if official:
    for x in official[:8]:
        sent = x["sentiment"]
        mood = "🟢" if sent > 0.5 else ("🔴" if sent < -0.5 else "⚪")
        st.markdown(f"{mood} **{re.sub('<[^>]+>',' ',x['title'])}**  \n{x['published']}")
        if x.get("link"):
            st.link_button("فتح الإفصاح", x["link"])
else:
    st.caption("لا توجد إفصاحات حديثة مطابقة في موجز RSS الحالي.")

st.markdown("### الأخبار العامة المرتبطة")
web_items = det.get("web", [])
if web_items:
    for x in web_items[:6]:
        mood = "🟢" if x["sentiment"] > 0.5 else ("🔴" if x["sentiment"] < -0.5 else "⚪")
        st.markdown(f"{mood} **{re.sub('<[^>]+>',' ',x['title'])}**  \n{x['published']}")
        if x.get("link"):
            st.link_button("فتح الخبر", x["link"])
else:
    st.caption("لم تُحمّل أخبار عامة لهذا السهم في هذا الفحص، أو لم توجد نتائج.")

fi = det.get("fund_info", {})
if fi:
    st.markdown("### 🧾 النتائج المالية الربع سنوية")
    qdf = pd.DataFrame([{
        "القطاع": det.get("sector_group", "عام"),
        "نمو الإيرادات YoY %": fi.get("qRevenueYoY", np.nan),
        "نمو صافي الربح YoY %": fi.get("qNetIncomeYoY", np.nan),
        "نمو EPS YoY %": fi.get("qEPSYoY", np.nan),
        "نمو التدفق التشغيلي YoY %": fi.get("qOperatingCashflowYoY", np.nan),
        "درجة المالي الربعي /10": row.get("Quarterly", 5.0),
        "درجة القطاع /10": row.get("SectorScore", 5.0),
    }]).round(2)
    st.dataframe(qdf, use_container_width=True, hide_index=True)
    if int(fi.get("quarterlyDataPoints", 0) or 0) <= 2:
        st.caption("البيانات الربع سنوية المتاحة لهذا السهم محدودة؛ لذلك يقلل البرنامج وزنها تلقائيًا.")

analyst = det.get("analyst_meta", {})
if analyst:
    st.markdown("### 👥 توصيات المحللين والأسعار المستهدفة")
    ac1, ac2, ac3 = st.columns(3)
    ac1.metric("إجماع المحللين", analyst.get("label", "لا توجد تغطية كافية"))
    ac2.metric("عدد المحللين", int(analyst.get("count", 0) or 0))
    upside = analyst.get("upside_pct", np.nan)
    ac3.metric("فرق السعر المستهدف", f"{upside:.1f}%" if pd.notna(upside) else "-")

    analyst_table = pd.DataFrame([{
        "شراء": analyst.get("buy", 0),
        "احتفاظ": analyst.get("hold", 0),
        "بيع": analyst.get("sell", 0),
        "متوسط الهدف": fmt_num(analyst.get("target_mean")),
        "أعلى هدف": fmt_num(analyst.get("target_high")),
        "أدنى هدف": fmt_num(analyst.get("target_low")),
    }])
    st.dataframe(analyst_table, use_container_width=True, hide_index=True)

    if int(analyst.get("count", 0) or 0) == 0:
        st.caption("هذا السهم لا تظهر له تغطية محللين كافية من مزود البيانات الحالي؛ لذلك تأثير المحللين محايد.")
    elif int(analyst.get("count", 0) or 0) <= 2:
        st.caption("التغطية محدودة؛ البرنامج يقلل تلقائيًا وزن هذا الإجماع في الدرجة النهائية.")

fi = det.get("fund_info", {})
if fi:
    st.markdown("### لقطة أساسية")
    fdf = pd.DataFrame([{
        "القطاع": fi.get("sector","-"),
        "P/E": fmt_num(fi.get("trailingPE"),2),
        "P/B": fmt_num(fi.get("priceToBook"),2),
        "ROE": fmt_pct(fi.get("returnOnEquity"), multiply=True),
        "نمو الإيرادات": fmt_pct(fi.get("revenueGrowth"), multiply=True),
        "نمو الأرباح": fmt_pct(fi.get("earningsGrowth"), multiply=True),
        "عائد التوزيعات": fmt_pct(fi.get("dividendYield"), multiply=True),
    }])
    st.dataframe(fdf, use_container_width=True, hide_index=True)

st.markdown("---")

st.markdown("---")
with st.expander("📈 تغير الفرص منذ الفحص السابق", expanded=False):
    changed = result[result["DeltaScore"].abs() >= 2.0].copy()
    if not changed.empty:
        changed = changed.sort_values("DeltaScore", ascending=False).head(15)
        cd = changed[["Ticker","NameAR","Score","DeltaScore","Signal"]].rename(columns={
            "Ticker":"الرمز","NameAR":"الشركة","Score":"الدرجة",
            "DeltaScore":"التغير","Signal":"التقييم"
        })
        st.dataframe(cd, use_container_width=True, hide_index=True)
    else:
        st.caption("أعد الفحص لاحقًا؛ سيظهر هنا أي سهم تغيرت درجته بشكل واضح.")

with st.expander("🗂️ سجل التوصيات", expanded=False):
    session_hist = pd.DataFrame(st.session_state.recommendation_history)
    local_hist = load_local_history()
    hist = local_hist if not local_hist.empty else session_hist
    if not hist.empty:
        st.dataframe(hist.tail(100), use_container_width=True, hide_index=True)
        st.download_button(
            "تنزيل سجل التوصيات CSV",
            data=hist.to_csv(index=False).encode("utf-8-sig"),
            file_name="kuwait_scanner_recommendation_history.csv",
            mime="text/csv"
        )
        st.caption("ملاحظة: حفظ Streamlit Community Cloud المحلي ليس مضمونًا بعد Reboot/Deploy؛ نزّل CSV دوريًا للاحتفاظ بالسجل.")
    else:
        st.caption("لا يوجد سجل بعد.")

with st.expander("كيف تُحسب الدرجة؟"):
    st.markdown("""
**V4 لا يعطي الدرجة من مؤشر واحد.** الدرجة الأساسية من 100:

- **التحليل الفني: 25** — اتجاه، RSI، MACD، زخم وحجم.
- **الإفصاحات الرسمية: 15** — مع وزن أكبر للإفصاح الحديث ونوعه.
- **الأخبار العامة: 10** — مع تناقص وزن الخبر كلما تقادم.
- **الأساسيات: 15** — P/E، P/B، ROE، نمو الإيرادات والأرباح والتوزيعات.
- **السيولة: 10** — مقارنة ببقية السوق.
- **القوة النسبية: 10** — هل السهم يتفوق على متوسط سوق الكويت؟
- **جودة التوقيت وRisk/Reward: 10** — الدعم/المقاومة، قرب السعر من منطقة دخول، ونسبة العائد للمخاطرة.
- **حالة السوق العامة: 5** — اتساع الصعود بين الأسهم، وليس سهمًا منفردًا فقط.
- **Forecast V8** يبحث عن حالات تاريخية مشابهة ويقارن 5/10/20/60 جلسة؛ يستخدم لترتيب الفرص ولا يعمل كشرط يمنعها.
- **الاختبار التاريخي الاحترافي** أصبح تأثيره أخف، بحد أقصى يقارب **±2.5 نقطة**.
- **توصيات المحللين** تأثيرها أخف، بحد أقصى يقارب **±2 نقطة**.
- **النتائج المالية الربع سنوية** و**تقييم القطاع** أصبح تأثير كل منهما محدودًا بنحو **±2 نقطة**.
- الهدف في V8 هو إظهار فرص أكثر مع فصل **الفرصة المبكرة** عن **مرشح الشراء** بدل إخفاء السهم بالكامل.

**Confidence / ثقة العوامل** تقيس اكتمال البيانات واتفاق العوامل، وليست احتمالًا مضمونًا للربح.

البرنامج أداة فرز وتحليل، وليس ضمانًا أو أمر شراء. بيانات الأسعار العامة قد تكون مؤخرة.
""")

st.caption("المصادر: تقارير وRSS بورصة الكويت + Yahoo Finance للأسعار/بعض الأساسيات + Google News RSS للأخبار العامة. V8 يضيف Forecast احتمالي خفيف 5/10/20/60 جلسة ويخفف تأثير التحاليل العميقة حتى تظهر فرص أكثر، مع إبقاء الأخبار والنتائج والمحللين كعوامل مساندة.")
