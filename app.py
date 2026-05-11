import streamlit as st
import pandas as pd
import sqlite3
import io
import re
import numpy as np
import zipfile
import os

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
    """Конвертирует значение в число"""
    if pd.isna(s) or s == '' or s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    s_clean = str(s).replace('\n', ' ').replace('\xa0', '').replace(' ', '').replace(',', '.').strip()
    try:
        return float(s_clean)
    except:
        return 0.0


def is_valid_sku(value):
    """
    Проверяет, является ли значение валидным артикулом.
    Артикул должен содержать буквы и цифры, не может состоять только из цифр
    """
    if pd.isna(value) or value == '' or value is None:
        return False

    s = str(value).strip()

    # Пустая строка
    if not s:
        return False

    # Содержит хотя бы одну букву (русскую или английскую) и дефис
    has_letters = bool(re.search(r'[а-яА-Яa-zA-Z]', s))

    # Содержит хотя бы одну цифру
    has_digits = bool(re.search(r'\d', s))

    # Артикул должен содержать и буквы, и цифры
    if not has_letters or not has_digits:
        return False

    # Исключаем явно не артикулы:
    # - слишком длинные (скорее всего штрих-коды)
    if len(s) > 20:
        return False

    # - служебные строки
    if s.lower() in ['итого', 'всего', 'наименование', 'итог', 'total']:
        return False

    return True


def extract_archives(uploaded_files):
    """Извлекает файлы из архивов"""
    all_files = []

    for uploaded_file in uploaded_files:
        if uploaded_file.name.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(uploaded_file.read()), 'r') as zip_ref:
                    for file_name in zip_ref.namelist():
                        # Пропускаем служебные файлы
                        if file_name.startswith('__MACOSX') or file_name.startswith('.'):
                            continue
                        # Пропускаем папки
                        if file_name.endswith('/'):
                            continue

                        content = zip_ref.read(file_name)
                        if file_name.lower().endswith(('.xlsx', '.xls', '.csv')):
                            all_files.append({
                                'name': os.path.basename(file_name),
                                'content': content
                            })
            except Exception as e:
                st.error(f"Ошибка чтения архива {uploaded_file.name}: {e}")
        else:
            # Обычный файл
            all_files.append({
                'name': uploaded_file.name,
                'content': uploaded_file.read()
            })

    return all_files


# --- Обработка Ozon ---
def find_header_row_ozon(df_raw):
    """Находит строку с заголовками Ozon (Артикул + SKU)"""
    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns) - 1):
            val = str(df_raw.iloc[r, c]).strip().lower()
            next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

            if val == "артикул" and next_val == "sku":
                return r

    return None


def find_revenue_column_ozon(df_raw, header_row, sku_col):
    """Находит колонку с итоговой выручкой в отчете Ozon"""
    # Ищем по заголовку "Итого к начислению"
    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if 'итого к начислению' in header:
            return c

    # Если не нашли, ищем по стандартной позиции (артикул + 10 колонок)
    if sku_col is not None:
        potential_col = sku_col + 10
        if potential_col < len(df_raw.columns):
            sample_count = 0
            for r in range(header_row + 2, min(header_row + 10, len(df_raw))):
                val = to_num(df_raw.iloc[r, potential_col])
                if val > 0:
                    sample_count += 1
            if sample_count >= 2:
                return potential_col

    return None


def process_ozon_report(df_raw):
    """Обрабатывает отчет Ozon"""
    header_row = find_header_row_ozon(df_raw)
    if header_row is None:
        return None

    # Определяем колонки
    headers = {}
    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if header and header != 'nan':
            headers[c] = header

    # Находим колонку с артикулом
    sku_col = None
    for col_idx, header in headers.items():
        if header == 'артикул':
            sku_col = col_idx
            break

    if sku_col is None:
        return None

    # Находим колонку с выручкой
    revenue_col = find_revenue_column_ozon(df_raw, header_row, sku_col)
    if revenue_col is None:
        return None

    # Читаем данные со 2-й строки после заголовков
    data_start = header_row + 2

    result_data = []

    for r in range(data_start, len(df_raw)):
        sku_value = df_raw.iloc[r, sku_col]

        # Проверяем, что это валидный артикул
        if not is_valid_sku(sku_value):
            continue

        sku = str(sku_value).strip().upper()  # Приводим к верхнему регистру для единообразия
        revenue = to_num(df_raw.iloc[r, revenue_col])

        if revenue >= 0:
            result_data.append({
                'Артикул': sku,
                'Выручка': revenue,
                'Маркетплейс': 'Ozon'
            })

    if not result_data:
        return None

    return pd.DataFrame(result_data)


