import os
import time
import re
import requests
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
import logging
import urllib3
from dotenv import load_dotenv
import traceback
import random
from flask import Flask, jsonify
import threading
import subprocess

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

# ===================================================
#  НАСТРОЙКИ
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

STORE_NAME = os.getenv('STORE_NAME', "Zibo Food")

# Используем /tmp для загрузок и логов (доступно для записи)
DOWNLOAD_DIR = "/tmp/downloads"
LOG_DIR = "/tmp/logs"

TARGET_MIN = int(os.getenv('TARGET_MIN', 580))
TARGET_MAX = int(os.getenv('TARGET_MAX', 620))

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

app = Flask(__name__)

# ===================================================
#  ЛОГГИРОВАНИЕ
# ===================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===================================================
#  ФУНКЦИИ
# ===================================================

def setup_download_dir():
    """Создает папки для загрузок и логов"""
    for directory in [DOWNLOAD_DIR, LOG_DIR]:
        try:
            os.makedirs(directory, exist_ok=True)
            os.chmod(directory, 0o777)
            logger.info(f"✅ Создана папка: {directory}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось создать папку {directory}: {e}")

def get_date_strings():
    yesterday = datetime.now() - timedelta(days=1)
    return {'display': yesterday.strftime("%d.%m.%Y")}

def extract_number(text):
    if not text:
        return 0
    clean = re.sub(r'[^\d\s,.]', '', text).replace(' ', '').replace(',', '.')
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
    if TARGET_MIN <= avg_check <= TARGET_MAX:
        return "✅"
    elif avg_check > TARGET_MAX:
        return "✅"
    else:
        return "❌"

def get_motivational_phrase():
    return random.choice(MOTIVATIONAL_PHRASES)

def get_chrome_driver():
    options = Options()
    
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    prefs = {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True
    }
    options.add_experimental_option("prefs", prefs)
    
    # Ищем ChromeDriver в системе
    driver_paths = [
        "/usr/local/bin/chromedriver",
        "/usr/bin/chromedriver"
    ]
    
    driver_path = None
    for path in driver_paths:
        if os.path.exists(path):
            driver_path = path
            logger.info(f"✅ Найден ChromeDriver: {path}")
            break
    
    if not driver_path:
        try:
            result = subprocess.run(["which", "chromedriver"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                driver_path = result.stdout.strip()
                logger.info(f"✅ ChromeDriver найден через which: {driver_path}")
        except:
            pass
    
    if not driver_path:
        raise Exception("ChromeDriver не найден в системе")
    
    service = Service(driver_path)
    
    try:
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.implicitly_wait(10)
        return driver
    except Exception as e:
        logger.error(f"Ошибка создания драйвера: {e}")
        raise

# ===================================================
#  ПОЧТА
# ===================================================

def get_sbis_download_link():
    logger.info("🔍 Подключаемся к почте...")
    mail = None
    
    try:
        mail = imaplib.IMAP4_SSL(MAIL_CONFIG['imap_server'])
        mail.login(MAIL_CONFIG['email'], MAIL_CONFIG['password'])
        
        try:
            mail.select('Saby')
        except:
            logger.warning("Папка Saby не найдена, используем INBOX")
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
            
            body_html = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/html':
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                body_html = payload.decode('utf-8', errors='ignore')
                                break
                        except:
                            pass
            
            html_links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>', body_html, re.IGNORECASE)
            for link in html_links:
                if 'disk.sbis.ru' in link or 'online.sbis.ru/disk' in link:
                    if link.startswith('/'):
                        link = 'https://online.sbis.ru' + link
                    logger.info(f"✅ Найдена ссылка: {link}")
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
#  ПАРСИНГ
# ===================================================

def parse_report_from_page(driver):
    logger.info("🔍 Парсим данные...")
    employees = []
    
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(5)
        
        page_text = driver.find_element(By.TAG_NAME, "body").text
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
                        logger.info(f"👤 Найден: {name}, выручка: {revenue}, чеки: {checks}")
        
        unique_employees = {}
        for emp in employees:
            if emp['name'] not in unique_employees:
                unique_employees[emp['name']] = emp
        
        result = list(unique_employees.values())
        result.sort(key=lambda x: x['revenue'], reverse=True)
        
        logger.info(f"✅ Найдено {len(result)} сотрудников")
        return result
        
    except Exception as e:
        logger.error(f"Ошибка при парсинге: {e}")
        return []

# ===================================================
#  ФОРМАТИРОВАНИЕ ОТЧЕТА
# ===================================================

def format_report_for_telegram(employees, date_str):
    if not employees:
        return "❌ Данные по сотрудникам не найдены"
    
    sorted_employees = sorted(employees, key=lambda x: x['avg_check'], reverse=True)
    total_revenue = sum(emp['revenue'] for emp in employees)
    total_checks = sum(emp['checks'] for emp in employees)
    total_avg = round(total_revenue / total_checks, 2) if total_checks > 0 else 0
    
    in_target = []
    not_in_target = []
    
    for emp in sorted_employees:
        status = check_target(emp['avg_check'])
        if status == "✅":
            in_target.append(emp)
        else:
            not_in_target.append(emp)
    
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
    
    for emp in sorted_employees:
        status = check_target(emp['avg_check'])
        lines.append(f"{emp['name']} — {emp['avg_check']:,.2f} ₽ {status}")
    
    lines.append("")
    lines.append(f"📊 ИТОГИ:")
    lines.append(f"• В цели: {len(in_target)} из {len(employees)} сотрудников")
    lines.append(f"• Средний чек по всем: {total_avg:,.2f} ₽")
    lines.append("")
    
    if not_in_target:
        names = ", ".join([emp['name'] for emp in not_in_target])
        lines.append(f"💪 Работаем над собой!")
        lines.append(f"{names} — время показать результат!")
        lines.append("")
    
    lines.append(f"{get_motivational_phrase()}")
    
    return "\n".join(lines)

# ===================================================
#  ВХОД В СБИС
# ===================================================

def login_to_sbis(driver):
    logger.info("🔐 Выполняем вход в СБИС...")
    
    try:
        if "login" in driver.current_url or "auth" in driver.current_url:
            logger.info("Выполняем вход...")
            
            login_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "login"))
            )
            login_input.clear()
            login_input.send_keys(SBIS_LOGIN)
            login_input.send_keys(Keys.RETURN)
            time.sleep(3)
            
            password_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "password"))
            )
            password_input.clear()
            password_input.send_keys(SBIS_PASSWORD)
            time.sleep(1)
            
            try:
                submit_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                submit_button.click()
            except:
                password_input.send_keys(Keys.RETURN)
            
            time.sleep(5)
            logger.info("✅ Вход выполнен")
            
    except Exception as e:
        logger.warning(f"Ошибка при авторизации: {e}")
        time.sleep(20)

