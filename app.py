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

# Используем /tmp для загрузок и логов
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
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ===================================================

def setup_download_dir():
    for directory in [DOWNLOAD_DIR, LOG_DIR]:
        try:
            os.makedirs(directory, exist_ok=True)
            os.chmod(directory, 0o777)
            logger.info(f"✅ Создана папка: {directory}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось создать папку {directory}: {e}")

def get_yesterday_date():
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%d.%m.%Y"), yesterday.strftime("%Y-%m-%d")

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

# ===================================================
#  ПОЧТА (ВАША РАБОЧАЯ ФУНКЦИЯ)
# ===================================================

def get_sbis_download_link():
    """Находит письмо с отчётом и извлекает ссылку."""
    try:
        logger.info("Подключаемся к почте...")
        mail = imaplib.IMAP4_SSL(MAIL_CONFIG['imap_server'])
        mail.login(MAIL_CONFIG['email'], MAIL_CONFIG['password'])
        mail.select('INBOX')

        status, messages = mail.search(None, 'FROM "saby.ru"')
        if status != 'OK':
            logger.info("Писем от СБИС не найдено.")
            return None

        for msg_id in messages[0].split():
            status, msg_data = mail.fetch(msg_id, '(RFC822)')
            if status != 'OK':
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_header(msg['Subject'])[0][0]
            if isinstance(subject, bytes):
                subject = subject.decode('utf-8')
            logger.info(f"Найдено письмо: {subject}")

            if "ВЫРУЧКА" not in subject:
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
                    logger.info(f"Найдена ссылка в HTML: {link}")
                    mail.store(msg_id, '+FLAGS', '\\Seen')
                    mail.close()
                    mail.logout()
                    return link

            text_links = re.findall(r'https?://[^\s<>"]+', body_text)
            for link in text_links:
                if 'disk.sbis.ru' in link or 'online.sbis.ru/disk' in link:
                    logger.info(f"Найдена ссылка в тексте: {link}")
                    mail.store(msg_id, '+FLAGS', '\\Seen')
                    mail.close()
                    mail.logout()
                    return link

            logger.warning("Ссылка на отчёт в письме не найдена")

        mail.close()
        mail.logout()
        return None
    except Exception as e:
        logger.error(f"Ошибка при проверке почты: {e}")
        return None

# ===================================================
#  ПАРСИНГ ДАННЫХ
# ===================================================

def parse_report_from_page(driver):
    """Парсит данные по сотрудникам со страницы отчёта."""
    logger.info("🔍 Парсим данные со страницы...")
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
#  ОСНОВНАЯ ФУНКЦИЯ С ВХОДОМ (ВАШ РАБОЧИЙ КОД)
# ===================================================

def download_report_from_link(download_link):
    """Открывает страницу с отчётом и парсит данные."""
    logger.info("🚀 Открываем страницу с отчётом...")

    options = webdriver.ChromeOptions()
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
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--headless=new")

    driver = None
    try:
        # Используем прямой путь к ChromeDriver (установлен в Docker)
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        driver.implicitly_wait(15)

        logger.info("Открываем сайт СБИС...")
        driver.get("https://online.sbis.ru/")
        time.sleep(3)

        # ---------- ДВУХЭТАПНАЯ АВТОРИЗАЦИЯ ----------
        try:
            if "login" in driver.current_url or "auth" in driver.current_url:
                logger.info("Выполняем вход...")
                
                # ШАГ 1: Вводим логин и нажимаем Enter
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
                    
                    # Нажимаем Enter, чтобы появилось поле пароля
                    login_input.send_keys(Keys.RETURN)
                    logger.info("🔄 Нажат Enter для отображения поля пароля")
                    time.sleep(3)
                    
                    # ШАГ 2: Вводим пароль
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
                        
                        # ШАГ 3: Нажимаем Enter или кнопку "Войти"
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

        # Переходим по ссылке на отчёт
        logger.info(f"Переходим по ссылке: {download_link}")
        driver.get(download_link)
        time.sleep(5)
        
        # Парсим данные со страницы
        employees = parse_report_from_page(driver)
        
        if employees:
            date_str = get_yesterday_date()[0]
            message = format_report_for_telegram(employees, date_str)
            return {"message": message, "employees": employees, "count": len(employees)}
        else:
            logger.error("❌ Не удалось найти данные сотрудников на странице")
            driver.save_screenshot(os.path.join(DOWNLOAD_DIR, "page_screenshot.png"))
            logger.info("Сохранен скриншот страницы для анализа")
            return None

    except Exception as e:
        logger.error(f"❌ Ошибка в процессе: {e}")
        try:
            if driver:
                driver.save_screenshot(os.path.join(DOWNLOAD_DIR, "error_screenshot.png"))
                logger.info("Сохранен скриншот ошибки")
        except:
            pass
        return None
    finally:
        if driver:
            driver.quit()
            logger.info("Браузер закрыт")

# ===================================================
#  ОТПРАВКА В TELEGRAM
# ===================================================

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

# ===================================================
#  ОСНОВНАЯ ФУНКЦИЯ
# ===================================================

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