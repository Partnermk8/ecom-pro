import streamlit as st
import pandas as pd
import sqlite3
import io
import re
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

    # Содержит хотя бы одну букву (русскую или английскую)
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


def find_header_row(df_raw):
    """
    Находит строку с заголовками, где есть связка "Артикул + SKU"
    """
    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns) - 1):
            val = str(df_raw.iloc[r, c]).strip().lower()
            next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

            if val == "артикул" and next_val == "sku":
                return r

    return None


def find_revenue_column(df_raw, header_row, sku_col):
    """
    Находит колонку с итоговой выручкой ("Итого к начислению")
    """
    # Ищем по заголовку
    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if 'итого к начислению' in header:
            return c

    # Если не нашли по заголовку, ищем числовую колонку после артикула
    # В стандартном отчете Ozon "Итого к начислению" - это 13-я колонка (индекс 12)
    # После артикула идет 10 колонок
    if sku_col is not None:
        potential_col = sku_col + 10
        if potential_col < len(df_raw.columns):
            # Проверяем, что там числа
            sample_count = 0
            for r in range(header_row + 2, min(header_row + 10, len(df_raw))):
                val = to_num(df_raw.iloc[r, potential_col])
                if val > 0:
                    sample_count += 1
            if sample_count >= 2:  # Минимум 2 строки с числами
                return potential_col

    return None


def process_ozon_report(df_raw):
    """
    Обрабатывает отчет Ozon по правилу:
    1. Найти строку с "Артикул + SKU"
    2. Данные читать со 2-й строки после заголовков
    3. Артикул = буквы + цифры
    4. Выручка из колонки "Итого к начислению"
    """
    # Шаг 1: Ищем строку с заголовками
    header_row = find_header_row(df_raw)
    if header_row is None:
        return None

    # Определяем индексы колонок
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
    revenue_col = find_revenue_column(df_raw, header_row, sku_col)
    if revenue_col is None:
        return None

    # Шаг 2: Читаем данные со 2-й строки после заголовков
    data_start = header_row + 2

    result_data = []

    for r in range(data_start, len(df_raw)):
        sku_value = df_raw.iloc[r, sku_col]

        # Шаг 3: Проверяем, что это валидный артикул (буквы + цифры)
        if not is_valid_sku(sku_value):
            continue

        sku = str(sku_value).strip()

        # Получаем выручку
        revenue = to_num(df_raw.iloc[r, revenue_col])

        if revenue >= 0:  # Включаем и нулевую выручку
            result_data.append({
                'Артикул': sku,
                'Выручка': revenue,
                'Маркетплейс': 'Ozon'
            })

    if not result_data:
        return None

    return pd.DataFrame(result_data)


def process_compensation_report(df_raw):
    """
    Обрабатывает отчет о компенсациях Ozon
    """
    # Ищем строку с заголовками
    header_row = None

    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns)):
            val = str(df_raw.iloc[r, c]).strip().lower()
            if val == "артикул":
                # Проверяем контекст - это компенсационный отчет?
                for check_r in range(max(0, r - 3), r):
                    for check_c in range(len(df_raw.columns)):
                        cell_text = str(df_raw.iloc[check_r, check_c]).lower()
                        if 'компенсац' in cell_text:
                            header_row = r
                            break
                    if header_row is not None:
                        break

                if header_row is None:
                    # Если не нашли "компенсация", но есть "Артикул" и "SKU"
                    for c2 in range(len(df_raw.columns)):
                        if str(df_raw.iloc[r, c2]).strip().lower() == 'sku':
                            header_row = r
                            break

                if header_row is not None:
                    break
        if header_row is not None:
            break

    if header_row is None:
        # Пробуем найти просто строку с "Артикул"
        for r in range(min(len(df_raw), 100)):
            for c in range(len(df_raw.columns)):
                if str(df_raw.iloc[r, c]).strip().lower() == 'артикул':
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
        elif "итого к начислению" in header or "компенсац" in header:
            # Для компенсаций может быть другая колонка
            if revenue_col is None:
                revenue_col = c

    if sku_col is None:
        return None

    # Если не нашли колонку с выручкой, ищем числовую колонку в конце
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

        # Проверяем валидность артикула
        if not is_valid_sku(sku_value):
            # Проверяем, не закончились ли данные
            if r > header_row + 1:
                # Если это не артикул, и предыдущая строка тоже не была артикулом
                prev_sku = df_raw.iloc[r - 1, sku_col]
                if not is_valid_sku(prev_sku):
                    break
            continue

        sku = str(sku_value).strip()
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