# ===================================================
#  ОСНОВНАЯ ФУНКЦИЯ
# ===================================================

def download_report_from_link(download_link):
    logger.info("🚀 Загружаем страницу...")
    
    driver = None
    try:
        driver = get_chrome_driver()
        driver.get("https://online.sbis.ru/")
        time.sleep(3)
        
        login_to_sbis(driver)
        
        driver.get(download_link)
        time.sleep(8)
        
        employees = parse_report_from_page(driver)
        
        if employees:
            date_str = get_date_strings()['display']
            message = format_report_for_telegram(employees, date_str)
            return {"message": message, "employees": employees, "count": len(employees)}
        else:
            return None
            
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        raise
        
    finally:
        if driver:
            driver.quit()
            logger.info("Браузер закрыт")

def send_text_to_telegram(text):
    if not SEND_TO_TELEGRAM:
        logger.info("📝 Отправка в Telegram отключена")
        logger.info("=" * 50)
        logger.info(text)
        logger.info("=" * 50)
        return True
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'}
    
    try:
        response = requests.post(url, json=data, timeout=30, verify=False)
        if response.status_code == 200:
            logger.info("✅ Сообщение отправлено в Telegram")
            return True
        else:
            logger.error(f"Ошибка отправки: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return False

def run_parser():
    logger.info("=" * 60)
    logger.info("🔄 ЗАПУСК ПАРСИНГА")
    logger.info(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    try:
        setup_download_dir()
        
        if not all([SBIS_LOGIN, SBIS_PASSWORD, MAIL_CONFIG['email'], MAIL_CONFIG['password']]):
            error_msg = "❌ Отсутствуют обязательные переменные окружения"
            logger.error(error_msg)
            return
        
        logger.info("\n📧 Шаг 1: Проверка почты...")
        link = get_sbis_download_link()
        
        if not link:
            logger.info("❌ Новых ссылок на отчеты не найдено")
            return
        
        logger.info("\n🌐 Шаг 2: Парсинг отчета...")
        result = download_report_from_link(link)
        
        if not result:
            logger.error("❌ Не удалось получить данные отчёта")
            return
        
        logger.info("\n📤 Шаг 3: Отправка отчета...")
        send_text_to_telegram(result["message"])
        
        logger.info(f"✅ Найдено сотрудников: {result['count']}")
        logger.info("=" * 60)
        logger.info("🎉 ПАРСИНГ ВЫПОЛНЕН УСПЕШНО!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        logger.error(traceback.format_exc())

# ===================================================
#  FLASK
# ===================================================

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'message': 'SBIS Parser is running!'})

@app.route('/run')
def run():
    thread = threading.Thread(target=run_parser)
    thread.start()
    return jsonify({'status': 'started'})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy'})

# ===================================================
#  ЗАПУСК
# ===================================================

if __name__ == "__main__":
    run_parser()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)