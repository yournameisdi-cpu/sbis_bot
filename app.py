import os
import time
import re
import requests
import json
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import logging
from logging.handlers import RotatingFileHandler
import urllib3
from dotenv import load_dotenv
import traceback
from functools import wraps
import sys
import random
from flask import Flask, request, jsonify
import threading
import atexit

# Отключаем SSL предупреждения
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Загружаем переменные окружения
load_dotenv()

# ===================================================
#  НАСТРОЙКИ (из переменных окружения)
# ===================================================

SBIS_LOGIN = os.getenv('SBIS_LOGIN')
SBIS_PASSWORD = os.getenv('SBIS_PASSWORD')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
SEND_TO_TELEGRAM = os.getenv('SEND_TO_TELEGRAM', 'True').lower() == 'true'

MAIL_CONFIG = {
    'imap_server': os.getenv('IMAP_SERVER', 'imap.yandex.ru'),
    'email': os.getenv('MAIL_EMAIL'),
    'password': os.getenv('MAIL_PASSWORD')
}

PROXY_ENABLED = os.getenv('PROXY_ENABLED', 'False').lower() == 'true'
PROXY = os.getenv('PROXY', None)

STORE_NAME = os.getenv('STORE_NAME', "Zibo Food")
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
HEADLESS_MODE = os.getenv('HEADLESS_MODE', 'True').lower() == 'true'
MAX_RETRIES = int(os.getenv('MAX_RETRIES', 3))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', 5))

# Целевой диапазон
TARGET_MIN = int(os.getenv('TARGET_MIN', 580))
TARGET_MAX = int(os.getenv('TARGET_MAX', 620))

# Список ободряющих фраз
MOTIVATIONAL_PHRASES = [
    "🔥 Дорогу осилит идущий! Продолжайте в том же духе!",
    "💪 Вы молодцы! Каждый день становитесь лучше!",
    "🚀 Успех — это сумма маленьких усилий, повторяемых изо дня в день!",
    "🌟 Верьте в себя! У вас всё получится!",
    "💫 Каждый чек — это шаг к вашей цели! Не останавливайтесь!",
    "🎯 Вы способны на большее! Докажите себе и всем!",
    "💥 Победа любит старательных! Вы на правильном пути!",
    "📈 Сегодня вы лучше, чем вчера! Завтра будет ещё лучше!",
    "🏆 Не сдавайтесь! Успех приходит к тем, кто не боится пробовать!",
    "🤝 Команда — это сила! Вместе мы достигнем цели!"
]

# ===================================================
#  FLASK APP
# ===================================================

app = Flask(__name__)

# ===================================================
#  НАСТРОЙКА ЛОГГИРОВАНИЯ
# ===================================================

def setup_logging():
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    log_file = os.path.join(LOG_DIR, f'sbis_parser_{datetime.now().strftime("%Y%m%d")}.log')
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

# ===================================================
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ===================================================

def setup_download_dir():
    for directory in [DOWNLOAD_DIR, LOG_DIR]:
        if not os.path.exists(directory):
            os.makedirs(directory)

def get_date_strings():
    yesterday = datetime.now() - timedelta(days=1)
    return {
        'display': yesterday.strftime("%d.%m.%Y"),
        'iso': yesterday.strftime("%Y-%m-%d"),
        'full': yesterday.strftime("%d %B %Y")
    }

def extract_number(text):
    if not text:
        return 0
    clean = re.sub(r'[^\d\s,.]', '', text)
    clean = clean.replace(' ', '')
    clean = clean.replace(',', '.')
    try:
        return float(clean)
    except ValueError:
        return 0

def clean_employee_name(name):
    if not name:
        return ""
    name = ' '.join(name.split())
    
    if re.search(r'стажер', name, re.IGNORECASE):
        return "Стажер"
    
    match = re.search(r'([А-Я][а-я]+)\s+([А-Я])\.\s*([А-Я])\.?', name)
    if match:
        return f"{match.group(1)} {match.group(2)}.{match.group(3)}."
    
    return name