def process_ozon_compensation(df_raw):
    """Обрабатывает отчет о компенсациях Ozon"""
    # Ищем строку с заголовками
    header_row = None

    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns)):
            val = str(df_raw.iloc[r, c]).strip().lower()
            if val == "артикул":
                header_row = r
                break
        if header_row is not None:
            break

    if header_row is None:
        return None

    # Находим колонки
    sku_col = None
    revenue_col = None

    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if header == "артикул":
            sku_col = c
        elif "итого к начислению" in header:
            revenue_col = c

    if sku_col is None:
        return None

    # Если не нашли колонку с выручкой, ищем последнюю числовую колонку
    if revenue_col is None:
        for c in range(len(df_raw.columns) - 1, sku_col, -1):
            sample_val = to_num(df_raw.iloc[header_row + 1, c])
            if sample_val > 0:
                revenue_col = c
                break

    if revenue_col is None:
        return None

    # Собираем данные
    result_data = []

    for r in range(header_row + 1, len(df_raw)):
        sku_value = df_raw.iloc[r, sku_col]

        if not is_valid_sku(sku_value):
            if r > header_row + 1:
                prev_sku = df_raw.iloc[r - 1, sku_col]
                if not is_valid_sku(prev_sku):
                    break
            continue

        sku = str(sku_value).strip().upper()
        revenue = to_num(df_raw.iloc[r, revenue_col])

        if revenue >= 0:
            result_data.append({
                'Артикул': sku,
                'Выручка': revenue,
                'Маркетплейс': 'Ozon'
            })

    if not result_data:
        return None

    return pd.DataFrame(result_data)


# --- Обработка Wildberries ---
def is_wb_report(df_raw):
    """Определяет, является ли отчет от Wildberries"""
    for r in range(min(len(df_raw), 10)):
        row_text = ' '.join([str(x).lower() for x in df_raw.iloc[r] if pd.notna(x)])
        if 'вайлдберриз' in row_text or 'wildberries' in row_text:
            return True
        if 'артикул поставщика' in row_text or 'к перечислению продавцу' in row_text:
            return True
    return False


def process_wb_report(df_raw):
    """Обрабатывает отчет Wildberries (еженедельный детализированный)"""
    # Ищем строку с заголовками
    header_row = None

    for r in range(min(len(df_raw), 20)):
        row_values = [str(x).strip().lower() for x in df_raw.iloc[r] if pd.notna(x)]
        if 'артикул поставщика' in row_values and 'к перечислению продавцу' in row_values:
            header_row = r
            break

    if header_row is None:
        return None

    # Определяем индексы колонок по заголовкам
    sku_col = None
    revenue_col = None
    doc_type_col = None

    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()

        if header == 'артикул поставщика':
            sku_col = c
        elif 'к перечислению продавцу' in header:
            revenue_col = c
        elif header == 'тип документа':
            doc_type_col = c

    if sku_col is None or revenue_col is None:
        return None

    # Собираем данные
    result_data = []

    for r in range(header_row + 1, len(df_raw)):
        # Проверяем тип документа (берем только "Продажа")
        if doc_type_col is not None:
            doc_type = str(df_raw.iloc[r, doc_type_col]).strip().lower()
            if doc_type != 'продажа':
                continue

        sku_value = df_raw.iloc[r, sku_col]

        # Проверяем, что это валидный артикул
        if not is_valid_sku(sku_value):
            # Проверяем, не закончились ли данные
            if r > header_row + 10:
                prev_rows_valid = False
                for check_r in range(max(header_row + 1, r - 5), r):
                    if is_valid_sku(df_raw.iloc[check_r, sku_col]):
                        prev_rows_valid = True
                        break
                if not prev_rows_valid:
                    break
            continue

        sku = str(sku_value).strip().upper()
        revenue = to_num(df_raw.iloc[r, revenue_col])

        if revenue > 0:  # Только строки с положительной выручкой
            result_data.append({
                'Артикул': sku,
                'Выручка': revenue,
                'Маркетплейс': 'WB'
            })

    if not result_data:
        return None

    return pd.DataFrame(result_data)


