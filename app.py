import streamlit as st
import pandas as pd
import sqlite3
import io
import numpy as np

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
    """Конвертирует значение в число, обрабатывая различные форматы"""
    if pd.isna(s) or s == '' or s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s_clean = str(s).replace('\n', ' ').replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
    try:
        return float(s_clean)
    except:
        return 0.0


# --- Специализированное чтение под ваш запрос ---
def ultra_smart_read(content, filename):
    """Умное чтение файлов Ozon и Wildberries"""
    try:
        # Читаем файл без заголовков, чтобы видеть сырую сетку
        if filename.lower().endswith('.csv'):
            df_raw = pd.read_csv(io.BytesIO(content), header=None, dtype=str, encoding='utf-8')
        else:
            df_raw = pd.read_excel(io.BytesIO(content), header=None, dtype=str)

        # Ищем связку "Артикул + SKU" (Ozon)
        found_row, found_col_sku = -1, -1

        for r in range(min(len(df_raw), 100)):
            for c in range(len(df_raw.columns) - 1):
                val = str(df_raw.iloc[r, c]).strip().lower()
                next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

                if val == "артикул" and next_val == "sku":
                    found_row = r
                    found_col_sku = c
                    break
            if found_row != -1:
                break

        # Если нашли Ozon формат
        if found_row != -1:
            # Берем заголовки из строки с "Артикул" и "SKU"
            headers = []
            for c in range(len(df_raw.columns)):
                headers.append(str(df_raw.iloc[found_row, c]).strip().lower())

            # Данные начинаются через 2 строки после заголовков
            data_start = found_row + 2

            # Берем все строки до первой пустой или итоговой строки
            data_rows = []
            for r in range(data_start, len(df_raw)):
                row = df_raw.iloc[r]
                # Проверяем, не пустая ли строка и не итоговая
                first_cell = str(row.iloc[0]).strip() if len(row) > 0 else ''
                if first_cell == '' or first_cell.lower() in ['итого', 'всего', 'nan']:
                    # Проверяем, есть ли данные в других колонках
                    if not any(str(row.iloc[c]).strip() not in ['', 'nan'] for c in range(1, min(5, len(row)))):
                        break

                data_rows.append(row)

            if data_rows:
                df_data = pd.DataFrame(data_rows)
                df_data.columns = headers + ['unnamed_' + str(i) for i in range(len(headers), len(df_data.columns))]
                return df_data
            return None

        # Если не нашли Ozon, ищем WB формат
        for r in range(min(len(df_raw), 60)):
            row_str = " ".join([str(x).lower() for x in df_raw.iloc[r] if pd.notna(x)])
            if "артикул поставщика" in row_str or "к перечислению" in row_str:
                headers = [str(x).strip().lower() for x in df_raw.iloc[r]]
                data_rows = []
                for r2 in range(r + 1, len(df_raw)):
                    first_cell = str(df_raw.iloc[r2, 0]).strip() if len(df_raw.iloc[r2]) > 0 else ''
                    if first_cell == '' or first_cell.lower() in ['итого', 'всего', 'nan']:
                        break
                    data_rows.append(df_raw.iloc[r2])

                if data_rows:
                    df_data = pd.DataFrame(data_rows)
                    df_data.columns = headers + ['unnamed_' + str(i) for i in range(len(headers), len(df_data.columns))]
                    return df_data
                return None

        return None

    except Exception as e:
        st.error(f"Ошибка чтения {filename}: {e}")
        return None