def process_report_multiple_sheets(content, filename):
    """Обрабатывает Excel файл с несколькими листами"""
    try:
        excel_file = pd.ExcelFile(io.BytesIO(content))
        all_data = []

        # Фильтруем листы
        relevant_sheets = []

        for sheet_name in excel_file.sheet_names:
            sheet_lower = sheet_name.lower()

            # Пропускаем явно ненужные листы
            skip_keywords = ['биллинг', 'взаиморасчет', 'перевыставлен', 'лояльност',
                             'штраф', 'механик', 'суммах услуг']
            if any(keyword in sheet_lower for keyword in skip_keywords):
                continue

            relevant_sheets.append(sheet_name)

        # Обрабатываем каждый лист
        for sheet_name in relevant_sheets:
            df_raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name,
                                   header=None, dtype=str)

            # Пробуем разные обработчики
            result = process_ozon_report(df_raw)
            if result is None:
                result = process_compensation_report(df_raw)

            if result is not None and not result.empty:
                st.success(f"✅ Найдены данные в файле '{filename}', лист '{sheet_name}'")
                all_data.append(result)

        if all_data:
            return pd.concat(all_data, ignore_index=True)
        return None

    except Exception as e:
        st.error(f"Ошибка обработки файла {filename}: {e}")
        return None


# --- UI ---
init_db()
st.title("💎 Ecom Insight Pro v3.0")
st.markdown("""
**Алгоритм обработки:**
- 🔍 Поиск связки 'Артикул + SKU' в заголовках
- 📖 Чтение данных со 2-й строки после заголовков
- ✅ Артикул = буквы + цифры (только цифры не подходят)
""")

uploaded_files = st.file_uploader("Загрузите Excel или CSV", accept_multiple_files=True,
                                  type=['xlsx', 'xls', 'csv'])

if uploaded_files:
    all_data = []

    # Показываем прогресс обработки
    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, f in enumerate(uploaded_files):
        status_text.text(f"Обрабатываю: {f.name}")
        content = f.read()
        result = process_report_multiple_sheets(content, f.name)

        if result is not None and not result.empty:
            all_data.append(result)

        progress_bar.progress((i + 1) / len(uploaded_files))

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
                    cols_per_row = 3
                    missing_list = list(missing)

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

        # Отображаем результаты
        st.divider()
        st.subheader("📊 Финансовые результаты")

        total_revenue = full_df['Выручка'].sum()
        total_profit = full_df['Прибыль'].sum()
        total_cost = full_df['Себестоимость'].sum()
        total_tax = full_df['Налог'].sum()
        roi = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("💰 Выручка", f"{total_revenue:,.0f} ₽")
        m2.metric("📦 Себестоимость", f"{total_cost:,.0f} ₽")
        m3.metric("💸 Налог", f"{total_tax:,.0f} ₽")
        m4.metric("📈 Прибыль", f"{total_profit:,.0f} ₽",
                  delta=f"ROI: {roi:.1f}%")

        # Таблица с результатами
        st.subheader("📋 Детальная таблица")
        st.dataframe(
            full_df.style.format({
                'Выручка': '{:,.2f} ₽',
                'Себестоимость': '{:,.2f} ₽',
                'Налог': '{:,.2f} ₽',
                'Прибыль': '{:,.2f} ₽'
            }).apply(lambda x: ['background-color: #ffcccc' if x['Прибыль'] < 0 else '' for _ in x], axis=1),
            use_container_width=True,
            hide_index=True
        )

        # Дополнительная аналитика
        with st.expander("📊 Расширенная аналитика"):
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
                st.write(f"Прибыльных товаров: {len(full_df[full_df['Прибыль'] > 0])}")
                st.write(f"Убыточных товаров: {len(full_df[full_df['Прибыль'] < 0])}")
                st.write(f"Средняя маржинальность: {(total_profit / total_revenue * 100):.1f}%")

        # Возможность скачать результаты
        csv = full_df.to_csv(index=False)
        st.download_button(
            label="📥 Скачать отчет",
            data=csv,
            file_name='ecom_insight_report.csv',
            mime='text/csv'
        )

    else:
        st.error("❌ Не удалось найти данные в загруженных файлах.")
        st.info("""
        **Проверьте, что файлы содержат:**
        - Связку 'Артикул + SKU' в заголовках
        - Артикулы в формате: буквы + цифры (например: КБ-25, РБ-3)
        - Колонку 'Итого к начислению'

        **Поддерживаемые форматы:** Excel (.xlsx, .xls), CSV
        **Типы отчетов:** Отчеты о реализации и компенсациях Ozon
        """)