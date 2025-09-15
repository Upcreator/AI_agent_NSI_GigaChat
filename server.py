import time
import json
import os
import re
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import uvicorn
import logging
from gigachat import GigaChat

# Создаем директорию и файл с правильными правами
log_dir = './logs'
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

# Модель для классификации (только уровень 3 как строка)
class ClassifyResult(BaseModel):
    mdm_key: str = None
    наименование: str = None
    полное_наименование: str
    класс: str

class ClassifyResponse(BaseModel):
    results: list[ClassifyResult]

# Модели для обогащения данных
class EnrichmentRequest(BaseModel):
    ПолноеНаименование: str
    Класс: str
    Свойства: Dict[str, str]  # Изменено на Dict для передачи свойств

class EnrichmentResponse(BaseModel):
    ПолноеНаименование: str
    Класс: str
    Свойства: Dict[str, str]

# Таблица единиц измерения ОКЕИ
UNIT_CODES = {
    "006": "МЕТР",
    "055": "КВАДРАТНЫЙ МЕТР",
    "112": "ЛИТР",
    "113": "КУБИЧЕСКИЙ МЕТР",
    "114": "1000 КУБИЧЕСКИХ МЕТРОВ",
    "130": "1000 ЛИТРОВ",
    "162": "МЕТРИЧЕСКИЙ КАРАТ",
    "163": "ГРАММ",
    "166": "КИЛОГРАММ",
    "185": "ГРУЗОПОДЪЕМНОСТЬ В ТОННАХ",
    "246": "1000 КИЛОВАТТ-ЧАС",
    "305": "КЮРИ",
    "306": "ГРАММ ДЕЛЯЩИХСЯ ИЗОТОПОВ",
    "715": "ПАРА",
    "796": "ШТУКА",
    "797": "СТО ШТУК",
    "798": "ТЫСЯЧА ШТУК",
    "831": "ЛИТР ЧИСТОГО (100%) СПИРТА",
    "841": "КИЛОГРАММ ПЕРОКСИДА ВОДОРОДА",
    "845": "КИЛОГРАММ СУХОГО НА 90 % ВЕЩЕСТВА",
    "852": "КИЛОГРАММ ОКСИДА КАЛИЯ",
    "859": "КИЛОГРАММ ГИДРОКСИДА КАЛИЯ",
    "861": "КИЛОГРАММ АЗОТА",
    "863": "КИЛОГРАММ ГИДРОКСИДА НАТРИЯ",
    "865": "КИЛОГРАММ ПЯТИОКИСИ ФОСФОРА",
    "867": "КИЛОГРАММ УРАНА"
}

# Обратный словарь для поиска кода по наименованию
UNIT_NAMES_TO_CODES = {v: k for k, v in UNIT_CODES.items()}

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

