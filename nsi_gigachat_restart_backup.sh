#!/bin/bash

# Конфигурация
DOCKER_COMPOSE_DIR="/home/rbrol/AI_agent_NSI_GigaChat"
BASE_URL="http://localhost:8090"
CHECK_INTERVAL=30  # 30 секунд для теста
TIMEOUT=10         # Таймаут для curl запроса
STARTUP_WAIT=30    # Время ожидания после запуска сервиса
BACKUP_DIR="/home/rbrol/AI_agent_NSI_GigaChat/backup"  # Директория для бэкапов
CONTAINER_NAME="ai_agent_nsi_gigachat-gigachat-service-1"  # Имя контейнера
BACKUP_INTERVAL=20  # 1 час в секундах (3600 = 1 час, можно изменить для тестов)

# Создаем директорию для бэкапов если её нет
mkdir -p "$BACKUP_DIR"

# Переменная для отслеживания времени последнего бэкапа
last_backup_time=0

# Функция для логирования
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Функция для сохранения логов сервиса
backup_logs() {
    local backup_filename="$BACKUP_DIR/$(date '+%Y%m%d_%H%M%S').log"
    log_message "Сохранение логов сервиса в $backup_filename..."

    # Проверяем, существует ли контейнер
    if docker ps -a --format '{{.Names}}' | grep -q "^$CONTAINER_NAME$"; then
        # Копируем логи из контейнера
        if docker cp "$CONTAINER_NAME:/app/server.log" "$backup_filename" 2>/dev/null; then
            log_message "Логи успешно сохранены в $backup_filename"
        else
            # Если не удалось скопировать файл, получаем логи через docker logs
            log_message "Не удалось скопировать server.log, получаем логи через docker logs..."
            docker logs "$CONTAINER_NAME" > "$backup_filename" 2>&1
            log_message "Логи docker сохранены в $backup_filename"
        fi
    else
        log_message "Контейнер $CONTAINER_NAME не найден, пропускаем сохранение логов"
    fi
}

# Функция для проверки и выполнения бэкапа по расписанию
check_and_backup() {
    local current_time=$(date +%s)
    local time_since_last_backup=$((current_time - last_backup_time))

    if [ $time_since_last_backup -ge $BACKUP_INTERVAL ]; then
        log_message "Пора делать регулярный бэкап логов (прошло $((time_since_last_backup / 60)) минут)"
        backup_logs
        last_backup_time=$current_time
    fi
}

# Функция для мягкий перезапуск сервиса (только restart)
soft_restart() {
    log_message "Мягкий перезапуск сервиса через docker-compose restart..."
    cd "$DOCKER_COMPOSE_DIR"

    # Сохраняем логи перед перезапуском
    backup_logs

    if command -v docker-compose &> /dev/null; then
        docker-compose restart
        return $?
    elif command -v docker &> /dev/null && docker compose version &> /dev/null; then
        docker compose restart
        return $?
    else
        log_message "ОШИБКА: Не найдены команды docker-compose или docker compose"
        return 1
    fi
}

# Функция для полного перезапуска сервиса (down и up)
hard_restart() {
    log_message "Полный перезапуск сервиса через docker-compose down и up..."
    cd "$DOCKER_COMPOSE_DIR"

    # Сохраняем логи перед остановкой
    backup_logs

    if command -v docker-compose &> /dev/null; then
        docker-compose down
        sleep 5  # Небольшая пауза перед запуском
        docker-compose up -d
        return $?
    elif command -v docker &> /dev/null && docker compose version &> /dev/null; then
        docker compose down
        sleep 5  # Небольшая пауза перед запуском
        docker compose up -d
        return $?
    else
        log_message "ОШИБКА: Не найдены команды docker-compose или docker compose"
        return 1
    fi
}

# Инициализация времени последнего бэкапа
last_backup_time=$(date +%s)
log_message "Скрипт запущен. Первый бэкап будет через $((BACKUP_INTERVAL / 60)) минут"

# Бесконечный цикл мониторинга
while true; do
    log_message "Проверка состояния сервиса по $BASE_URL/health..."

    # Отправляем запрос к health endpoint
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout $TIMEOUT "$BASE_URL/health" 2>/dev/null)

    if [ "$RESPONSE" = "200" ]; then
        log_message "Сервис работает нормально (HTTP $RESPONSE)"
        # Проверяем необходимость регулярного бэкапа
        check_and_backup
    else
        log_message "Сервис НЕДОСТУПЕН (HTTP $RESPONSE). Попытка восстановления..."

        # Сначала пробуем мягкий перезапуск
        if soft_restart; then
            log_message "Мягкий перезапуск выполнен. Ожидание $STARTUP_WAIT секунд..."
            sleep $STARTUP_WAIT

            # Проверяем, помогло ли
            RESPONSE_AFTER_SOFT=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout $TIMEOUT "$BASE_URL/health" 2>/dev/null)
            if [ "$RESPONSE_AFTER_SOFT" = "200" ]; then
                log_message "Мягкий перезапуск помог. Сервис работает (HTTP $RESPONSE_AFTER_SOFT)"
                sleep $((CHECK_INTERVAL - STARTUP_WAIT))  # Корректируем общее время ожидания
                continue
            else
                log_message "Мягкий перезапуск не помог (HTTP $RESPONSE_AFTER_SOFT). Пробуем полный перезапуск..."
            fi
        else
            log_message "Мягкий перезапуск не удался. Пробуем полный перезапуск..."
        fi

        # Если мягкий перезапуск не помог, пробуем полный
        if hard_restart; then
            log_message "Полный перезапуск выполнен. Ожидание $STARTUP_WAIT секунд..."
            sleep $STARTUP_WAIT

            # Проверяем, помогло ли
            RESPONSE_AFTER_HARD=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout $TIMEOUT "$BASE_URL/health" 2>/dev/null)
            if [ "$RESPONSE_AFTER_HARD" = "200" ]; then
                log_message "Полный перезапуск помог. Сервис работает (HTTP $RESPONSE_AFTER_HARD)"
            else
                log_message "Полный перезапуск выполнен, но сервис все еще недоступен (HTTP $RESPONSE_AFTER_HARD)"
            fi
        else
            log_message "ОШИБКА: Не удалось выполнить ни мягкий, ни полный перезапуск"
        fi
    fi

    # Ждем заданное время до следующей проверки
    log_message "Ожидание $((CHECK_INTERVAL / 60)) минут до следующей проверки..."
    sleep $CHECK_INTERVAL
done