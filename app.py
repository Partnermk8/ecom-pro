import streamlit as st
import pandas as pd
import sqlite3
import zipfile
import io
import plotly.express as px

# --- КОНФИГУРАЦИЯ СТРАНИЦЫ ---
st.set_page_config(
    page_title="Ecom Insight Pro",
    page_icon="💎",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# --- МОБИЛЬНЫЙ ИНТЕРФЕЙС (CSS) ---
st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.6rem !important; color: #00d4ff; }
    .stButton>button { 
        width: 100%; 
        border-radius: 12px; 
        height: 3.5em; 
        background-color: #00d4ff; 
        color: black; 
        font-weight: bold; 
        margin-top: 10px; 
    }
    div[data-testid="metric-container"] { 
        background-color: #1a1c24; 
        border: 1px solid #2d2f39; 
        border-radius: 15px; 
        padding: 15px; 
        box-shadow: 0 4px 6px rgba(0,0,0,0.3); 
    }
    .stDataFrame { border-radius: 15px; overflow: hidden; }
    </style>
    """, unsafe_allow_html=True)

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
    return dict(zip(df['sku'].astype(str), df['cost']))


def save_cost(sku, cost):
    conn = sqlite3.connect(DB_NAME)
    conn.execute('INSERT OR REPLACE INTO units (sku, cost) VALUES (?, ?)', (str(sku), cost))
    conn.commit()
    conn.close()


# --- ЛОГИКА ОБРАБОТКИ ОТЧЕТОВ ---
def smart_read_file(content, filename):
    try:
        if filename.lower().endswith('.csv'):
            try:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='utf-8')
            except:
                df = pd.read_csv(io.BytesIO(content), sep=None, engine='python', encoding='cp1251')
        else:
            df = pd.read_excel(io.BytesIO(content))

        markers = ["артикул", "обоснование", "баркод", "sku", "номер отправления"]
        for i in range(min(len(df), 40)):
            row_str = " ".join([str(val).lower() for val in df.iloc[i].values])
            if any(m in row_str for m in markers):
                new_df = df.iloc[i + 1:].reset_index(drop=True)
                new_df.columns = df.iloc[i].values
                return new_df
        return df
    except:
        return None


def process_report(df, filename):
    if df is None or df.empty: return None
    df.columns = [str(c).strip().lower() for c in df.columns]
    costs = get_costs()

    try:
        is_wb = any("обоснование" in c for c in df.columns)
        is_ozon = any("номер отправления" in c for c in df.columns)

        if is_wb:
            sku_col = [c for c in df.columns if any(m in c for m in ["артикул поставщика", "артикул"])][0]
            rev_col = [c for c in df.columns if "к перечислению" in c][0]
            log_col = [c for c in df.columns if any(m in c for m in ["логистика", "доставке"])][0]
            pen_col = [c for c in df.columns if "штраф" in c]
            source = "Wildberries"
        elif is_ozon:
            sku_col = [c for c in df.columns if any(m in c for m in ["артикул", "sku"])][0]
            rev_col = [c for c in df.columns if any(m in c for m in ["приход", "итого", "начислено"])][0]
            log_col = None
            pen_col = []
            source = "Ozon"
        else:
            return None

        to_num = lambda s: pd.to_numeric(s, errors='coerce').fillna(0)

        res = pd.DataFrame({
            'Артикул': df[sku_col].astype(str),
            'Выручка': to_num(df[rev_col]),
            'Логистика': to_num(df[log_col]) if log_col else 0,
            'Штрафы': to_num(df[pen_col[0]]) if pen_col else 0,
            'Маркетплейс': source
        })
        res = res[res['Артикул'] != 'nan'].copy()
        res['Себестоимость'] = res['Артикул'].map(costs).fillna(0)
        return res
    except Exception:
        return None


# --- ГЛАВНЫЙ ЭКРАН ---
init_db()

st.title("💎 Ecom Insight Pro")

uploaded_files = st.file_uploader("📁 Загрузите отчеты (ZIP, XLSX, CSV)", accept_multiple_files=True)

if uploaded_files:
    raw_data = []
    for f in uploaded_files:
        if f.name.lower().endswith('.zip'):
            with zipfile.ZipFile(f) as z:
                for zinfo in z.infolist():
                    try:
                        fname = zinfo.filename.encode('cp437').decode('cp866')
                    except:
                        fname = zinfo.filename
                    if fname.lower().endswith(('.xlsx', '.csv')):
                        with z.open(zinfo) as internal_f:
                            df_f = smart_read_file(internal_f.read(), fname)
                            res_f = process_report(df_f, fname)
                            if res_f is not None: raw_data.append(res_f)
        else:
            df_f = smart_read_file(f.read(), f.name)
            res_f = process_report(df_f, f.name)
            if res_f is not None: raw_data.append(res_f)

    if raw_data:
        df = pd.concat(raw_data).reset_index(drop=True)

        # Настройка недостающей себестоимости
        missing = df[df['Себестоимость'] == 0]['Артикул'].unique()
        if len(missing) > 0:
            with st.expander("💸 Укажите себестоимость", expanded=True):
                with st.form("prices"):
                    for sku in missing:
                        new_val = st.number_input(f"Товар: {sku}", min_value=0.0, key=sku)
                        if new_val > 0: save_cost(sku, new_val)
                    if st.form_submit_button("🔥 Сохранить и рассчитать"):
                        st.rerun()

        # Расчет показателей
        df['Налоги (6%)'] = df['Выручка'] * 0.06
        df['Прибыль'] = df['Выручка'] - df['Логистика'] - df['Штрафы'] - df['Себестоимость'] - df['Налоги (6%)']

        st.subheader("📊 Ключевые метрики")
        m1, m2 = st.columns(2)
        m1.metric("Выручка", f"{df['Выручка'].sum():,.0f} ₽")
        m2.metric("Прибыль", f"{df['Прибыль'].sum():,.0f} ₽")

        st.subheader("📍 Доли маркетплейсов")
        fig_pie = px.pie(df, values='Выручка', names='Маркетплейс', hole=0.5,
                         color_discrete_sequence=['#00d4ff', '#ff007a'])
        st.plotly_chart(fig_pie, use_container_width=True)

        st.subheader("🏆 ТОП-5 товаров по прибыли")
        top_df = df.groupby('Артикул')['Прибыль'].sum().nlargest(5).reset_index()
        fig_bar = px.bar(top_df, x='Прибыль', y='Артикул', orientation='h', color='Прибыль')
        st.plotly_chart(fig_bar, use_container_width=True)

        st.subheader("📋 Полная таблица")
        st.dataframe(df, use_container_width=True)
else:
    st.info("👋 Ожидаю загрузки отчетов...")