def check_target(avg_check):
    """Проверяет, попадает ли средний чек в целевой диапазон"""
    if TARGET_MIN <= avg_check <= TARGET_MAX:
        return "✅"
    elif avg_check > TARGET_MAX:
        return "✅"
    else:
        return "❌"

def get_motivational_phrase():
    """Возвращает случайную ободряющую фразу"""
    return random.choice(MOTIVATIONAL_PHRASES)

# ===================================================
#  ПРОВЕРКА ПОЧТЫ
# ===================================================

def get_sbis_download_link():
    logger.info("🔍 Подключаемся к почте...")
    mail = None
    
    try:
        mail = imaplib.IMAP4_SSL(MAIL_CONFIG['imap_server'])
        mail.login(MAIL_CONFIG['email'], MAIL_CONFIG['password'])
        
        try:
            folder_names = ['Saby', 'Saby/', 'INBOX.Saby', '[Gmail]/Saby']
            selected = False
            
            for folder in folder_names:
                try:
                    status, _ = mail.select(folder)
                    if status == 'OK':
                        logger.info(f"✅ Выбрана папка: {folder}")
                        selected = True
                        break
                except:
                    continue
            
            if not selected:
                logger.warning("Папка Saby не найдена, используем INBOX")
                mail.select('INBOX')
        except Exception as e:
            logger.warning(f"Ошибка при выборе папки: {e}, используем INBOX")
            mail.select('INBOX')
        
        date = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(FROM "saby.ru" SINCE "{date}")')
        
        if status != 'OK' or not messages[0]:
            logger.info("Новых писем от СБИС не найдено")
            return None
        
        msg_ids = messages[0].split()
        logger.info(f"Найдено {len(msg_ids)} писем от СБИС")
        
        for msg_id in reversed(msg_ids):
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            if status != 'OK':
                continue
            
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_header(msg['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode('utf-8', errors='ignore')
            
            logger.info(f"📧 Проверяем письмо: {subject[:50]}...")
            
            if not re.search(r'выручк|отчет|отчёт|report', subject, re.IGNORECASE):
                continue
            
            body_text = ''
            body_html = ''
            
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    try:
                        payload = part.get_payload(decode=True)
                        if not payload:
                            continue
                        decoded = payload.decode('utf-8', errors='ignore')
                        if content_type == 'text/plain':
                            body_text += decoded
                        elif content_type == 'text/html':
                            body_html += decoded
                    except:
                        pass
            else:
                try:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body_text = payload.decode('utf-8', errors='ignore')
                except:
                    pass
            
            html_links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>', body_html, re.IGNORECASE)
            for link in html_links:
                if 'disk.sbis.ru' in link or 'online.sbis.ru/disk' in link:
                    if link.startswith('/'):
                        link = 'https://online.sbis.ru' + link
                    logger.info(f"✅ Найдена ссылка в HTML: {link}")
                    mail.store(msg_id, '+FLAGS', '\\Seen')
                    mail.close()
                    mail.logout()
                    return link
            
            text_links = re.findall(r'https?://[^\s<>"\']+', body_text)
            for link in text_links:
                if 'disk.sbis.ru' in link or 'online.sbis.ru/disk' in link:
                    logger.info(f"✅ Найдена ссылка в тексте: {link}")
                    mail.store(msg_id, '+FLAGS', '\\Seen')
                    mail.close()
                    mail.logout()
                    return link
        
        mail.close()
        mail.logout()
        return None
        
    except Exception as e:
        logger.error(f"Ошибка при проверке почты: {e}")
        if mail:
            try:
                mail.close()
                mail.logout()
            except:
                pass
        return None

# ===================================================
#  ПАРСИНГ ДАННЫХ
# ===================================================

def parse_report_from_page(driver):
    logger.info("🔍 Парсим данные со страницы...")
    
    employees = []
    
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(5)
        
        page_text = driver.find_element(By.TAG_NAME, "body").text
        logger.info(f"Получен текст страницы, длина: {len(page_text)} символов")
        
        # Сохраняем текст для отладки
        text_file = os.path.join(LOG_DIR, f'page_text_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
        with open(text_file, 'w', encoding='utf-8') as f:
            f.write(page_text)
        
        lines = page_text.split('\n')
        
        for i, line in enumerate(lines):
            name_match = re.search(r'([А-Я][а-я]+\s+[А-Я]\.\s*[А-Я]\.?|Стажер)', line, re.IGNORECASE)
            if not name_match:
                continue
            
            name = clean_employee_name(name_match.group(1))
            
            if name in ['Выручка', 'Чеков']:
                continue
            
            numbers = re.findall(r'(\d+[\s\d]*\d*)', line)
            clean_numbers = []
            
            for num in numbers:
                clean_num = extract_number(num)
                if clean_num > 0:
                    clean_numbers.append(clean_num)
            
            if len(clean_numbers) < 2:
                for j in range(i + 1, min(i + 3, len(lines))):
                    more_numbers = re.findall(r'(\d+[\s\d]*\d*)', lines[j])
                    for num in more_numbers:
                        clean_num = extract_number(num)
                        if clean_num > 0:
                            clean_numbers.append(clean_num)
            
            if len(clean_numbers) >= 2:
                clean_numbers.sort()
                
                checks = 0
                revenue = 0
                
                for num in clean_numbers:
                    if num.is_integer() and num > 0:
                        if checks == 0 or num < checks:
                            checks = int(num)
                
                if checks == 0:
                    checks = int(clean_numbers[0])
                
                revenue = max(clean_numbers)
                
                if revenue < checks:
                    checks, revenue = revenue, checks
                    checks = int(checks)
                
                if not any(emp['name'] == name for emp in employees):
                    if revenue > 0 and checks > 0:
                        employees.append({
                            'name': name,
                            'revenue': revenue,
                            'checks': checks,
                            'avg_check': round(revenue / checks, 2) if checks > 0 else 0
                        })
                        logger.info(f"👤 Найден сотрудник: {name}, выручка: {revenue}, чеки: {checks}")
        
        # Дополнительный поиск через таблицу
        if len(employees) < 4:
            try:
                tables = driver.find_elements(By.TAG_NAME, "table")
                for table in tables:
                    rows = table.find_elements(By.TAG_NAME, "tr")
                    for row in rows:
                        cells = row.find_elements(By.TAG_NAME, "td")
                        if len(cells) >= 3:
                            cell_texts = [cell.text.strip() for cell in cells if cell.text.strip()]
                            
                            if len(cell_texts) >= 3:
                                name_match = re.search(r'([А-Я][а-я]+\s+[А-Я]\.\s*[А-Я]\.?|Стажер)', cell_texts[0], re.IGNORECASE)
                                if name_match:
                                    name = clean_employee_name(name_match.group(1))
                                    
                                    if name in ['Выручка', 'Чеков']:
                                        continue
                                    
                                    numbers = []
                                    for text in cell_texts[1:]:
                                        num = extract_number(text)
                                        if num > 0:
                                            numbers.append(num)
                                    
                                    if len(numbers) >= 2:
                                        numbers.sort()
                                        checks = int(numbers[0]) if numbers[0] < numbers[-1] else int(numbers[-1])
                                        revenue = max(numbers)
                                        
                                        if revenue < checks:
                                            checks, revenue = revenue, checks
                                            checks = int(checks)
                                        
                                        if not any(emp['name'] == name for emp in employees):
                                            employees.append({
                                                'name': name,
                                                'revenue': revenue,
                                                'checks': checks,
                                                'avg_check': round(revenue / checks, 2) if checks > 0 else 0
                                            })
                                            logger.info(f"👤 Найден сотрудник (таблица): {name}, выручка: {revenue}, чеки: {checks}")
            except Exception as e:
                logger.debug(f"Ошибка при парсинге таблицы: {e}")
        
        # Удаляем дубликаты
        unique_employees = {}
        for emp in employees:
            if emp['name'] not in unique_employees:
                unique_employees[emp['name']] = emp
        
        result = list(unique_employees.values())
        result.sort(key=lambda x: x['revenue'], reverse=True)
        
        logger.info(f"✅ Найдено {len(result)} сотрудников")
        for emp in result:
            logger.info(f"  {emp['name']}: чеки={emp['checks']}, выручка={emp['revenue']}, средний={emp['avg_check']}")
        
        if not result:
            screenshot_path = os.path.join(DOWNLOAD_DIR, f'no_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
            driver.save_screenshot(screenshot_path)
            logger.warning(f"Данные не найдены, сохранен скриншот: {screenshot_path}")
        
        return result
        
    except Exception as e:
        logger.error(f"Ошибка при парсинге: {e}")
        logger.error(traceback.format_exc())
        return []

# ===================================================
#  ФОРМАТИРОВАНИЕ ОТЧЕТА
# ===================================================

def format_report_for_telegram(employees, date_str):
    """Форматирует данные для отправки в Telegram"""
    if not employees:
        return "❌ Данные по сотрудникам не найдены"
    
    # Сортируем по среднему чеку (по убыванию)
    sorted_employees = sorted(employees, key=lambda x: x['avg_check'], reverse=True)
    
    # Общая статистика
    total_revenue = sum(emp['revenue'] for emp in employees)
    total_checks = sum(emp['checks'] for emp in employees)
    total_avg = round(total_revenue / total_checks, 2) if total_checks > 0 else 0
    
    # Считаем кто в цели
    in_target = []
    not_in_target = []
    
    for emp in sorted_employees:
        status = check_target(emp['avg_check'])
        if status == "✅":
            in_target.append(emp)
        else:
            not_in_target.append(emp)
    
    # Формируем отчет
    lines = [
        "📊 Отчет по среднему чеку",
        f"📅 {date_str}",
        f"🏪 {STORE_NAME}",
        "",
        f"🎯 **ЦЕЛЕВОЙ ДИАПАЗОН: {TARGET_MIN} - {TARGET_MAX} ₽**",
        "",
        "👤 **ПО СОТРУДНИКАМ:**",
        ""
    ]
    
    # Список сотрудников
    for emp in sorted_employees:
        status = check_target(emp['avg_check'])
        if status == "✅":
            lines.append(f"{emp['name']} — {emp['avg_check']:,.2f} ₽ ✅")
        else:
            lines.append(f"{emp['name']} — {emp['avg_check']:,.2f} ₽ ❌")
    
    lines.append("")
    lines.append(f"📊 ИТОГИ:")
    lines.append(f"• В цели: {len(in_target)} из {len(employees)} сотрудников")
    lines.append(f"• Средний чек по всем: {total_avg:,.2f} ₽")
    lines.append("")
    
    # Кто не в цели
    if not_in_target:
        names = ", ".join([emp['name'] for emp in not_in_target])
        lines.append(f"💪 Работаем над собой!")
        lines.append(f"{names} — время показать результат!")
        lines.append("")
    
    # Рандомная ободряющая фраза
    lines.append(f"{get_motivational_phrase()}")
    
    return "\n".join(lines)

# ===================================================
#  АВТОРИЗАЦИЯ В СБИС
# ===================================================

def login_to_sbis(driver):
    logger.info("🔐 Выполняем вход в СБИС...")
    
    try:
        if "login" in driver.current_url or "auth" in driver.current_url:
            logger.info("Выполняем вход...")
            
            login_input = None
            try:
                login_input = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "login"))
                )
            except:
                try:
                    login_input = driver.find_element(By.NAME, "login")
                except:
                    try:
                        login_input = driver.find_element(By.CSS_SELECTOR, "input[type='text']")
                    except:
                        try:
                            login_input = driver.find_element(By.XPATH, "//input[@placeholder='Логин' or @placeholder='Телефон' or @placeholder='Email']")
                        except:
                            pass
            
            if login_input:
                login_input.clear()
                login_input.send_keys(SBIS_LOGIN)
                logger.info("✅ Введен логин")
                time.sleep(1)
                
                login_input.send_keys(Keys.RETURN)
                logger.info("🔄 Нажат Enter для отображения поля пароля")
                time.sleep(3)
                
                password_input = None
                try:
                    password_input = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.ID, "password"))
                    )
                except:
                    try:
                        password_input = driver.find_element(By.NAME, "password")
                    except:
                        try:
                            password_input = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
                        except:
                            pass
                
                if password_input:
                    password_input.clear()
                    password_input.send_keys(SBIS_PASSWORD)
                    logger.info("✅ Введен пароль")
                    time.sleep(1)
                    
                    try:
                        submit_button = None
                        try:
                            submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                        except:
                            try:
                                submit_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Войти')]")
                            except:
                                try:
                                    submit_button = driver.find_element(By.XPATH, "//button[contains(text(), 'Вход')]")
                                except:
                                    pass
                        
                        if submit_button:
                            submit_button.click()
                            logger.info("✅ Нажата кнопка 'Войти'")
                        else:
                            password_input.send_keys(Keys.RETURN)
                            logger.info("🔄 Нажат Enter для входа")
                        
                        time.sleep(5)
                    except Exception as e:
                        logger.warning(f"Ошибка при нажатии кнопки входа: {e}")
                        password_input.send_keys(Keys.RETURN)
                        time.sleep(5)
                else:
                    logger.warning("Поле пароля не появилось, возможно, вход уже выполнен")
            else:
                logger.warning("Поле логина не найдено, возможно, уже авторизованы")
        else:
            logger.info("Уже авторизованы, вход не требуется")
            
    except Exception as e:
        logger.warning(f"Ошибка при авторизации: {e}")
        logger.info("⏳ Если автоматический вход не удался, войдите вручную за 20 секунд...")
        time.sleep(20)

