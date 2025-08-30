# Используем официальный образ Python 3.12
FROM python:3.12-slim

# Установка метаданных
LABEL maintainer="Your Name"
LABEL version="1.0"
LABEL description="GigaChat Normalization Service"

# Создание непrivileged пользователя для безопасности
RUN useradd --create-home --shell /bin/bash app
USER app
WORKDIR /home/app

# Копируем файл зависимостей
COPY --chown=app:app requirements.txt .

# Установка зависимостей
RUN pip install --user --no-cache-dir -r requirements.txt

# Копируем исходный код приложения
COPY --chown=app:app . .

# Создаем директорию для сертификатов
RUN mkdir -p gigachat/certificate

# Копируем сертификат
COPY --chown=app:app russian_trusted_root_ca.cer gigachat/certificate/

# Создаем файл логов
RUN touch server.log

# Открываем порт
EXPOSE 8090

# Команда для запуска приложения
CMD ["python", "server.py"]