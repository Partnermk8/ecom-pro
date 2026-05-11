import streamlit as st
import pandas as pd
import sqlite3
import io

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
    s_clean = str(s).replace('\n', ' ').replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
    try:
        return float(pd.to_numeric(s_clean, errors='coerce'))
    except:
        return 0.0


# --- Специализированное чтение под ваш запрос ---
def ultra_smart_read(content, filename):
    try:
        # Читаем файл без заголовков, чтобы видеть сырую сетку
        if filename.lower().endswith('.csv'):
            df_raw = pd.read_csv(io.BytesIO(content), header=None, sep=None, engine='python')
        else:
            df_raw = pd.read_excel(io.BytesIO(content), header=None)

        found_row, found_col_sku = -1, -1

        # Шаг 1: Ищем ячейку "Артикул", у которой справа "SKU"
        for r in range(min(len(df_raw), 100)):
            for c in range(len(df_raw.columns) - 1):
                val = str(df_raw.iloc[r, c]).strip().lower()
                next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

                if val == "артикул" and next_val == "sku":
                    found_row = r
                    found_col_sku = c
                    break
            if found_row != -1: break

        if found_row == -1:
            # Если не нашли связку Артикул+SKU, ищем WB формат (заголовок по "Артикул поставщика")
            for r in range(min(len(df_raw), 60)):
                row_str = " ".join([str(x).lower() for x in df_raw.iloc[r]])
                if "артикул поставщика" in row_str or "к перечислению" in row_str:
                    df = df_raw.iloc[r + 1:].copy()
                    df.columns = [str(x).strip().lower() for x in df_raw.iloc[r]]
                    return df
            return None

        # Шаг 2: Если нашли Озон (Артикул + SKU), берем данные через строку (+2)
        # Заголовком назначаем ту самую найденную строку
        headers = [str(x).strip().lower() for x in df_raw.iloc[found_row]]
        data_start = found_row + 2

        df = df_raw.iloc[data_start:].copy()
        df.columns = headers
        return df

    except Exception as e:
        st.error(f"Ошибка чтения {filename}: {e}")
        return None


def process_report(df):
    if df is None or df.empty: return None
    costs = get_costs()
    cols = list(df.columns)

    # Детекция WB
    is_wb = any("артикул поставщика" in c or "обоснование" in c for c in cols)

    try:
        if is_wb:
            sku_col = next((c for c in cols if "артикул поставщика" in c or "артикул" in c), None)
            rev_col = next((c for c in cols if "к перечислению" in c or "возмещение" in c), None)
            source = "Wildberries"
        else:
            # Озон формат
            sku_col = next((c for c in cols if c == "артикул"), None)
            rev_col = next((c for c in cols if any(m in c for m in ["начислено", "итого", "сумма"])), None)
            source = "Ozon"

        if not sku_col or not rev_col: return None

        # Чистим от пустых строк
        df = df[df[sku_col].astype(str).str.lower() != 'nan'].copy()

        res = pd.DataFrame({
            'Артикул': df[sku_col].astype(str).str.strip(),
            'Выручка': df[rev_col].apply(to_num),
            'Маркетплейс': source
        })
        res['Себестоимость'] = res['Артикул'].map(costs).fillna(0.0)
        return res
    except:
        return None


# --- UI ---
init_db()
st.title("💎 Ecom Insight Pro v3.0")
st.write("Специальный алгоритм: поиск по связке 'Артикул + SKU' и отступ 2 строки.")

uploaded_files = st.file_uploader("Загрузите Excel или CSV", accept_multiple_files=True)

if uploaded_files:
    all_data = []
    for f in uploaded_files:
        df_raw = ultra_smart_read(f.read(), f.name)
        processed = process_report(df_raw)
        if processed is not None:
            all_data.append(processed)

    if all_data:
        full_df = pd.concat(all_data).groupby(['Артикул', 'Маркетплейс']).sum(numeric_only=True).reset_index()

        # Проверка цен
        missing = full_df[full_df['Себестоимость'] == 0]['Артикул'].unique()
        if len(missing) > 0:
            st.warning(f"Нужно ввести себестоимость для {len(missing)} товаров")
            with st.form("price_form"):
                for sku in missing:
                    c1, c2 = st.columns([3, 1])
                    c1.write(f"Товар: **{sku}**")
                    new_v = c2.number_input("Закуп", key=sku, min_value=0.0)
                    if new_v > 0: full_df.loc[full_df['Артикул'] == sku, 'Себестоимость'] = new_v
                if st.form_submit_button("📊 Посчитать всё"):
                    for sku in missing:
                        val = full_df.loc[full_df['Артикул'] == sku, 'Себестоимость'].values[0]
                        if val > 0: save_cost(sku, val)
                    st.rerun()

        full_df['Налог'] = full_df['Выручка'] * 0.06
        full_df['Прибыль'] = full_df['Выручка'] - full_df['Себестоимость'] - full_df['Налог']

        st.divider()
        m1, m2 = st.columns(2)
        m1.metric("💰 Выручка", f"{full_df['Выручка'].sum():,.0f} ₽")
        m2.metric("📈 Прибыль", f"{full_df['Прибыль'].sum():,.0f} ₽")
        st.dataframe(full_df, use_container_width=True)
    else:
        st.error("Данные не найдены. Проверьте, что в файле Озон есть ячейки 'Артикул' и 'SKU' рядом.")