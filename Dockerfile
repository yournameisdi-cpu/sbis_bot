FROM python:3.11-slim

# Устанавливаем Firefox
RUN apt-get update && apt-get install -y \
    firefox-esr \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем GeckoDriver
RUN wget -q "https://github.com/mozilla/geckodriver/releases/download/v0.34.0/geckodriver-v0.34.0-linux64.tar.gz" \
    && tar -xzf geckodriver-v0.34.0-linux64.tar.gz \
    && mv geckodriver /usr/local/bin/ \
    && chmod +x /usr/local/bin/geckodriver \
    && rm geckodriver-v0.34.0-linux64.tar.gz

# Устанавливаем Python зависимости
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY app.py .

# Запускаем
CMD ["python", "app.py"]