# ===================================================
#  ОСНОВНАЯ ФУНКЦИЯ ПАРСИНГА
# ===================================================

def download_report_from_link(download_link):
    logger.info("🚀 Загружаем страницу с отчётом...")
    
    options = Options()
    
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    
    options.add_argument("--disable-notifications")
    options.add_argument("--ignore-ssl-errors=yes")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    if HEADLESS_MODE:
        options.add_argument("--headless=new")
        logger.info("Запуск в headless режиме")
    
    if PROXY_ENABLED and PROXY:
        options.add_argument(f'--proxy-server={PROXY}')
        logger.info(f"Используется прокси: {PROXY}")
    
    driver = None
    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.implicitly_wait(15)
        driver.set_window_size(1920, 1080)
        
        logger.info("Открываем сайт СБИС...")
        driver.get("https://online.sbis.ru/")
        time.sleep(3)
        
        login_to_sbis(driver)
        
        logger.info(f"Переходим по ссылке: {download_link}")
        driver.get(download_link)
        time.sleep(8)
        
        employees = parse_report_from_page(driver)
        
        if employees:
            date_str = get_date_strings()['display']
            message = format_report_for_telegram(employees, date_str)
            return {
                "message": message,
                "employees": employees,
                "count": len(employees)
            }
        else:
            return None
            
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        try:
            if driver:
                screenshot_path = os.path.join(DOWNLOAD_DIR, f'error_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png')
                driver.save_screenshot(screenshot_path)
                logger.info(f"Сохранен скриншот ошибки: {screenshot_path}")
        except:
            pass
        raise
        
    finally:
        if driver:
            driver.quit()
            logger.info("Браузер закрыт")

