# GigaChat Нормализатор Наименований

## 📋 Описание

Сервис для нормализации наименований оборудования с помощью ИИ модели GigaChat. Принимает данные от 1С в формате JSON, извлекает артикулы и нормализует наименования оборудования по заданным правилам.

## 🚀 Как работает

1. **Получает данные** от 1С через POST запрос на `/normalize`
2. **Извлекает артикулы** из полного наименования по паттернам
3. **Нормализует наименования** с помощью GigaChat API
4. **Возвращает структурированный ответ** с нормализованными данными

## ⚙️ Установка и запуск

### 1. Сборка Docker образа
```bash
docker build -t gigachat-normalizer .
```

### 2. Создание файла окружения
Создайте файл `.env`:
```env
GIGACHAT_CREDENTIALS=ваш_ключ_GigaChat_API
```

### 3. Создание docker-compose.yml
```yaml
version: '3.8'
services:
  gigachat-service:
    image: gigachat-normalizer:latest
    ports:
      - "8090:8090"
    environment:
      - GIGACHAT_CREDENTIALS=${GIGACHAT_CREDENTIALS}
```

### 4. Запуск сервиса
```bash
docker-compose up -d
```

### 5. Запуск сервиса локально
```bash
docker run -d -p 8090:8090 -v ${PWD}/logs:/app/logs -e GIGACHAT_CREDENTIALS=NmYxZTE4ZWYtMGFlNy00NDI4LWFiZDItNjRkMjQ0ODFhYmExOmI5YzE5ZjdkLWVhNGMtNGFhMC1hMGI1LTgxMjFiZDhlYzZkZg== --name gigachat-service gigachat-normalizer
```

## 📡 Использование

### Проверка работоспособности
```bash
curl http://localhost:8090/health
```

### Нормализация данных
POST запрос на `http://localhost:8090/normalize` с данными от 1С в формате JSON.

## 🛠️ Основные эндпоинты

- `POST /normalize` - нормализация наименований
- `GET /health` - проверка состояния сервиса
- `GET /logs` - получение логов работы

## 📁 Структура проекта

- `server.py` - основной серверный код
- `Dockerfile` - конфигурация Docker образа
- `requirements.txt` - зависимости Python
- `russian_trusted_root_ca.cer` - SSL сертификат для GigaChat
- `server.log*` - Файл с логами сервиса для монтировки