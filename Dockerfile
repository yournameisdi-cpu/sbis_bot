FROM python:3.11-slim

# Устанавливаем все необходимые зависимости для Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxi6 \
    libxtst6 \
    libnss3 \
    libcups2 \
    libxss1 \
    libxrandr2 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm1 \
    libxshmfence1 \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Chrome
RUN wget -q "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb" \
    && dpkg -i google-chrome-stable_current_amd64.deb || apt-get -f install -y \
    && rm google-chrome-stable_current_amd64.deb

# Устанавливаем ChromeDriver
RUN wget -q "https://edgedl.me.gvt1.com/edgedl/chrome/chrome-for-testing/122.0.6261.128/linux64/chromedriver-linux64.zip" \
    && unzip chromedriver-linux64.zip \
    && mv chromedriver-linux64/chromedriver /usr/local/bin/ \
    && chmod +x /usr/local/bin/chromedriver \
    && rm -rf chromedriver-linux64.zip chromedriver-linux64

# Проверяем установку
RUN google-chrome --version && chromedriver --version

# Устанавливаем Python зависимости
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY app.py .

# Запускаем
CMD ["python", "app.py"]