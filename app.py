import streamlit as st
import pandas as pd
import sqlite3
import zipfile
import io
import plotly.express as px

st.set_page_config(page_title="Ecom Insight Pro", layout="wide")


# --- Инициализация БД ---
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


# --- Чтение файлов ---
def smart_read_file(content, filename):
    if filename.lower().endswith('.pdf'):
        st.error(f"❌ Файл {filename} пропущен: PDF не поддерживается. Используйте Excel.")
        return None
    try:
        if filename.lower().endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='utf-8')
            except:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='cp1251')
        else:
            df = pd.read_excel(io.BytesIO(content))

        # Поиск строки-заголовка
        target_row = -1
        markers = ["артикул", "баркод", "sku", "номер отправления", "seller_sku", "offer_id"]
        for i in range(min(len(df), 60)):
            row_vals = [str(val).lower() for val in df.iloc[i].values]
            if any(m in " ".join(row_vals) for m in markers):
                target_row = i
                break

        if target_row != -1:
            cols = [str(c).strip().lower() for c in df.iloc[target_row].values]
            df = df.iloc[target_row + 1:].reset_index(drop=True)
            df.columns = cols
            return df
        else:
            st.warning(f"⚠️ В файле {filename} не найдены заголовки (Артикул/SKU).")
            return None
    except Exception as e:
        st.error(f"💥 Ошибка чтения {filename}: {e}")
        return None


def process_report(df):
    if df is None or df.empty: return None
    costs = get_costs()
    cols = df.columns

    # Проверка на WB
    is_wb = any(c in cols for c in ["обоснование для оплаты", "артикул поставщика", "объявление для оплаты"])
    # Проверка на Ozon
    is_ozon = any(c in cols for c in ["номер отправления", "начислено", "тип начисления"]) and not is_wb

    try:
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

        if not sku_col or not rev_col:
            st.info(f"Найдено: {source}. Но не найдены колонки: Артикул({sku_col}) или Выручка({rev_col})")
            return None

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


# --- Интерфейс ---
init_db()
st.title("💎 Ecom Insight Pro (v2.3)")
files = st.file_uploader("Загрузите отчеты", accept_multiple_files=True)

if files:
    all_data = []
    for f in files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    if name.lower().endswith(('.xlsx', '.csv')):
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
            st.warning(f"🔎 Новые товары: {len(missing)}")
            with st.form("costs"):
                for sku in missing:
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"**{sku}**")
                    new_val = c2.number_input("Себестоимость", key=sku, min_value=0.0)
                    if new_val > 0: df.loc[df['Артикул'] == sku, 'Себестоимость'] = new_val
                if st.form_submit_button("✅ Применить и сохранить"):
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
        col3.metric("📦 Товаров", len(df))

        st.subheader("📊 Аналитика по товарам")
        st.dataframe(df[['Артикул', 'Маркетплейс', 'Выручка', 'Прибыль']], use_container_width=True)

        fig = px.bar(df.nlargest(10, 'Прибыль'), x='Прибыль', y='Артикул', orientation='h', color='Маркетплейс')
        st.plotly_chart(fig, use_container_width=True)