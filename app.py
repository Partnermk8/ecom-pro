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
    Артикул должен содержать буквы и цифры/дефис
    """
    if pd.isna(value) or value == '' or value is None:
        return False

    s = str(value).strip()

    if not s:
        return False

    # Исключаем служебные строки
    if s.lower() in ['итого', 'всего', 'наименование', 'итог', 'total', 'nan']:
        return False

    # Содержит буквы (русские или английские)
    has_letters = bool(re.search(r'[а-яА-Яa-zA-Z]', s))

    # Если нет букв - это не артикул
    if not has_letters:
        return False

    # Слишком длинные строки - скорее всего не артикулы
    if len(s) > 30:
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
                        if file_name.startswith('__MACOSX') or file_name.startswith('.'):
                            continue
                        if file_name.endswith('/'):
                            continue

                        content = zip_ref.read(file_name)
                        ext = os.path.splitext(file_name)[1].lower()
                        if ext in ['.xlsx', '.xls', '.csv']:
                            all_files.append({
                                'name': os.path.basename(file_name),
                                'content': content
                            })
            except Exception as e:
                st.error(f"Ошибка чтения архива {uploaded_file.name}: {e}")
        else:
            all_files.append({
                'name': uploaded_file.name,
                'content': uploaded_file.read()
            })

    return all_files


# ============ OZON (НЕ ТРОГАЕМ, РАБОТАЕТ) ============

def find_header_row_ozon(df_raw):
    """Находит строку с заголовками Ozon (Артикул + SKU)"""
    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns) - 1):
            if c + 1 >= len(df_raw.columns):
                break
            val = str(df_raw.iloc[r, c]).strip().lower()
            next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

            if val == "артикул" and next_val == "sku":
                return r

    return None


def find_revenue_column_ozon(df_raw, header_row, sku_col):
    """Находит колонку с итоговой выручкой в отчете Ozon"""
    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if 'итого к начислению' in header:
            return c

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

    headers = {}
    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if header and header != 'nan':
            headers[c] = header

    sku_col = None
    for col_idx, header in headers.items():
        if header == 'артикул':
            sku_col = col_idx
            break

    if sku_col is None:
        return None

    revenue_col = find_revenue_column_ozon(df_raw, header_row, sku_col)
    if revenue_col is None:
        return None

    data_start = header_row + 2

    result_data = []

    for r in range(data_start, len(df_raw)):
        if sku_col >= len(df_raw.columns):
            continue

        sku_value = df_raw.iloc[r, sku_col]

        if not is_valid_sku(sku_value):
            continue

        sku = str(sku_value).strip().upper()

        if revenue_col < len(df_raw.columns):
            revenue = to_num(df_raw.iloc[r, revenue_col])
        else:
            revenue = 0.0

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

    if revenue_col is None:
        for c in range(len(df_raw.columns) - 1, sku_col + 1, -1):
            if header_row + 1 < len(df_raw):
                sample_val = to_num(df_raw.iloc[header_row + 1, c])
                if sample_val > 0:
                    revenue_col = c
                    break

    if revenue_col is None:
        return None

    result_data = []

    for r in range(header_row + 1, len(df_raw)):
        if sku_col >= len(df_raw.columns):
            continue

        sku_value = df_raw.iloc[r, sku_col]

        if not is_valid_sku(sku_value):
            continue

        sku = str(sku_value).strip().upper()

        if revenue_col < len(df_raw.columns):
            revenue = to_num(df_raw.iloc[r, revenue_col])
        else:
            revenue = 0.0

        result_data.append({
            'Артикул': sku,
            'Выручка': revenue,
            'Маркетплейс': 'Ozon'
        })

    if not result_data:
        return None

    return pd.DataFrame(result_data)


# ============ WILDBERRIES (ИСПРАВЛЯЕМ) ============

def debug_wb_raw(df_raw, sheet_name=""):
    """Выводит отладочную информацию о структуре WB отчета"""
    st.write(f"### 🔍 Отладка WB: {sheet_name}")
    st.write(f"Размер таблицы: {df_raw.shape[0]} строк x {df_raw.shape[1]} колонок")

    # Показываем первые 15 строк, первые 30 колонок
    st.write("**Первые строки:**")
    max_cols = min(30, df_raw.shape[1])
    st.dataframe(df_raw.iloc[:15, :max_cols], use_container_width=True)

    # Ищем ключевые слова
    st.write("**Поиск ключевых колонок:**")
    for r in range(min(15, len(df_raw))):
        for c in range(min(30, len(df_raw.columns))):
            val = str(df_raw.iloc[r, c]).strip().lower()
            if any(kw in val for kw in ['артикул', 'перечислен', 'продаж', 'тип документ', 'обоснование']):
                st.write(f"  Строка {r}, Колонка {c}: '{val}'")


def process_wb_report(df_raw, debug=False, sheet_name=""):
    """Обрабатывает отчет Wildberries. Исправленная версия."""

    # ===== Шаг 1: Ищем заголовки =====
    header_row = None

    for r in range(min(len(df_raw), 30)):
        # Собираем все значения строки
        row_values = []
        for c in range(min(len(df_raw.columns), 50)):
            val = str(df_raw.iloc[r, c]).strip().lower()
            if val and val != 'nan':
                row_values.append(val)

        # Проверяем наличие ключевых колонок
        has_artikul = 'артикул поставщика' in row_values
        has_perechislen = any('к перечислению продавцу' in v for v in row_values)

        if has_artikul and has_perechislen:
            header_row = r
            if debug:
                st.write(f"✅ Найдена строка заголовков: {r}")
            break

    if header_row is None:
        # Пробуем найти по отдельности
        for r in range(min(len(df_raw), 30)):
            row_text = ' '.join([str(df_raw.iloc[r, c]).strip().lower()
                                 for c in range(min(50, len(df_raw.columns)))])
            if 'артикул поставщика' in row_text:
                header_row = r
                if debug:
                    st.write(f"⚠️ Найдена строка с 'артикул поставщика': {r}")
                break

    if header_row is None:
        if debug:
            st.error("❌ Строка заголовков не найдена")
        return None

    # ===== Шаг 2: Определяем колонки =====
    sku_col = None
    revenue_col = None
    doc_type_col = None

    for c in range(min(len(df_raw.columns), 100)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()

        if 'артикул поставщика' == header or 'артикул' == header:
            sku_col = c
        elif 'к перечислению продавцу' in header:
            revenue_col = c
        elif header in ['тип документа', 'обоснование для оплаты']:
            doc_type_col = c

    if debug:
        st.write(f"📋 Колонки: Артикул={sku_col}, Выручка={revenue_col}, Тип={doc_type_col}")

    if sku_col is None or revenue_col is None:
        if debug:
            st.error(f"❌ Не найдены колонки: Артикул={sku_col}, Выручка={revenue_col}")
        return None

    # ===== Шаг 3: Собираем данные =====
    result_data = []

    for r in range(header_row + 1, len(df_raw)):
        # Проверяем тип документа (если есть колонка)
        if doc_type_col is not None:
            if doc_type_col < len(df_raw.columns):
                doc_type = str(df_raw.iloc[r, doc_type_col]).strip().lower()
                # Берем только строки с "Продажа"
                if doc_type != 'продажа':
                    continue

        # Получаем артикул
        if sku_col >= len(df_raw.columns):
            continue

        sku_value = df_raw.iloc[r, sku_col]

        # Проверяем валидность
        if not is_valid_sku(sku_value):
            continue

        sku = str(sku_value).strip().upper()

        # Получаем выручку
        if revenue_col < len(df_raw.columns):
            revenue = to_num(df_raw.iloc[r, revenue_col])
        else:
            revenue = 0.0

        if revenue > 0:
            result_data.append({
                'Артикул': sku,
                'Выручка': revenue,
                'Маркетплейс': 'WB'
            })

            if debug and len(result_data) <= 5:
                st.write(f"  ✅ Строка {r}: Артикул='{sku}', Выручка={revenue}")

    if debug:
        st.write(f"📊 Найдено записей: {len(result_data)}")

    if not result_data:
        return None

    return pd.DataFrame(result_data)


# ============ Основная обработка ============

def process_single_file(file_info, debug=False):
    """Обрабатывает один файл"""
    content = file_info['content']
    filename = file_info['name']

    try:
        if filename.lower().endswith(('.xlsx', '.xls')):
            excel_file = pd.ExcelFile(io.BytesIO(content))
            all_results = []

            for sheet_name in excel_file.sheet_names:
                sheet_lower = sheet_name.lower()

                # Пропускаем служебные листы
                skip_keywords = ['биллинг', 'взаиморасчет', 'перевыставлен',
                                 'лояльност', 'штраф', 'механик', 'суммах услуг']
                if any(keyword in sheet_lower for keyword in skip_keywords):
                    continue

                try:
                    df_raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name,
                                           header=None, dtype=str)
                except:
                    continue

                if df_raw.empty or len(df_raw.columns) < 2:
                    continue

                # Определяем тип отчета
                result = None

                # Сначала проверяем Ozon
                if find_header_row_ozon(df_raw) is not None:
                    result = process_ozon_report(df_raw)
                    if result is None:
                        result = process_ozon_compensation(df_raw)
                    if result is not None and not result.empty:
                        st.success(f"✅ {filename} / {sheet_name} → Ozon")
                else:
                    # Если не Ozon, пробуем WB
                    if debug:
                        debug_wb_raw(df_raw, sheet_name)
                    result = process_wb_report(df_raw, debug=debug, sheet_name=sheet_name)
                    if result is not None and not result.empty:
                        st.success(f"✅ {filename} / {sheet_name} → Wildberries")

                if result is not None and not result.empty:
                    all_results.append(result)

            if all_results:
                return pd.concat(all_results, ignore_index=True)

    except Exception as e:
        st.error(f"Ошибка обработки {filename}: {str(e)[:200]}")

    return None


# --- UI ---
init_db()
st.title("💎 Ecom Insight Pro v3.0")
st.markdown("""
**Поддерживаемые маркетплейсы:** Ozon | Wildberries  
**Форматы:** Excel (.xlsx, .xls), CSV, ZIP архивы
""")

debug_mode = st.checkbox("🔍 Режим отладки (показать структуру WB отчетов)", value=True)

with st.expander("📋 Правила обработки", expanded=False):
    st.markdown("""
    **Ozon:**
    - Поиск связки 'Артикул + SKU' в заголовках
    - Чтение данных со 2-й строки после заголовков
    - Выручка из колонки 'Итого к начислению'

    **Wildberries:**
    - Поиск 'Артикул поставщика' и 'К перечислению Продавцу'
    - Только строки с типом 'Продажа'
    - Выручка из колонки 'К перечислению Продавцу'
    """)

uploaded_files = st.file_uploader(
    "Загрузите файлы или архивы",
    accept_multiple_files=True,
    type=['xlsx', 'xls', 'csv', 'zip']
)

if uploaded_files:
    with st.spinner('Распаковываю архивы...'):
        all_files = extract_archives(uploaded_files)

    if not all_files:
        st.error("Не найдено поддерживаемых файлов")
        st.stop()

    st.info(f"Найдено файлов для обработки: {len(all_files)}")

    all_data = []
    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, file_info in enumerate(all_files):
        status_text.text(f"Обрабатываю: {file_info['name']}")
        result = process_single_file(file_info, debug=debug_mode)

        if result is not None and not result.empty:
            all_data.append(result)

        progress_bar.progress((i + 1) / len(all_files))

    status_text.text("Обработка завершена!")

    if all_data:
        full_df = pd.concat(all_data, ignore_index=True)

        # Группируем
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

        # Расчеты
        full_df['Налог'] = full_df['Выручка'] * 0.06
        full_df['Прибыль'] = full_df['Выручка'] - full_df['Себестоимость'] - full_df['Налог']

        for col in ['Выручка', 'Себестоимость', 'Налог', 'Прибыль']:
            full_df[col] = full_df[col].round(2)

        full_df = full_df.sort_values('Прибыль', ascending=False)

        # Сводка
        st.divider()
        st.subheader("📊 Сводка по маркетплейсам")

        mp_summary = full_df.groupby('Маркетплейс').agg({
            'Выручка': 'sum',
            'Себестоимость': 'sum',
            'Налог': 'sum',
            'Прибыль': 'sum'
        }).round(2)

        if len(mp_summary) > 0:
            cols_mp = st.columns(len(mp_summary))

            for idx, (mp, row) in enumerate(mp_summary.iterrows()):
                with cols_mp[idx]:
                    emoji = "🟦" if mp == 'Ozon' else "🟪"
                    st.markdown(f"{emoji} **{mp}**")
                    st.metric("Выручка", f"{row['Выручка']:,.0f} ₽")
                    st.metric("Прибыль", f"{row['Прибыль']:,.0f} ₽")
                    roi = (row['Прибыль'] / row['Выручка'] * 100) if row['Выручка'] > 0 else 0
                    st.metric("ROI", f"{roi:.1f}%")

        # Общая таблица
        st.divider()
        st.subheader("📋 Общая таблица")

        total_revenue = full_df['Выручка'].sum()
        total_profit = full_df['Прибыль'].sum()

        m1, m2 = st.columns(2)
        m1.metric("💰 Общая выручка", f"{total_revenue:,.0f} ₽")
        m2.metric("📈 Общая прибыль", f"{total_profit:,.0f} ₽")

        st.dataframe(
            full_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                'Выручка': st.column_config.NumberColumn(format='%.2f ₽'),
                'Себестоимость': st.column_config.NumberColumn(format='%.2f ₽'),
                'Налог': st.column_config.NumberColumn(format='%.2f ₽'),
                'Прибыль': st.column_config.NumberColumn(format='%.2f ₽'),
            }
        )

        # Экспорт
        csv_all = full_df.to_csv(index=False)
        st.download_button("📥 Скачать отчет", csv_all, "ecom_insight_report.csv", "text/csv")

    else:
        st.error("❌ Не удалось найти данные в загруженных файлах.")