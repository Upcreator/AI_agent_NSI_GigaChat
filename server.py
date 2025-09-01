import time
import json
import os
import re # Не забудьте импорты
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn
import logging
from gigachat import GigaChat

# Создаем директорию и файл с правильными правами
log_dir = '/app/logs'
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'server.log')

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация FastAPI приложения
app = FastAPI(title="GigaChat Normalization Service", version="1.0.0")

# Middleware для логирования всех запросов
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    body = await request.body()
    
    logger.info(f"REQUEST: {request.method} {request.url}")
    logger.info(f"HEADERS: {dict(request.headers)}")
    logger.info(f"BODY: {body.decode('utf-8') if body else 'No body'}")
    
    response = await call_next(request)
    
    process_time = time.time() - start_time
    logger.info(f"RESPONSE TIME: {process_time:.4f}s")
    
    return response

# Модели для ответа от GigaChat и результата
class ModelResponse(BaseModel):
    НормализованноеНаименование: str

class Result(BaseModel):
    mdm_key: str
    артикул: str
    наименование: str
    полное_наименование: str
    единица_измерения: str

class NormalizeResponse(BaseModel):
    results: list[Result]

# Модель для многоуровневого класса
class ClassLevel(BaseModel):
    уровень1: str
    уровень2: str
    уровень3: str

class ClassifyResult(BaseModel):
    mdm_key: str
    наименование: str
    полное_наименование: str
    класс: ClassLevel

class ClassifyResponse(BaseModel):
    results: list[ClassifyResult]

# Инициализация GigaChat
giga_chat = GigaChat(
    credentials=os.getenv("GIGACHAT_CREDENTIALS"),
    ca_bundle_file="russian_trusted_root_ca.cer"
)

def extract_field_manual(item_map: dict, field_name: str) -> str:
    """
    Извлекает значение поля из структуры jv8:Map вручную.
    item_map: словарь {"#type": "jv8:Map", "#value": [...]}
    field_name: имя поля для поиска (например, "ПолноеНаименование")
    """
    try:
        # Проверяем тип корневого элемента
        if item_map.get("#type") != "jv8:Map":
            logger.warning("Item is not a jv8:Map")
            return ""

        # Получаем список пар ключ-значение
        key_value_list = item_map.get("#value", [])
        if not isinstance(key_value_list, list):
             logger.warning("Item #value is not a list")
             return ""

        for kv_pair in key_value_list:
            # Проверяем структуру пары
            if not isinstance(kv_pair, dict) or "Key" not in kv_pair or "Value" not in kv_pair:
                continue

            key_obj = kv_pair["Key"]
            value_obj = kv_pair["Value"]

            # Проверяем структуру ключа
            if not isinstance(key_obj, dict) or key_obj.get("#type") != "jxs:string":
                continue

            key_value = key_obj.get("#value", "")
            if key_value != field_name:
                continue

            # Проверяем структуру значения
            if isinstance(value_obj, dict):
                value_type = value_obj.get("#type")
                if value_type == "jxs:string":
                    return value_obj.get("#value", "")
                elif value_type == "jv8:Null" or value_obj is None:
                    return ""
                else:
                     logger.info(f"Found field '{field_name}' with non-string type: {value_type}. Value object: {value_obj}")
                     # Для простоты возвращаем пустую строку для неизвестных типов
                     return ""
            else:
                 # Неожиданная структура значения
                 logger.warning(f"Value for field '{field_name}' is not a dict: {value_obj}")
                 return ""

        # Поле не найдено
        return ""
    except Exception as e:
        logger.error(f"Error extracting field '{field_name}' manually: {e}", exc_info=True)
        return ""


def generate_prompt(full_name: str) -> str:
    """Генерирует промпт для GigaChat"""
    return f'''Ты — ассистент по нормализации наименований оборудования.
Приведи входную строку к стандартизованному виду по правилам:

1. Формат: [Тип оборудования] [Производитель] [Модель] [Ключевые характеристики]
2. Удали:
   - Цвет
   - Стандартные функции (USB, Wi-Fi и т.д.)
   - Технические детали в скобках (если не критичны)
   - Повторяющиеся характеристики
3. Сокращения:
   - МФУ вместо Многофункциональное устройство
   - ИБП вместо Источник бесперебойного питания
   - Гб, ГГц, Вт, дюйм
4. Регистр: Каждое слово с заглавной буквы

Строго верни JSON в формате:
{{"НормализованноеНаименование": "результат"}}

Вход: "{full_name}"
Выход:'''

