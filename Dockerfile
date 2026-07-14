FROM selenium/standalone-chrome:latest

# Переключаемся на root
USER root

# Устанавливаем Python
RUN apt-get update && apt-get install -y python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Устанавливаем Python зависимости
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Копируем код
COPY app.py .

# Возвращаемся к пользователю seluser
USER seluser

# Запускаем
CMD ["python3", "app.py"]