import streamlit as st
import pandas as pd
import sqlite3
import zipfile
import io
import plotly.express as px

st.set_page_config(page_title="Ecom Insight Pro", layout="wide")


# --- БД ---
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
    # Очистка от мусора: пробелы, валюта, переносы строк
    s_clean = str(s).replace('\n', ' ').replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
    try:
        return float(pd.to_numeric(s_clean, errors='coerce'))
    except:
        return 0.0


# --- ЧТЕНИЕ ---
def smart_read_file(content, filename):
    try:
        if filename.lower().endswith('.csv'):
            # Пробуем разные кодировки и разделители
            for enc in ['utf-8', 'cp1251']:
                for sep in [',', ';']:
                    try:
                        df = pd.read_csv(io.BytesIO(content), sep=sep, encoding=enc)
                        if len(df.columns) > 5: break
                    except:
                        continue
                else:
                    continue
                break
        else:
            df = pd.read_excel(io.BytesIO(content))

        # Ищем заголовки (теперь до 200 строк вниз)
        markers = ["артикул", "баркод", "sku", "номер отправления", "тип компенсации"]
        target_row = -1
        for i in range(min(len(df), 200)):
            row_vals = [str(val).lower() for val in df.iloc[i].values]
            if any(any(m in v for m in markers) for v in row_vals):
                target_row = i
                break

        if target_row != -1:
            cols = [str(c).strip().replace('\n', ' ').lower() for c in df.iloc[target_row].values]
            df = df.iloc[target_row + 1:].reset_index(drop=True)
            df.columns = cols
            return df
        return None
    except:
        return None


def process_report(df):
    if df is None or df.empty: return None
    costs = get_costs()
    cols = list(df.columns)

    # Детекция WB (по специфичным заголовкам)
    is_wb = any("обоснование" in c or "артикул поставщика" in c for c in cols)
    # Детекция Ozon (по компенсациям или отправлениям)
    is_ozon = any("номер отправления" in c or "компенсаци" in c or "начислен" in c for c in cols) and not is_wb

    try:
        if is_wb:
            sku_col = next((c for c in cols if any(m == c for m in ["артикул поставщика", "артикул", "баркод"])), None)
            rev_col = next((c for c in cols if "к перечислению" in c or "возмещение" in c), None)
            log_col = next((c for c in cols if "логистика" in c or "доставке" in c), None)
            pen_col = next((c for c in cols if "штраф" in c), None)
            source = "Wildberries"
        elif is_ozon:
            sku_col = next((c for c in cols if any(m == c for m in ["артикул", "sku", "seller_sku"])), None)
            # Ищем "итого к начислению" или "сумма компенсации"
            rev_col = next((c for c in cols if any(m in c for m in ["начислен", "сумма", "итого"])), None)
            log_col, pen_col = None, None
            source = "Ozon"
        else:
            return None

        if not sku_col or not rev_col: return None

        df = df[df[sku_col].astype(str).str.lower() != 'nan'].copy()
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


# --- ИНТЕРФЕЙС ---
init_db()
st.title("💎 Ecom Insight Pro (v2.6)")
files = st.file_uploader("Загрузите CSV или Excel отчеты", accept_multiple_files=True)

if files:
    all_data = []
    for f in files:
        d = smart_read_file(f.read(), f.name)
        p = process_report(d)
        if p is not None: all_data.append(p)

    if all_data:
        df = pd.concat(all_data).groupby(['Артикул', 'Маркетплейс']).sum(numeric_only=True).reset_index()
        missing = df[df['Себестоимость'] == 0]['Артикул'].unique()

        if len(missing) > 0:
            st.warning(f"🔎 Новых товаров: {len(missing)}")
            with st.form("costs_form"):
                for sku in missing:
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"Артикул: **{sku}**")
                    new_val = c2.number_input("Цена закупа", key=sku, min_value=0.0)
                    if new_val > 0: df.loc[df['Артикул'] == sku, 'Себестоимость'] = new_val
                if st.form_submit_button("🚀 Рассчитать"):
                    for sku in missing:
                        val = df.loc[df['Артикул'] == sku, 'Себестоимость'].values[0]
                        if val > 0: save_cost(sku, val)
                    st.rerun()

        df['Налог'] = df['Выручка'] * 0.06
        df['Прибыль'] = df['Выручка'] - df['Логистика'] - df['Штрафы'] - df['Себестоимость'] - df['Налог']

        st.divider()
        m1, m2, m3 = st.columns(3)
        m1.metric("💰 Выручка", f"{df['Выручка'].sum():,.0f} ₽")
        m2.metric("📈 Прибыль", f"{df['Прибыль'].sum():,.0f} ₽")
        m3.metric("📦 Товаров", len(df))
        st.dataframe(df[['Артикул', 'Маркетплейс', 'Выручка', 'Прибыль']], use_container_width=True)
    else:
        st.error("Данные не найдены. Убедитесь, что вы загрузили файлы CSV, а не PDF.")