def generate_classify_prompt(full_name: str, name: str) -> str:
    """Генерирует промпт для многоуровневой классификации"""
    combined = f"{full_name} {name}".strip()
    
    return f'''Ты — эксперт по классификации товаров.
Определи класс оборудования по наименованию.

Используй следующую иерархию:

1 уровень:
- ИТ Оборудование
- Оборудование для передачи электрической энергии, сигналов и информации
- Прочее

2 уровень (в зависимости от 1):
- Под ИТ Оборудование:
  - Орттехника
  - Компьютерная техника
  - Сетевое оборудование
  - Прочее
- Под Оборудование для передачи энергии:
  - Кабельно-проводниковая продукция
  - Прочее

3 уровень (в зависимости от 2):
- Под Орттехника:
  - МФУ (Многофункциональные устройства)
- Под Компьютерная техника:
  - Моноблоки
  - Мониторы
- Под Кабельно-проводниковая продукция:
  - Кабели и провода общепромышленные
- Под Прочее:
  - Прочее

Верни строго JSON в формате:
{{
  "уровень1": "название первого уровня",
  "уровень2": "название второго уровня",
  "уровень3": "название третьего уровня"
}}

Наименование: "{combined}"
Ответ:'''

def query_gigachat_model(prompt: str) -> ModelResponse:
    """Запрашивает модель GigaChat"""
    try:
        logger.info(f"Sending prompt to GigaChat: {prompt}")
        response = giga_chat.chat(prompt)
        content = response.choices[0].message.content.strip()
        logger.info(f"Received response from GigaChat: {content}")
        
        # Извлекаем JSON из ответа
        import re
        json_match = re.search(r'\{[^{}]*\}', content)
        if json_match:
            json_str = json_match.group(0)
            # Заменяем экранированные кавычки
            json_str = json_str.replace('\\"', '"')
            data = json.loads(json_str)
            return ModelResponse(**data)
        else:
            logger.warning(f"No JSON found in response: {content}")
            return ModelResponse(НормализованноеНаименование="")
    except Exception as e:
        logger.error(f"Error querying GigaChat: {e}")
        # Возвращаем пустой результат в случае ошибки
        return ModelResponse(НормализованноеНаименование="")

def extract_article_from_full_name(full_name: str) -> str:
    """Извлекает артикул из полного наименования"""
    if not full_name:
        return ""
    
    # Паттерны для артикулов
    article_pattern = re.compile(r'(?i)(?:арт[.\s]*|код[.\s]*|art[.\s]*)([\w\d-]{3,20})')
    model_pattern = re.compile(r'\b([A-Z\d][A-Z\d-]{4,20}[A-Z\d])\b')
    sku_pattern = re.compile(r'\b([A-Z]{2,5}\d{3,}[A-Z\d]*)\b')
    bracket_pattern = re.compile(r'[\[(]([^\]()]{5,30})[\])]')
    
    # Фильтры
    resolution_pattern = re.compile(r'\b\d{3,4}[xх*]\d{3,4}\b')
    ratio_pattern = re.compile(r'\b\d+[:/]\d+\b')
    measurement_pattern = re.compile(r'(?i)\b\d+[.,]?\d*\s*(?:гц|ghz|кг|kg|см|см|дюйм|in|мм|mm|м|m)\b')
    
    def is_valid_article(candidate: str) -> bool:
        if len(candidate) < 5 or len(candidate) > 30:
            return False
        if resolution_pattern.search(candidate):
            return False
        if ratio_pattern.search(candidate):
            return False
        if measurement_pattern.search(candidate.lower()):
            return False
        if ' ' in candidate or '/' in candidate:
            return False
        
        has_letter = any(c.isalpha() for c in candidate)
        has_digit = any(c.isdigit() for c in candidate)
        return has_letter and has_digit
    
    def normalize_article(article: str) -> str:
        return re.sub(r'[.\s]', '', article.upper())
    
    # Приоритет 1: Явное указание артикула
    match = article_pattern.search(full_name)
    if match:
        candidate = match.group(1).strip()
        if is_valid_article(candidate):
            return normalize_article(candidate)
    
    # Приоритет 2: Модели оборудования
    model_matches = model_pattern.findall(full_name)
    for candidate in reversed(model_matches): # Ищем с конца, более вероятно, что модель в конце
        if is_valid_article(candidate):
            return normalize_article(candidate)
    
    # Приоритет 3: SKU-подобные артикулы
    sku_matches = sku_pattern.findall(full_name)
    for candidate in reversed(sku_matches):
        if is_valid_article(candidate):
            return normalize_article(candidate)
    
    # Приоритет 4: Содержимое скобок
    bracket_matches = bracket_pattern.findall(full_name)
    for candidate in reversed(bracket_matches):
        if is_valid_article(candidate):
            return normalize_article(candidate)
    
    return ""