def generate_enrichment_prompt(full_name: str, product_class: str, properties: Dict[str, str]) -> str:
    """Генерирует промпт для обогащения данных"""
    
    # Определяем примеры и инструкции в зависимости от класса
    class_specific_instructions = ""
    examples = ""
    
    if "кабел" in product_class.lower() or "провод" in product_class.lower():
        class_specific_instructions = """Для кабелей и проводов:
- Марка: указывается в наименовании, например КГтп-ЬТ
- Количество жил и размер сечения: указывается как числоxсечение, например 14x400 мм²
- Номинальное переменное напряжение: указывается в кВ
- Конструкция жилы: медная многопроволочная или медная однопроволочная
- Тип брони: стальная лента, стальная проволока, без брони
- Цвет изоляции: черный, белый, синий, желто-зеленый и т.д.
- Фасовка: указывается в метрах"""
        examples = """Примеры:
Наименование: "Кабель КГтп-ЬТ 14x400 1кВ бронированный с медными жилами" -> {"Марка": "КГтп-ЬТ", "Количество жил и размер сечения": "14x400 мм²", "Номинальное переменное напряжение (кВ)": "1", "Конструкция жилы": "Медная многопроволочная", "Тип брони": "Стальная лента", "Цвет изоляции": "Черный", "Фасовка (м)": "500"}"""
    
    elif "моноблок" in product_class.lower():
        class_specific_instructions = """Для моноблоков:
- Модель: указывается после названия производителя
- Диагональ экрана: указывается в дюймах
- Процессор: полное название процессора
- Оперативная память: объем в ГБ
- Накопитель: объем и тип накопителя (SSD, HDD)
- Тип матрицы: IPS, TN, VA
- Операционная система: Windows, macOS и версия
- Цвет корпуса: цвет корпуса устройства"""
        examples = """Примеры:
Наименование: "Моноблок MAXX Pro 27\" Intel Core i7-12700 16GB RAM 512GB SSD" -> {"Модель": "Pro 27", "Диагональ экрана": "27 дюймов", "Процессор": "Intel Core i7-12700", "Оперативная память": "16 ГБ", "Накопитель": "512 ГБ SSD", "Тип матрицы": "IPS", "Операционная система": "Windows 11 Pro", "Цвет корпуса": "Серый космос"}"""
    
    elif "монитор" in product_class.lower():
        class_specific_instructions = """Для мониторов:
- Модель: указывается после названия производителя
- Диагональ экрана: указывается в дюймах
- Разрешение экрана: полное разрешение и тип (4K UHD, Full HD и т.д.)
- Тип матрицы: IPS, TN, VA
- Частота обновления: указывается в Hz
- Форма экрана: плоский, изогнутый
- Порты подключения: перечисляются все доступные порты
- Яркость: указывается в кд/м²"""
        examples = """Примеры:
Наименование: "Монитор MAXX Vision 24\" 4K UHD VA 144Hz с изогнутым экраном" -> {"Модель": "Vision 24", "Диагональ экрана": "24 дюйма", "Разрешение экрана": "3840x2160 (4K UHD)", "Тип матрицы": "VA", "Частота обновления (Hz)": "144", "Форма экрана": "Изогнутый", "Порты подключения": "HDMI 2.1, DisplayPort 1.4, USB-C", "Яркость (кд/м²)": "350"}"""
    
    else:
        class_specific_instructions = "Анализируй наименование и извлекай запрошенные свойства"
        examples = "Примеры не предоставлены для данного класса"
    
    # Формируем список свойств для запроса
    properties_list = "\n".join([f"- {key}" for key in properties.keys()])
    
    # Формируем таблицу единиц измерения для промпта
    unit_table_lines = []
    for code, name in list(UNIT_CODES.items())[:15]:  # Показываем первые 15 записей
        unit_table_lines.append(f"{code};{name}")
    unit_table = "\n".join(unit_table_lines)
    
    return f'''Ты — эксперт по анализу товаров и оборудования.
Проанализируй наименование товара и класс, чтобы определить запрошенные свойства.

{class_specific_instructions}

Таблица единиц измерения (ОКЕИ):
{unit_table}
... (таблица продолжается)

Наименование товара: "{full_name}"
Класс товара: "{product_class}"

Запрошенные свойства:
{properties_list}

{examples}

Анализируй наименование внимательно и извлекай только те значения, которые можно однозначно определить из текста.
Если какое-то свойство не может быть определено, поставь "Не определено"
Если значение свойства не указано явно, но может быть вычислено или определено по контексту, укажи вычисленное значение.

Верни строго JSON в формате:
{{
  "Свойства": {{
    "Название свойства": "Значение свойства"
  }}
}}

Ответ:'''

def query_gigachat_model(prompt: str) -> ModelResponse:
    """Запрашивает модель GigaChat"""
    try:
        logger.info(f"Sending prompt to GigaChat: {prompt}")
        response = giga_chat.chat(prompt)
        content = response.choices[0].message.content.strip()
        logger.info(f"Received response from GigaChat: {content}")
        
        # Извлекаем JSON из ответа
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

def query_gigachat_enrichment(prompt: str) -> dict:
    """Запрашивает GigaChat для обогащения данных"""
    try:
        logger.info(f"Sending enrichment prompt to GigaChat")
        response = giga_chat.chat(prompt)
        content = response.choices[0].message.content.strip()
        logger.info(f"Received enrichment response: {content}")

        # Ищем JSON в ответе
        json_match = re.search(r'```(?:json)?\s*({[^{}]*(?:{[^{}]*}[^{}]*)*})\s*```', content)
        if not json_match:
            json_match = re.search(r'({[^{}]*(?:{[^{}]*}[^{}]*)*})', content)
            
        if json_match:
            json_str = json_match.group(1)
            json_str = json_str.replace('\\"', '"')
            
            # Очищаем JSON от комментариев и некорректных символов
            # Удаляем строки, содержащие комментарии
            lines = json_str.split('\n')
            cleaned_lines = []
            for line in lines:
                # Удаляем комментарии в стиле // и /*
                line = re.sub(r'//.*$', '', line)
                line = re.sub(r'/\*.*?\*/', '', line)
                # Удаляем непечатаемые символы
                line = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', line)
                cleaned_lines.append(line)
            
            cleaned_json = '\n'.join(cleaned_lines)
            
            # Попытка парсинга очищенного JSON
            try:
                data = json.loads(cleaned_json)
                return data.get("Свойства", {})
            except json.JSONDecodeError as e:
                logger.warning(f"JSON decode error after cleaning: {e}")
                # Если не удалось распарсить, попробуем извлечь свойства регулярными выражениями
                properties = {}
                # Ищем все пары ключ-значение
                prop_matches = re.findall(r'"([^"]+)"\s*:\s*"([^"]*)"', cleaned_json)
                for key, value in prop_matches:
                    # Удаляем комментарии из значений
                    clean_value = re.sub(r'//.*$', '', value).strip()
                    clean_value = re.sub(r'/\*.*?\*/', '', clean_value).strip()
                    # Удаляем кавычки в начале/конце если есть
                    clean_value = clean_value.strip('"')
                    # Если значение содержит "Не определено", заменяем на "Не определено"
                    if "не определ" in clean_value.lower() or clean_value == "":
                        properties[key] = "Не определено"
                    else:
                        properties[key] = clean_value
                return properties if properties else {}
        else:
            logger.warning(f"No JSON found in enrichment response: {content}")
            return {}
    except Exception as e:
        logger.error(f"Error in enrichment query: {e}", exc_info=True)
        return {}

