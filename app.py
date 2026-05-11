import streamlit as st
import pandas as pd
import sqlite3
import zipfile
import io
import plotly.express as px

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="Ecom Insight Pro", layout="centered")

# --- БАЗА ДАННЫХ ---
DB_NAME = "ecom_data.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute('CREATE TABLE IF NOT EXISTS units (sku TEXT PRIMARY KEY, cost REAL)')
    conn.close()


def get_costs():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql('SELECT * FROM units', conn)
    conn.close()
    return dict(zip(df['sku'].astype(str).str.strip(), df['cost']))


def save_cost(sku, cost):
    conn = sqlite3.connect(DB_NAME)
    conn.execute('INSERT OR REPLACE INTO units (sku, cost) VALUES (?, ?)', (str(sku).strip(), cost))
    conn.commit()
    conn.close()


# --- ФУНКЦИЯ ОЧИСТКИ ЧИСЕЛ ---
def to_num(s):
    if pd.isna(s) or s == '': return 0.0
    # Удаляем пробелы, заменяем запятые на точки, убираем лишние символы
    s_clean = str(s).replace(',', '.').replace('\xa0', '').replace(' ', '').strip()
    try:
        return float(pd.to_numeric(s_clean))
    except:
        return 0.0


# --- УЛУЧШЕННЫЙ ЗАГРУЗЧИК ФАЙЛОВ ---
def smart_read_file(content, filename):
    try:
        if filename.lower().endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='utf-8')
            except:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='cp1251')
        else:
            df = pd.read_excel(io.BytesIO(content))

        # Ищем строку с заголовками (проверяем первые 50 строк)
        target_row = -1
        markers = ["артикул", "баркод", "sku", "номер отправления", "seller_sku"]

        for i in range(min(len(df), 50)):
            row_values = [str(val).lower() for val in df.iloc[i].values]
            if any(m in " ".join(row_values) for m in markers):
                target_row = i
                break

        if target_row != -1:
            new_columns = df.iloc[target_row].values
            df = df.iloc[target_row + 1:].reset_index(drop=True)
            df.columns = [str(c).strip().lower() for c in new_columns]

        return df
    except:
        return None


# --- УЛУЧШЕННАЯ ОБРАБОТКА ОТЧЕТА (WB + OZON) ---
def process_report(df):
    if df is None or df.empty: return None

    costs = get_costs()
    cols = df.columns

    try:
        # Определяем маркетплейс
        is_wb = any(c in cols for c in ["обоснование для оплаты", "артикул поставщика"])
        is_ozon = any(c in cols for c in ["номер отправления", "начислено", "артикул"]) and not is_wb

        if is_wb:
            sku_candidates = ["артикул поставщика", "артикул", "sa", "barcode"]
            sku_col = next((c for c in cols if any(m == c for m in sku_candidates)), None)
            rev_col = next((c for c in cols if "к перечислению" in c or "возмещение" in c), None)
            log_col = next((c for c in cols if "логистика" in c or "услуги по доставке" in c), None)
            pen_col = next((c for c in cols if "штраф" in c), None)
            source = "Wildberries"
        elif is_ozon:
            # Расширенный поиск для Ozon
            sku_candidates = ["артикул", "артикул товара", "sku", "seller_sku", "offer_id"]
            sku_col = next((c for c in cols if any(m == c for m in sku_candidates)), None)
            rev_col = next((c for c in cols if "начислено" in c or "итого" in c or "сумма" in c), None)
            log_col = None  # В отчетах Ozon логистика часто идет отдельными строками или уже вычтена
            pen_col = None
            source = "Ozon"
        else:
            return None

        if not sku_col or not rev_col: return None

        # Фильтруем данные: убираем пустые артикулы и технические строки
        df = df[df[sku_col].astype(str).str.lower() != 'nan'].copy()
        df = df[df[sku_col].astype(str).str.strip() != ''].copy()

        res = pd.DataFrame({
            'Артикул': df[sku_col].astype(str).str.strip(),
            'Выручка': df[rev_col].apply(to_num),
            'Логистика': df[log_col].apply(to_num) if log_col else 0.0,
            'Штрафы': df[pen_col].apply(to_num) if pen_col else 0.0,
            'Маркетплейс': source
        })

        # Итоговая фильтрация мусора
        res = res[res['Артикул'].str.lower() != 'итого']
        res['Себестоимость'] = res['Артикул'].map(costs).fillna(0.0)

        return res
    except Exception as e:
        return None


# --- ИНТЕРФЕЙС ---
init_db()
st.title("💎 Ecom Insight Pro (v2.1)")

files = st.file_uploader("Загрузите отчеты (WB или Ozon)", accept_multiple_files=True)

if files:
    all_data = []
    for f in files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    if name.lower().endswith(('.xlsx', '.csv')):
                        with z.open(name) as iz:
                            d = smart_read_file(iz.read(), name)
                            processed = process_report(d)
                            if processed is not None: all_data.append(processed)
        else:
            d = smart_read_file(f.read(), f.name)
            processed = process_report(d)
            if processed is not None: all_data.append(processed)

    if all_data:
        full_df = pd.concat(all_data).groupby(['Артикул', 'Маркетплейс']).sum().reset_index()

        # Настройка недостающих цен
        missing = full_df[full_df['Себестоимость'] == 0]['Артикул'].unique()
        if len(missing) > 0:
            with st.expander("📝 Укажите себестоимость для новых товаров", expanded=True):
                for sku in missing:
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"Артикул: `{sku}`")
                    new_p = c2.number_input("Цена", key=f"in_{sku}", min_value=0.0)
                    if new_p > 0:
                        save_cost(sku, new_p)
                        st.rerun()

        # Расчеты прибыли
        full_df['Налог'] = full_df['Выручка'] * 0.06
        full_df['Прибыль'] = full_df['Выручка'] - full_df['Логистика'] - full_df['Штрафы'] - full_df['Себестоимость'] - \
                             full_df['Налог']

        st.divider()
        m1, m2, m3 = st.columns(3)
        m1.metric("Общая Выручка", f"{full_df['Выручка'].sum():,.0f} ₽")
        m2.metric("Себестоимость", f"{full_df['Себестоимость'].sum():,.0f} ₽")
        m3.metric("Чистая Прибыль", f"{full_df['Прибыль'].sum():,.2f} ₽")

        st.subheader("📋 Таблица по товарам")
        st.dataframe(full_df[['Артикул', 'Маркетплейс', 'Выручка', 'Прибыль']], use_container_width=True)