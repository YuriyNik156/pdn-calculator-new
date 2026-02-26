from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Dict, Optional
import os
import json
import pandas as pd
import requests

app = FastAPI()

# --------------------------
# Подключение шаблонов и статики
# --------------------------
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --------------------------
# Пути и файлы
# --------------------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
LOCAL_JSON = os.path.join(DATA_DIR, "regions_wages.json")

# --------------------------
# Расчет ПДН
# --------------------------
def calculate_pdn(monthly_income: float, monthly_payments: list[float]) -> float:
    """Расчет показателя долговой нагрузки (ПДН)."""
    if monthly_income <= 0:
        raise ValueError("Доход должен быть больше 0")

    total_payments = sum(monthly_payments)
    pdn = (total_payments / monthly_income) * 100
    return round(pdn, 2)

# --------------------------
# Работа с данными регионов
# --------------------------
def try_load_from_local() -> Optional[Dict[str, float]]:
    """Попытка загрузить JSON из локального кэша."""
    if os.path.exists(LOCAL_JSON):
        with open(LOCAL_JSON, "r", encoding="utf8") as f:
            return json.load(f)
    return None

def save_local(data: Dict[str, float]) -> None:
    """Сохранение JSON в локальный кэш."""
    with open(LOCAL_JSON, "w", encoding="utf8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_from_rosstat_html(url: str) -> Optional[Dict[str, float]]:
    """Парсинг таблицы с Росстата."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(resp.text)
        for df in tables:
            cols = " ".join(map(str, df.columns)).lower()
            if "регион" in cols or "субъект" in cols:
                region_col = wage_col = None
                for c in df.columns:
                    c_str = str(c).lower()
                    if "регион" in c_str or "субъект" in c_str:
                        region_col = c
                    if "зарплат" in c_str or "зараб" in c_str:
                        wage_col = c
                if not region_col or not wage_col:
                    continue
                result = {}
                for _, row in df.iterrows():
                    try:
                        w = float(str(row[wage_col]).replace("\u202f", "").replace(" ", "").replace(",", "."))
                        result[str(row[region_col]).strip()] = round(w, 2)
                    except Exception:
                        continue
                if result:
                    return result
    except Exception:
        return None
    return None

def load_regions_data() -> Dict[str, float]:
    """
    Загружаем данные в порядке:
    1) Excel Росстата (если есть),
    2) локальный JSON (если есть),
    3) fallback.
    """
    excel_path = os.path.join(DATA_DIR, "rosstat_data_regions.xlsx")

    # 1. Пробуем загрузить Excel
    if os.path.exists(excel_path):
        try:
            # Данные начинаются с первой строки, но первый ряд — это месяцы
            df = pd.read_excel(excel_path, header=1)

            # Названия колонок
            region_col = df.columns[0]  # 'Unnamed: 0'

            wage_col = None
            for c in df.columns:
                if "июль" in str(c).lower():
                    wage_col = c
                    break

            if wage_col is None:
                wage_col = df.columns[-1]

            result = {}
            for _, row in df.iterrows():
                region = str(row[region_col]).strip()
                wage = row[wage_col]
                if pd.isna(region) or pd.isna(wage):
                    continue
                try:
                    wage_value = float(str(wage).replace(" ", "").replace(",", "."))
                    if "округ" not in region.lower() and "российская" not in region.lower():
                        result[region] = round(wage_value, 2)
                except:
                    continue

            if result:
                print(f"✅ Загружено из Excel: {len(result)} регионов ({wage_col})")
                save_local(result)
                return result

        except Exception as e:
            print(f"⚠️ Ошибка при чтении Excel: {e}")

    # 2. Пробуем локальный JSON
    local = try_load_from_local()
    if local:
        return local

    # 3. Fallback — базовые значения
    fallback = {
        "Белгородская область": 75834,
        "Владимирская область": 73240,
        "Нижегородская область": 81230,
        "Москва": 178596,
        "Московская область": 115811,
    }
    save_local(fallback)
    return fallback

# Загружаем данные при старте
REGION_WAGES = load_regions_data()

# --------------------------
# API-эндпоинты
# --------------------------
@app.get("/regions")
async def get_regions():
    """Возвращает список регионов и зарплат в JSON."""
    data = [{"region": r, "wage": w} for r, w in sorted(REGION_WAGES.items())]
    return JSONResponse(content=data)

# --------------------------
# Веб-интерфейс
# --------------------------
@app.get("/", response_class=HTMLResponse)
async def form_page(request: Request):
    """Главная страница калькулятора."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "result": None,
            "status": None,
            "regions": sorted(REGION_WAGES.keys()),
        },
    )

@app.post("/", response_class=HTMLResponse)
async def calculate_pdn_form(
    request: Request,
    income: float = Form(...),
    payments: str = Form(...),
    region: Optional[str] = Form(None),
):
    """Обработка формы и расчет ПДН."""
    try:
        if region and region in REGION_WAGES:
            income = REGION_WAGES[region]

        payments_list = [float(p.strip()) for p in payments.split(",") if p.strip()]
        pdn_value = calculate_pdn(income, payments_list)

        if pdn_value < 50:
            status = "✅ У вас низкий уровень долговой нагрузки."
        elif pdn_value < 80:
            status = "⚖️ Уровень долговой нагрузки — средний."
        else:
            status = "⚠️ У вас высокая долговая нагрузка. Стоит пересмотреть кредиты."

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": f"ПДН = {pdn_value}%",
                "status": status,
                "regions": sorted(REGION_WAGES.keys()),
                "selected_region": region,
                "income": income,
            },
        )

    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "result": "Ошибка: проверьте введённые данные.",
                "status": str(e),
                "regions": sorted(REGION_WAGES.keys()),
            },
        )