# ===================================================
#  ОТПРАВКА В TELEGRAM
# ===================================================

def send_text_to_telegram(text):
    """Отправляет текстовое сообщение в Telegram"""
    if not SEND_TO_TELEGRAM:
        logger.info("📝 Отправка в Telegram отключена")
        return True
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    if len(text) > 4096:
        parts = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for i, part in enumerate(parts):
            data = {
                'chat_id': TELEGRAM_CHAT_ID,
                'text': part,
                'parse_mode': 'Markdown'
            }
            _send_telegram_request(url, data)
            time.sleep(1)
        return True
    
    data = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown'
    }
    return _send_telegram_request(url, data)

def _send_telegram_request(url, data):
    proxies = None
    if PROXY_ENABLED and PROXY:
        proxies = {'http': PROXY, 'https': PROXY}
    
    try:
        response = requests.post(
            url,
            json=data,
            proxies=proxies,
            timeout=30,
            verify=False
        )
        
        if response.status_code == 200:
            logger.info("✅ Сообщение отправлено в Telegram")
            return True
        else:
            logger.error(f"Ошибка отправки: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        return False

# ===================================================
#  ОСНОВНАЯ ФУНКЦИЯ
# ===================================================

def run_parser():
    """Основная функция парсинга"""
    logger.info("=" * 60)
    logger.info("🔄 ЗАПУСК ПАРСИНГА")
    logger.info(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        setup_download_dir()
        
        # Проверка наличия обязательных переменных
        if not all([SBIS_LOGIN, SBIS_PASSWORD, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, MAIL_CONFIG['email'], MAIL_CONFIG['password']]):
            error_msg = "❌ Отсутствуют обязательные переменные окружения. Проверьте настройки."
            logger.error(error_msg)
            send_text_to_telegram(error_msg)
            return
        
        logger.info("\n📧 Шаг 1: Проверка почты (подпапка Saby)...")
        link = get_sbis_download_link()
        
        if not link:
            msg = "❌ Новых ссылок на отчеты не найдено"
            logger.info(msg)
            send_text_to_telegram(msg)
            return
        
        logger.info("\n🌐 Шаг 2: Парсинг отчета...")
        result = download_report_from_link(link)
        
        if not result:
            msg = "❌ Не удалось получить данные отчёта"
            logger.error(msg)
            send_text_to_telegram(msg)
            return
        
        logger.info("\n📤 Шаг 3: Отправка отчета в Telegram...")
        success = send_text_to_telegram(result["message"])
        
        if success:
            logger.info(f"✅ Отчет отправлен! Найдено сотрудников: {result['count']}")
        else:
            logger.error("❌ Не удалось отправить отчет")
        
        logger.info("\n📊 Итоги:")
        logger.info(f"• Сотрудников: {result['count']}")
        logger.info(f"• Общая выручка: {sum(emp['revenue'] for emp in result['employees']):,.0f} ₽")
        logger.info(f"• Всего чеков: {sum(emp['checks'] for emp in result['employees']):,}")
        
        logger.info("\n" + "=" * 60)
        logger.info("🎉 ПАРСИНГ ВЫПОЛНЕН УСПЕШНО!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
        
        error_msg = f"❌ Ошибка выполнения скрипта\n\n{str(e)}"
        send_text_to_telegram(error_msg)
        
        error_log = os.path.join(LOG_DIR, f'error_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        with open(error_log, 'w', encoding='utf-8') as f:
            f.write(traceback.format_exc())
        logger.info(f"Сохранен лог ошибки: {error_log}")

# ===================================================
#  FLASK ENDPOINTS
# ===================================================

@app.route('/')
def index():
    return jsonify({
        'status': 'ok',
        'message': 'SBIS Parser is running!',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/run')
def run():
    """Endpoint для запуска парсинга"""
    try:
        thread = threading.Thread(target=run_parser)
        thread.start()
        return jsonify({
            'status': 'started',
            'message': 'Parser started successfully'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/health')
def health():
    """Health check endpoint для UptimeRobot"""
    return jsonify({
        'status': 'healthy',
        'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

# ===================================================
#  ЗАПУСК
# ===================================================

if __name__ == "__main__":
    # При запуске сразу выполняем парсинг
    run_parser()
    
    # Запускаем Flask сервер
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)