def query_gigachat_classify(prompt: str) -> dict:
    """Запрашивает GigaChat для классификации (многоуровневая)"""
    try:
        logger.info(f"Sending classification prompt to GigaChat: {prompt}")
        response = giga_chat.chat(prompt)
        content = response.choices[0].message.content.strip()
        logger.info(f"Received classification response: {content}")

        # Ищем JSON, возможно обернутый в markdown
        json_match = re.search(r'```(?:json)?\s*({[^{}]*(?:{[^{}]*}[^{}]*)*})\s*```', content)
        if not json_match:
            # Если нет markdown, ищем просто JSON
            json_match = re.search(r'({[^{}]*(?:{[^{}]*}[^{}]*)*})', content)
            
        if json_match:
            json_str = json_match.group(1)
            # Заменяем экранированные кавычки
            json_str = json_str.replace('\\"', '"')
            data = json.loads(json_str)
            # Добавляем фолбеки, если поля пустые
            default = {"уровень1": "Прочее", "уровень2": "Прочее", "уровень3": "Прочее"}
            return {k: v or default[k] for k, v in data.items()}
        else:
            logger.warning(f"No JSON found in classification response: {content}")
            return {"уровень1": "Прочее", "уровень2": "Прочее", "уровень3": "Прочее"}
    except Exception as e:
        logger.error(f"Error in classification query: {e}", exc_info=True)
        return {"уровень1": "Прочее", "уровень2": "Прочее", "уровень3": "Прочее"}
        