# Обновленный обработчик POST
@app.post("/normalize")
async def normalize_content(request: Request):
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
                 continue

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
                наименование=name,
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

        # Возвращаем результат
        final_response = NormalizeResponse(results=results)
        logger.info(f"Successfully processed {len(results)} items.")
        return final_response

    except json.JSONDecodeError as e:
        error_msg = f"Ошибка декодирования JSON тела запроса: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        error_msg = f"Ошибка при нормализации: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)

@app.post("/classify")
async def classify_content(request: Request):
    """
    Классифицирует оборудование по наименованию с помощью GigaChat.
    Поддерживает обе структуры запроса: упрощённую и jv8:Map.
    Возвращает только уровень 3 классификации как строку.
    """
    logger.info("Processing classification request")

    try:
        body_json = await request.json()
        
        # Проверяем тип запроса
        if "ПолноеНаименование" in body_json:
            # Упрощённая структура
            logger.info("Processing simplified classification request structure")
            full_name = body_json.get("ПолноеНаименование", "")
            name = body_json.get("Наименование", "") or full_name
            mdm_key = body_json.get("mdm_key", "")
            
            logger.info(f"Classifying simplified item: mdm_key={mdm_key}, full_name='{full_name}', name='{name}'")
            
            classify_result = query_gigachat_classify(generate_classify_prompt(full_name, name))
            
            result = ClassifyResult(
                mdm_key=mdm_key,
                наименование=name,
                полное_наименование=full_name,
                класс=classify_result.get("уровень3", "Прочее")
            )
            
            return ClassifyResponse(results=[result])
            
        else:
            # Старая структура jv8:Map
            logger.info("Processing legacy jv8:Map classification request structure")
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
                    класс=classify_result.get("уровень3", "Прочее")
                ))

            return ClassifyResponse(results=results)

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error(f"Classification error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/enrichment", response_model=EnrichmentResponse)
async def enrichment_content(request: EnrichmentRequest):
    """
    Обогащает данные о товаре, извлекая дополнительные свойства с помощью GigaChat
    """
    logger.info(f"Processing enrichment request for: {request.ПолноеНаименование}")
    
    try:
        full_name = request.ПолноеНаименование
        product_class = request.Класс
        properties = request.Свойства
        
        logger.info(f"Enriching item: full_name='{full_name}', class='{product_class}', properties={list(properties.keys())}")
        
        # Генерируем промпт для LLM
        prompt = generate_enrichment_prompt(full_name, product_class, properties)
        
        # Запрашиваем у GigaChat
        enriched_properties = query_gigachat_enrichment(prompt)
        
        # Форматируем результат как один объект со всеми свойствами
        result_properties = {}
        for prop_name in properties.keys():
            value = enriched_properties.get(prop_name, "Не определено")
            # Если значение пустое или содержит "не определ", ставим "Не определено"
            if not value or "не определ" in str(value).lower():
                value = "Не определено"
            result_properties[prop_name] = value
        
        # Добавляем единицу измерения и производителя по умолчанию если они не определены
        if "Единица измерения" in properties and result_properties.get("Единица измерения") == "Не определено":
            # Логика по умолчанию для разных классов
            if "кабел" in product_class.lower() or "провод" in product_class.lower():
                result_properties["Единица измерения"] = "006"  # Метр для кабелей
            elif "штука" in full_name.lower() or "комплект" in full_name.lower():
                result_properties["Единица измерения"] = "796"  # Штука
            else:
                result_properties["Единица измерения"] = "796"  # По умолчанию штука
        
        if "Производитель" in properties and result_properties.get("Производитель") == "Не определено":
            # Пытаемся найти производителя в наименовании
            words = full_name.split()
            if len(words) > 1:
                potential_manufacturer = words[1]  # Обычно производитель идет после типа товара
                # Простая проверка - если слово состоит из букв и не является стандартными терминами
                if (potential_manufacturer.isalpha() and 
                    potential_manufacturer.lower() not in ["кгтп", "кг", "ввг", "пвс", "шввп", "кгт", "тьт"] and
                    len(potential_manufacturer) > 2):
                    result_properties["Производитель"] = potential_manufacturer
                # Если не нашли в слове 1, пробуем слово 0 (если оно не "Кабель")
                elif len(words) > 2 and words[0].lower() == "кабель":
                    potential_manufacturer = words[2]
                    if (potential_manufacturer.isalpha() and 
                        potential_manufacturer.lower() not in ["кгтп", "кг", "ввг", "пвс", "шввп", "кгт", "тьт"] and
                        len(potential_manufacturer) > 2):
                        result_properties["Производитель"] = potential_manufacturer
        
        response = EnrichmentResponse(
            ПолноеНаименование=full_name,
            Класс=product_class,
            Свойства=result_properties
        )
        
        logger.info(f"Enrichment completed: {response}")
        return response
        
    except Exception as e:
        error_msg = f"Error during enrichment: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)

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