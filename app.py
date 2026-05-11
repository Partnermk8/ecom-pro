import streamlit as st
import pandas as pd
import sqlite3
import zipfile
import io
import plotly.express as px

st.set_page_config(page_title="Ecom Insight Pro", layout="wide")


# --- База данных ---
def init_db():
    conn = sqlite3.connect("ecom_data.db")
    conn.execute('CREATE TABLE IF NOT EXISTS units (sku TEXT PRIMARY KEY, cost REAL)')
    conn.close()


def get_costs():
    conn = sqlite3.connect("ecom_data.db")
    df = pd.read_sql('SELECT * FROM units', conn)
    conn.close()
    return dict(zip(df['sku'].astype(str).str.strip(), df['cost']))


def save_cost(sku, cost):
    conn = sqlite3.connect("ecom_data.db")
    conn.execute('INSERT OR REPLACE INTO units (sku, cost) VALUES (?, ?)', (str(sku).strip(), cost))
    conn.commit()
    conn.close()


def to_num(s):
    if pd.isna(s) or s == '': return 0.0
    s_clean = str(s).replace(',', '.').replace('\xa0', '').replace(' ', '').strip()
    try:
        return float(pd.to_numeric(s_clean))
    except:
        return 0.0


# --- Бронебойное чтение файлов ---
def smart_read_file(content, filename):
    if filename.lower().endswith('.pdf'): return None
    try:
        if filename.lower().endswith('.csv'):
            # Пробуем разные кодировки и разделители для русского CSV
            try:
                df = pd.read_csv(io.BytesIO(content), sep=';', encoding='utf-8')
                if len(df.columns) < 3: raise ValueError
            except:
                try:
                    df = pd.read_csv(io.BytesIO(content), sep=',', encoding='utf-8')
                except:
                    df = pd.read_csv(io.BytesIO(content), sep=';', encoding='cp1251')
        else:
            df = pd.read_excel(io.BytesIO(content))

        # Ищем строку с заголовками глубже (до 150 строк, из-за шапок Ozon)
        markers = ["артикул поставщика", "артикул", "баркод", "sku", "номер отправления", "seller_sku"]
        target_row = -1

        for i in range(min(len(df), 150)):
            row_vals = [str(val).lower().strip() for val in df.iloc[i].values]
            # Проверяем точное совпадение или вхождение
            if any(m in row_vals for m in markers) or any(any(m in str(v) for m in markers) for v in row_vals):
                target_row = i
                break

        if target_row != -1:
            cols = [str(c).strip().lower() for c in df.iloc[target_row].values]
            df = df.iloc[target_row + 1:].reset_index(drop=True)
            df.columns = cols
            return df
        return None
    except Exception as e:
        st.error(f"Ошибка в файле {filename}: {e}")
        return None


# --- Обработка данных WB и Ozon ---
def process_report(df):
    if df is None or df.empty: return None
    costs = get_costs()
    cols = list(df.columns)

    # Определяем платформу по ключевым столбцам
    is_wb = any(c in cols for c in ["обоснование для оплаты", "артикул поставщика", "вайлдберриз"])
    is_ozon = any(c in cols for c in ["номер отправления", "начислено", "компенсаци", "seller_sku"]) and not is_wb

    try:
        if is_wb:
            sku_cand = ["артикул поставщика", "артикул", "баркод", "barcode"]
            sku_col = next((c for c in cols if any(m == c for m in sku_cand)), None)
            rev_cand = ["к перечислению", "возмещение", "итого к оплате"]
            rev_col = next((c for c in cols if any(m in c for m in rev_cand)), None)
            log_col = next((c for c in cols if "логистика" in c or "доставке" in c), None)
            pen_col = next((c for c in cols if "штраф" in c), None)
            source = "Wildberries"
        elif is_ozon:
            sku_cand = ["артикул", "sku", "seller_sku"]
            sku_col = next((c for c in cols if any(m == c for m in sku_cand)), None)
            # Добавлено слово "компенсаци" для Отчета о компенсациях
            rev_col = next((c for c in cols if any(m in c for m in ["начислено", "итого", "сумма компенсаци"])), None)
            source, log_col, pen_col = "Ozon", None, None
        else:
            return None

        if not sku_col or not rev_col: return None

        # Убираем системные строки (пустые артикулы и слово "Итого")
        df = df[df[sku_col].astype(str).str.lower() != 'nan'].copy()
        df = df[df[sku_col].astype(str).str.lower() != 'итого'].copy()

        res = pd.DataFrame({
            'Артикул': df[sku_col].astype(str).str.strip(),
            'Выручка': df[rev_col].apply(to_num),
            'Логистика': df[log_col].apply(to_num) if log_col else 0.0,
            'Штрафы': df[pen_col].apply(to_num) if pen_col else 0.0,
            'Маркетплейс': source
        })
        res['Себестоимость'] = res['Артикул'].map(costs).fillna(0.0)
        return res
    except:
        return None


# --- Интерфейс ---
init_db()
st.title("💎 Ecom Insight Pro (v2.5 - Ozon & WB Релиз)")
files = st.file_uploader("Загрузите отчеты (Excel, CSV, ZIP)", accept_multiple_files=True)

if files:
    all_data = []
    for f in files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    if name.lower().endswith(('.xlsx', '.csv', '.xls')):
                        with z.open(name) as iz:
                            d = smart_read_file(iz.read(), name)
                            p = process_report(d)
                            if p is not None: all_data.append(p)
        else:
            d = smart_read_file(f.read(), f.name)
            p = process_report(d)
            if p is not None: all_data.append(p)

    if all_data:
        df = pd.concat(all_data).groupby(['Артикул', 'Маркетплейс']).sum().reset_index()
        missing = df[df['Себестоимость'] == 0]['Артикул'].unique()

        if len(missing) > 0:
            st.warning(f"🔎 Найдено новых товаров: {len(missing)}")
            with st.form("costs"):
                for sku in missing:
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"Артикул: **{sku}**")
                    new_val = c2.number_input("Цена закупки", key=sku, min_value=0.0)
                    if new_val > 0: df.loc[df['Артикул'] == sku, 'Себестоимость'] = new_val
                if st.form_submit_button("🚀 Применить"):
                    for sku in missing:
                        val = df.loc[df['Артикул'] == sku, 'Себестоимость'].values[0]
                        if val > 0: save_cost(sku, val)
                    st.rerun()

        df['Налог'] = df['Выручка'] * 0.06
        df['Прибыль'] = df['Выручка'] - df['Логистика'] - df['Штрафы'] - df['Себестоимость'] - df['Налог']

        st.divider()
        col1, col2, col3 = st.columns(3)
        col1.metric("💰 Выручка", f"{df['Выручка'].sum():,.0f} ₽")
        col2.metric("📈 Прибыль", f"{df['Прибыль'].sum():,.0f} ₽")
        col3.metric("📦 Позиций", len(df))

        st.dataframe(df[['Артикул', 'Маркетплейс', 'Выручка', 'Прибыль']], use_container_width=True)
    else:
        st.info("Программа не нашла данные. Проверьте, что загружены правильные отчеты.")