import streamlit as st
import pandas as pd
import sqlite3
import zipfile
import io
import plotly.express as px

st.set_page_config(page_title="Ecom Insight Pro", layout="centered")

# --- БД ---
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


def to_num(s):
    if pd.isna(s) or s == '': return 0.0
    s_clean = str(s).replace(',', '.').replace('\xa0', '').replace(' ', '').strip()
    try:
        return float(pd.to_numeric(s_clean))
    except:
        return 0.0


# --- ЧТЕНИЕ ---
def smart_read_file(content, filename):
    if not filename.lower().endswith(('.xlsx', '.csv')): return None
    try:
        if filename.lower().endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='utf-8')
            except:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='cp1251')
        else:
            df = pd.read_excel(io.BytesIO(content))

        target_row = -1
        markers = ["артикул", "баркод", "sku", "номер отправления", "seller_sku"]
        for i in range(min(len(df), 50)):
            row_vals = [str(val).lower() for val in df.iloc[i].values]
            if any(m in " ".join(row_vals) for m in markers):
                target_row = i
                break
        if target_row != -1:
            cols = df.iloc[target_row].values
            df = df.iloc[target_row + 1:].reset_index(drop=True)
            df.columns = [str(c).strip().lower() for c in cols]
        return df
    except:
        return None


def process_report(df):
    if df is None or df.empty: return None
    costs = get_costs()
    cols = df.columns
    is_wb = any(c in cols for c in ["обоснование для оплаты", "артикул поставщика"])
    is_ozon = any(c in cols for c in ["номер отправления", "начислено", "артикул"]) and not is_wb

    if is_wb:
        sku_col = next((c for c in cols if any(m == c for m in ["артикул поставщика", "артикул", "sa", "barcode"])),
                       None)
        rev_col = next((c for c in cols if "к перечислению" in c or "возмещение" in c), None)
        log_col = next((c for c in cols if "логистика" in c or "услуги по доставке" in c), None)
        source, pen_col = "Wildberries", next((c for c in cols if "штраф" in c), None)
    elif is_ozon:
        sku_col = next((c for c in cols if any(m == c for m in ["артикул", "sku", "seller_sku", "offer_id"])), None)
        rev_col = next((c for c in cols if any(m in c for m in ["начислено", "итого", "сумма"])), None)
        source, log_col, pen_col = "Ozon", None, None
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
    res = res[res['Артикул'].str.lower() != 'итого']
    res['Себестоимость'] = res['Артикул'].map(costs).fillna(0.0)
    return res


# --- UI ---
init_db()
st.title("💎 Ecom Insight Pro (v2.2)")
files = st.file_uploader("Загрузите Excel или ZIP (PDF не поддерживается)", accept_multiple_files=True)

if files:
    all_data = []
    for f in files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    with z.open(name) as iz:
                        d = smart_read_file(iz.read(), name)
                        if d is not None:
                            p = process_report(d)
                            if p is not None: all_data.append(p)
        else:
            d = smart_read_file(f.read(), f.name)
            if d is not None:
                p = process_report(d)
                if p is not None: all_data.append(p)

    if all_data:
        df = pd.concat(all_data).groupby(['Артикул', 'Маркетплейс']).sum().reset_index()
        missing = df[df['Себестоимость'] == 0]['Артикул'].unique()

        if len(missing) > 0:
            st.warning(f"Нужно ввести себестоимость для {len(missing)} товаров")
            with st.form("cost_form"):
                for sku in missing:
                    df.loc[df['Артикул'] == sku, 'Себестоимость'] = st.number_input(f"Цена: {sku}", min_value=0.0)
                if st.form_submit_button("Сохранить и рассчитать"):
                    for sku in missing:
                        save_cost(sku, df.loc[df['Артикул'] == sku, 'Себестоимость'].values[0])
                    st.rerun()

        df['Налог'] = df['Выручка'] * 0.06
        df['Прибыль'] = df['Выручка'] - df['Логистика'] - df['Штрафы'] - df['Себестоимость'] - df['Налог']

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Выручка", f"{df['Выручка'].sum():,.0f} ₽")
        c2.metric("Прибыль", f"{df['Прибыль'].sum():,.0f} ₽")
        c3.metric("Товаров", len(df))

        # ИСПРАВЛЕННЫЙ ГРАФИК
        top_5 = df.nlargest(5, 'Прибыль')
        fig = px.bar(top_5, x='Прибыль', y='Артикул', orientation='h', title="ТОП-5 по прибыли", color='Прибыль')
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(df[['Артикул', 'Выручка', 'Прибыль', 'Маркетплейс']], use_container_width=True)