# --- Основная функция обработки ---
def process_single_file(file_info):
    """Обрабатывает один файл (Excel или CSV)"""
    content = file_info['content']
    filename = file_info['name']

    try:
        # Пробуем прочитать как Excel
        if filename.lower().endswith(('.xlsx', '.xls')):
            excel_file = pd.ExcelFile(io.BytesIO(content))
            all_results = []

            # Фильтруем листы
            relevant_sheets = []
            for sheet_name in excel_file.sheet_names:
                sheet_lower = sheet_name.lower()
                skip_keywords = ['биллинг', 'взаиморасчет', 'перевыставлен',
                                 'лояльност', 'штраф', 'механик', 'суммах услуг']
                if not any(keyword in sheet_lower for keyword in skip_keywords):
                    relevant_sheets.append(sheet_name)

            for sheet_name in relevant_sheets:
                df_raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name,
                                       header=None, dtype=str)

                # Определяем тип отчета
                if is_wb_report(df_raw):
                    result = process_wb_report(df_raw)
                    report_type = "Wildberries"
                else:
                    result = process_ozon_report(df_raw)
                    if result is None:
                        result = process_ozon_compensation(df_raw)
                    report_type = "Ozon"

                if result is not None and not result.empty:
                    st.success(f"✅ {filename} / {sheet_name} → {report_type}")
                    all_results.append(result)

            if all_results:
                return pd.concat(all_results, ignore_index=True)

        # Для CSV файлов
        elif filename.lower().endswith('.csv'):
            df_raw = pd.read_csv(io.BytesIO(content), header=None, dtype=str)

            if is_wb_report(df_raw):
                result = process_wb_report(df_raw)
            else:
                result = process_ozon_report(df_raw)

            if result is not None and not result.empty:
                return result

    except Exception as e:
        st.error(f"Ошибка обработки {filename}: {e}")

    return None


# --- UI ---
init_db()
st.title("💎 Ecom Insight Pro v3.0")
st.markdown("""
**Поддерживаемые маркетплейсы:** Ozon | Wildberries  
**Форматы:** Excel (.xlsx, .xls), CSV, ZIP архивы
""")

with st.expander("📋 Правила обработки", expanded=False):
    st.markdown("""
    **Ozon:**
    - Поиск связки 'Артикул + SKU' в заголовках
    - Чтение данных со 2-й строки после заголовков
    - Выручка из колонки 'Итого к начислению'

    **Wildberries:**
    - Поиск заголовка 'Артикул поставщика'
    - Только строки с типом документа 'Продажа'
    - Выручка из колонки 'К перечислению Продавцу'

    **Артикул:** буквы + цифры (например: КБ-25, РБ-3, кб-17)
    """)

uploaded_files = st.file_uploader(
    "Загрузите файлы или архивы",
    accept_multiple_files=True,
    type=['xlsx', 'xls', 'csv', 'zip']
)