def process_report(df):
    """Обрабатывает отчет, извлекая артикулы и выручку"""
    if df is None or df.empty:
        return None

    costs = get_costs()
    cols = list(df.columns)

    # Детекция WB
    is_wb = any("артикул поставщика" in str(c) or "обоснование" in str(c) for c in cols)

    try:
        if is_wb:
            # Wildberries
            sku_col = next((c for c in cols if "артикул поставщика" in str(c) or "артикул" == str(c)), None)
            rev_col = next((c for c in cols if "к перечислению" in str(c) or "возмещение" in str(c)), None)
            source = "Wildberries"
        else:
            # Ozon - ищем "артикул" и колонку с суммой к начислению
            sku_col = next((c for c in cols if str(c) == "артикул"), None)

            # Ищем колонку с итоговой суммой
            rev_col = None
            for c in cols:
                col_name = str(c)
                if any(term in col_name for term in ["итого к начислению", "начислено", "итого", "сумма"]):
                    rev_col = c
                    break

            # Если не нашли по названию, ищем по содержанию (может быть колонка 13)
            if rev_col is None:
                # Проверяем, есть ли числовые значения в колонках
                for c in cols:
                    if 'unnamed' not in str(c) or '13' in str(c):
                        sample_vals = df[c].head(3).apply(to_num)
                        if sample_vals.sum() > 0:
                            rev_col = c
                            break

            source = "Ozon"

        if sku_col is None:
            return None

        # Создаем датафрейм с результатами
        result_data = []
        for idx, row in df.iterrows():
            sku = str(row[sku_col]).strip()

            # Пропускаем пустые артикулы и итоговые строки
            if sku.lower() in ['nan', '', 'итого', 'всего']:
                continue

            # Пропускаем строки, где артикул - это число без букв (скорее всего, это не артикул)
            if sku.isdigit() and len(sku) > 4:
                continue

            # Получаем выручку
            revenue = 0.0
            if rev_col and rev_col in row.index:
                revenue = to_num(row[rev_col])
            elif rev_col and 'итого к начислению' in str(rev_col):
                revenue = to_num(row[rev_col])

            result_data.append({
                'Артикул': sku,
                'Выручка': revenue,
                'Маркетплейс': source
            })

        if not result_data:
            return None

        res = pd.DataFrame(result_data)

        # Группируем по артикулу и маркетплейсу
        res = res.groupby(['Артикул', 'Маркетплейс'])['Выручка'].sum().reset_index()

        # Добавляем себестоимость
        res['Себестоимость'] = res['Артикул'].map(costs).fillna(0.0)

        return res

    except Exception as e:
        st.error(f"Ошибка обработки: {e}")
        return None


def process_report_multiple_sheets(content, filename):
    """Обрабатывает Excel файл с несколькими листами"""
    try:
        excel_file = pd.ExcelFile(io.BytesIO(content))
        all_data = []

        for sheet_name in excel_file.sheet_names:
            df_raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None, dtype=str)
            df_processed = process_report_from_raw(df_raw, filename, sheet_name)
            if df_processed is not None:
                all_data.append(df_processed)

        if all_data:
            return pd.concat(all_data, ignore_index=True)
        return None
    except Exception as e:
        st.error(f"Ошибка обработки многостраничного файла {filename}: {e}")
        return None


def process_report_from_raw(df_raw, filename, sheet_name=""):
    """Обрабатывает сырой датафрейм"""
    # Пропускаем известные нерелевантные листы
    skip_keywords = ['биллинг', 'взаиморасчет', 'перевыставлен', 'лояльност', 'штраф']
    if any(keyword in sheet_name.lower() for keyword in skip_keywords):
        return None

    # Ищем связку "Артикул + SKU"
    found_row, found_col_sku = -1, -1

    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns) - 1):
            val = str(df_raw.iloc[r, c]).strip().lower()
            next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

            if val == "артикул" and next_val == "sku":
                found_row = r
                found_col_sku = c
                break
        if found_row != -1:
            break

    if found_row == -1:
        return None

    # Получаем заголовки
    headers = []
    for c in range(len(df_raw.columns)):
        headers.append(str(df_raw.iloc[found_row, c]).strip().lower())

    # Данные начинаются через 2 строки
    data_start = found_row + 2

    # Собираем данные
    data_rows = []
    for r in range(data_start, len(df_raw)):
        row = df_raw.iloc[r]

        # Проверяем, есть ли артикул в первой колонке
        if found_col_sku < len(row):
            sku = str(row.iloc[found_col_sku]).strip()

            # Пропускаем пустые и итоговые строки
            if sku in ['nan', '', 'итого', 'всего']:
                # Проверяем, есть ли другие данные в строке
                has_data = False
                for c in range(len(row)):
                    if c != found_col_sku:
                        val = str(row.iloc[c]).strip()
                        if val not in ['', 'nan']:
                            has_data = True
                            break
                if not has_data:
                    break
                continue

            data_rows.append(row)

    if not data_rows:
        return None

    df = pd.DataFrame(data_rows)
    if df.empty:
        return None

    # Назначаем заголовки
    all_headers = headers + [f'col_{i}' for i in range(len(headers), len(df.columns))]
    df.columns = all_headers[:len(df.columns)]

    return process_report(df)