# Обновленный обработчик POST
@app.post("/normalize") # Убираем response_model из декоратора, обрабатываем вручную
async def normalize_content(request: Request): # Принимаем Request напрямую
    """
    Нормализует содержимое с помощью GigaChat,
    возвращает структурированный ответ.
    Обрабатывает тело запроса вручную, чтобы соответствовать структуре JSON от 1С.
    """
    logger.info("Processing normalization request with structured content (manual parsing)")

    try:
        # Получаем тело запроса в виде словаря
        body_json = await request.json()
        logger.debug(f"Parsed request body JSON: {body_json}")

        # Проверяем корневой ключ
        if "#value" not in body_json or not isinstance(body_json["#value"], list):
            logger.error("Invalid request body structure: missing or invalid #value list")
            raise HTTPException(status_code=400, detail="Invalid request body structure: missing or invalid #value list")

        items_list = body_json["#value"]
        results = []

        # Итерируемся по элементам списка #value
        for i, item_map in enumerate(items_list):
            if not isinstance(item_map, dict) or item_map.get("#type") != "jv8:Map":
                 logger.warning(f"Skipping item {i}, not a valid jv8:Map: {item_map}")
                 continue # Или можно прервать, если ожидается строго jv8:Map

            # Извлекаем поля вручную
            mdm_key = extract_field_manual(item_map, "mdm_key") or ""
            full_name = extract_field_manual(item_map, "ПолноеНаименование") or ""
            name = extract_field_manual(item_map, "Наименование") or ""
            unit = extract_field_manual(item_map, "ЕдиницаИзмерения") or ""

            logger.info(f"Processing item {i+1}: mdm_key={mdm_key}, full_name='{full_name}', name='{name}', unit='{unit}'")

            # Базовая структура результата
            result = Result(
                mdm_key=mdm_key,
                артикул="",
                наименование=name, # Используем извлеченное "Наименование"
                полное_наименование=full_name,
                единица_измерения=unit
            )

            # Извлекаем артикул из полного наименования
            if full_name:
                article = extract_article_from_full_name(full_name)
                if article:
                    result.артикул = article
                    logger.info(f"Found article in full name: '{article}'")

            # Используем GigaChat для нормализации НАИМЕНОВАНИЯ (на основе ПолноеНаименование)
            if full_name:
                prompt = generate_prompt(full_name)
                llm_result = query_gigachat_model(prompt)
                if llm_result.НормализованноеНаименование:
                    # Обновляем НАИМЕНОВАНИЕ (поле "наименование" в результате)
                    result.наименование = llm_result.НормализованноеНаименование

                    # Если артикул не найден ранее, ищем в нормализованном наименовании
                    if not result.артикул:
                         article_from_normalized = extract_article_from_full_name(llm_result.НормализованноеНаименование)
                         if article_from_normalized:
                             result.артикул = article_from_normalized
                             logger.info(f"Found article in normalized name: '{article_from_normalized}'")

            # Фолбэк для артикула (если не найден ни по одному методу)
            if not result.артикул and full_name:
                 fallback_article = extract_article_from_full_name(full_name)
                 if fallback_article and not result.артикул:
                      result.артикул = fallback_article
                      logger.info(f"Fallback extracted article: '{result.артикул}'")


            logger.info(f"Final Result for item {i+1}: mdm_key={result.mdm_key}, article={result.артикул}, normalized_name='{result.наименование}', full_name='{result.полное_наименование}', unit='{result.единица_измерения}'")
            results.append(result)

        # Возвращаем результат, используя модель ответа Pydantic для структуры
        final_response = NormalizeResponse(results=results)
        logger.info(f"Successfully processed {len(results)} items.")
        return final_response # FastAPI автоматически сериализует Pydantic модель в JSON

    except json.JSONDecodeError as e:
        error_msg = f"Ошибка декодирования JSON тела запроса: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        error_msg = f"Ошибка при нормализации: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/classify", response_model=ClassifyResponse)
async def classify_content(request: Request):
    """
    Классифицирует оборудование по наименованию с помощью GigaChat (трёхуровнево).
    """
    logger.info("Processing classification request")

    try:
        body_json = await request.json()
        if "#value" not in body_json or not isinstance(body_json["#value"], list):
            raise HTTPException(status_code=400, detail="Invalid request body structure")

        items_list = body_json["#value"]
        results = []

        for i, item_map in enumerate(items_list):
            if not isinstance(item_map, dict) or item_map.get("#type") != "jv8:Map":
                logger.warning(f"Skipping item {i}, not a valid jv8:Map")
                continue

            mdm_key = extract_field_manual(item_map, "mdm_key") or ""
            full_name = extract_field_manual(item_map, "ПолноеНаименование") or ""
            name = extract_field_manual(item_map, "Наименование") or ""

            logger.info(f"Classifying item {i+1}: mdm_key={mdm_key}, full_name='{full_name}', name='{name}'")

            classify_result = query_gigachat_classify(generate_classify_prompt(full_name, name))

            results.append(ClassifyResult(
                mdm_key=mdm_key,
                наименование=name,
                полное_наименование=full_name,
                класс=ClassLevel(**classify_result)
            ))

        return ClassifyResponse(results=results)

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"Classification error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    logger.info("Health check requested")
    return {"status": "healthy"}

@app.get("/logs")
async def get_logs():
    try:
        with open('server.log', 'r', encoding='utf-8') as f:
            lines = f.readlines()
            return {"logs": lines[-50:] if len(lines) > 50 else lines}
    except FileNotFoundError:
        return {"logs": ["Log file not found"]}

if __name__ == "__main__":
    logger.info("Starting GigaChat Normalization Service on port 8090")
    uvicorn.run(app, host="0.0.0.0", port=8090)