if uploaded_files:
    # Извлекаем файлы из архивов
    with st.spinner('Распаковываю архивы...'):
        all_files = extract_archives(uploaded_files)

    if not all_files:
        st.error("Не найдено поддерживаемых файлов")
        st.stop()

    st.info(f"Найдено файлов для обработки: {len(all_files)}")

    # Обрабатываем все файлы
    all_data = []
    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, file_info in enumerate(all_files):
        status_text.text(f"Обрабатываю: {file_info['name']}")
        result = process_single_file(file_info)

        if result is not None and not result.empty:
            all_data.append(result)

        progress_bar.progress((i + 1) / len(all_files))

    status_text.text("Обработка завершена!")

    if all_data:
        full_df = pd.concat(all_data, ignore_index=True)

        # Группируем по артикулу и маркетплейсу
        full_df = full_df.groupby(['Артикул', 'Маркетплейс']).agg({
            'Выручка': 'sum'
        }).reset_index()

        # Добавляем себестоимость
        costs = get_costs()
        full_df['Себестоимость'] = full_df['Артикул'].map(costs).fillna(0.0)

        # Проверка цен
        missing = full_df[full_df['Себестоимость'] == 0]['Артикул'].unique()
        if len(missing) > 0:
            with st.expander(f"📝 Введите себестоимость для {len(missing)} товаров", expanded=True):
                st.warning("Для корректного расчета прибыли укажите закупочные цены:")
                with st.form("price_form"):
                    new_costs = {}
                    cols_per_row = 4
                    missing_list = sorted(missing)

                    for i in range(0, len(missing_list), cols_per_row):
                        cols = st.columns(cols_per_row)
                        for j in range(cols_per_row):
                            if i + j < len(missing_list):
                                sku = missing_list[i + j]
                                with cols[j]:
                                    st.write(f"**{sku}**")
                                    new_v = st.number_input(
                                        "Цена закупки",
                                        key=f"cost_{sku}",
                                        min_value=0.0,
                                        step=100.0,
                                        format="%.2f"
                                    )
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

        # Сортируем по прибыли
        full_df = full_df.sort_values('Прибыль', ascending=False)

        # --- Отображаем сводку по маркетплейсам ---
        st.divider()
        st.subheader("📊 Сводка по маркетплейсам")

        mp_summary = full_df.groupby('Маркетплейс').agg({
            'Выручка': 'sum',
            'Себестоимость': 'sum',
            'Налог': 'sum',
            'Прибыль': 'sum'
        }).round(2)

        cols_mp = st.columns(len(mp_summary))

        for idx, (mp, row) in enumerate(mp_summary.iterrows()):
            with cols_mp[idx]:
                if mp == 'Ozon':
                    st.markdown("🟦 **Ozon**")
                else:
                    st.markdown("🟪 **Wildberries**")
                st.metric("Выручка", f"{row['Выручка']:,.0f} ₽")
                st.metric("Прибыль", f"{row['Прибыль']:,.0f} ₽")
                roi = (row['Прибыль'] / row['Выручка'] * 100) if row['Выручка'] > 0 else 0
                st.metric("ROI", f"{roi:.1f}%")

        # --- Общая таблица ---
        st.divider()
        st.subheader("📋 Общая таблица")

        total_revenue = full_df['Выручка'].sum()
        total_profit = full_df['Прибыль'].sum()
        total_cost = full_df['Себестоимость'].sum()
        total_tax = full_df['Налог'].sum()
        roi = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("💰 Общая выручка", f"{total_revenue:,.0f} ₽")
        m2.metric("📦 Себестоимость", f"{total_cost:,.0f} ₽")
        m3.metric("💸 Налог", f"{total_tax:,.0f} ₽")
        m4.metric("📈 Прибыль", f"{total_profit:,.0f} ₽")
        m5.metric("📊 ROI", f"{roi:.1f}%")


        # Стилизуем таблицу
        def color_profit(val):
            color = 'red' if val < 0 else 'green'
            return f'color: {color}'


        def highlight_mp(val):
            if val == 'Ozon':
                return 'background-color: #e3f2fd'
            elif val == 'WB':
                return 'background-color: #f3e5f5'
            return ''


        styled_df = full_df.style \
            .applymap(color_profit, subset=['Прибыль']) \
            .applymap(highlight_mp, subset=['Маркетплейс']) \
            .format({
            'Выручка': '{:,.2f} ₽',
            'Себестоимость': '{:,.2f} ₽',
            'Налог': '{:,.2f} ₽',
            'Прибыль': '{:,.2f} ₽'
        })

        st.dataframe(styled_df, use_container_width=True, hide_index=True)

        # --- Раздельные таблицы по маркетплейсам ---
        with st.expander("📊 Раздельная аналитика по маркетплейсам"):
            tab1, tab2 = st.tabs(["🟦 Ozon", "🟪 Wildberries"])

            with tab1:
                ozon_df = full_df[full_df['Маркетплейс'] == 'Ozon'].copy()
                if not ozon_df.empty:
                    st.dataframe(
                        ozon_df.style.format({
                            'Выручка': '{:,.2f} ₽',
                            'Себестоимость': '{:,.2f} ₽',
                            'Налог': '{:,.2f} ₽',
                            'Прибыль': '{:,.2f} ₽'
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                    csv_ozon = ozon_df.to_csv(index=False)
                    st.download_button(
                        "📥 Скачать отчет Ozon",
                        csv_ozon,
                        "ozon_report.csv",
                        "text/csv"
                    )
                else:
                    st.info("Нет данных Ozon")

            with tab2:
                wb_df = full_df[full_df['Маркетплейс'] == 'WB'].copy()
                if not wb_df.empty:
                    st.dataframe(
                        wb_df.style.format({
                            'Выручка': '{:,.2f} ₽',
                            'Себестоимость': '{:,.2f} ₽',
                            'Налог': '{:,.2f} ₽',
                            'Прибыль': '{:,.2f} ₽'
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                    csv_wb = wb_df.to_csv(index=False)
                    st.download_button(
                        "📥 Скачать отчет WB",
                        csv_wb,
                        "wb_report.csv",
                        "text/csv"
                    )
                else:
                    st.info("Нет данных Wildberries")

        # --- Расширенная аналитика ---
        with st.expander("📈 Расширенная аналитика"):
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("🏆 Топ-5 товаров по прибыли")
                top_products = full_df.nlargest(5, 'Прибыль')[['Артикул', 'Маркетплейс', 'Прибыль']]
                st.dataframe(top_products, use_container_width=True, hide_index=True)

                st.subheader("📉 Топ-5 товаров по убыткам")
                bottom_products = full_df.nsmallest(5, 'Прибыль')[['Артикул', 'Маркетплейс', 'Прибыль']]
                st.dataframe(bottom_products, use_container_width=True, hide_index=True)

            with col2:
                st.subheader("📦 Статистика")
                st.write(f"Всего товаров: {len(full_df)}")
                st.write(f"Прибыльных: {len(full_df[full_df['Прибыль'] > 0])}")
                st.write(f"Убыточных: {len(full_df[full_df['Прибыль'] < 0])}")

                # Распределение по маркетплейсам
                mp_counts = full_df['Маркетплейс'].value_counts()
                for mp, count in mp_counts.items():
                    st.write(f"• {mp}: {count} товаров")

                st.subheader("💰 Маржинальность")
                for mp in full_df['Маркетплейс'].unique():
                    mp_data = full_df[full_df['Маркетплейс'] == mp]
                    mp_margin = (mp_data['Прибыль'].sum() / mp_data['Выручка'].sum() * 100) if mp_data[
                                                                                                   'Выручка'].sum() > 0 else 0
                    st.write(f"• {mp}: {mp_margin:.1f}%")

        # --- Общий экспорт ---
        csv_all = full_df.to_csv(index=False)
        st.download_button(
            "📥 Скачать общий отчет",
            csv_all,
            "ecom_insight_full_report.csv",
            "text/csv"
        )

    else:
        st.error("❌ Не удалось найти данные в загруженных файлах.")
        st.info("""
        **Проверьте, что файлы содержат:**

        **Для Ozon:**
        - Связку 'Артикул + SKU' в заголовках
        - Артикулы в формате: буквы + цифры (КБ-25, РБ-3)

        **Для Wildberries:**
        - Заголовок 'Артикул поставщика'
        - Колонку 'К перечислению Продавцу'
        - Тип документа 'Продажа'

        **Поддерживаемые форматы:** Excel (.xlsx, .xls), CSV, ZIP
        """)