# --- UI ---
init_db()
st.title("💎 Ecom Insight Pro v3.0")
st.write("Специальный алгоритм: поиск по связке 'Артикул + SKU' и отступ 2 строки.")

uploaded_files = st.file_uploader("Загрузите Excel или CSV", accept_multiple_files=True,
                                  type=['xlsx', 'xls', 'csv'])

if uploaded_files:
    all_data = []

    with st.spinner('Обрабатываю файлы...'):
        for f in uploaded_files:
            content = f.read()

            # Пробуем обработать как многостраничный Excel
            if f.name.lower().endswith(('.xlsx', '.xls')):
                result = process_report_multiple_sheets(content, f.name)
                if result is not None and not result.empty:
                    all_data.append(result)
            else:
                # Для CSV пробуем обычное чтение
                df_raw = ultra_smart_read(content, f.name)
                if df_raw is not None:
                    processed = process_report(df_raw)
                    if processed is not None and not processed.empty:
                        all_data.append(processed)

    if all_data:
        full_df = pd.concat(all_data, ignore_index=True)

        # Группируем по артикулу и маркетплейсу
        full_df = full_df.groupby(['Артикул', 'Маркетплейс']).agg({
            'Выручка': 'sum',
            'Себестоимость': 'sum'
        }).reset_index()

        # Проверка цен
        missing = full_df[full_df['Себестоимость'] == 0]['Артикул'].unique()
        if len(missing) > 0:
            with st.expander(f"📝 Введите себестоимость для {len(missing)} товаров", expanded=True):
                st.warning("Для корректного расчета прибыли укажите закупочные цены:")
                with st.form("price_form"):
                    new_costs = {}
                    for sku in missing:
                        c1, c2 = st.columns([3, 1])
                        c1.write(f"**{sku}**")
                        new_v = c2.number_input(f"Цена закупки", key=f"cost_{sku}", min_value=0.0, step=100.0)
                        if new_v > 0:
                            new_costs[sku] = new_v

                    if st.form_submit_button("💾 Сохранить и пересчитать"):
                        for sku, cost in new_costs.items():
                            save_cost(sku, cost)
                        st.rerun()

        # Расчет финансовых показателей
        full_df['Налог'] = full_df['Выручка'] * 0.06
        full_df['Прибыль'] = full_df['Выручка'] - full_df['Себестоимость'] - full_df['Налог']

        # Округляем значения
        for col in ['Выручка', 'Себестоимость', 'Налог', 'Прибыль']:
            full_df[col] = full_df[col].round(2)

        # Отображение результатов
        st.divider()

        total_revenue = full_df['Выручка'].sum()
        total_profit = full_df['Прибыль'].sum()
        roi = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("💰 Выручка", f"{total_revenue:,.0f} ₽")
        m2.metric("📈 Прибыль", f"{total_profit:,.0f} ₽")
        m3.metric("📊 ROI", f"{roi:.1f}%")

        st.dataframe(
            full_df.style.format({
                'Выручка': '{:,.2f} ₽',
                'Себестоимость': '{:,.2f} ₽',
                'Налог': '{:,.2f} ₽',
                'Прибыль': '{:,.2f} ₽'
            }),
            use_container_width=True,
            hide_index=True
        )

        # Дополнительная аналитика
        with st.expander("📊 Детальная аналитика"):
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("По маркетплейсам")
                mp_stats = full_df.groupby('Маркетплейс').agg({
                    'Выручка': 'sum',
                    'Прибыль': 'sum'
                }).round(2)
                st.dataframe(mp_stats, use_container_width=True)

            with col2:
                st.subheader("Топ-5 товаров по прибыли")
                top_products = full_df.nlargest(5, 'Прибыль')[['Артикул', 'Прибыль']]
                st.dataframe(top_products, use_container_width=True)

    else:
        st.error("❌ Не удалось найти данные в загруженных файлах.")
        st.info("""
        **Проверьте, что файлы содержат:**
        - Для Ozon: ячейки "Артикул" и "SKU" рядом в одной строке
        - Для Wildberries: колонку "Артикул поставщика"

        **Поддерживаемые форматы:** Excel (.xlsx, .xls), CSV
        """)