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


def find_sku_and_revenue_columns(df_raw, header_row):
    """
    Находит колонки с артикулом и выручкой на основе заголовков
    Возвращает индексы колонок
    """
    headers = {}
    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if header and header != 'nan':
            headers[c] = header

    # Ищем колонку с артикулом (обычно вторая колонка, "Артикул")
    sku_col = None
    for col_idx, header in headers.items():
        if header == 'артикул':
            sku_col = col_idx
            break

    # Ищем колонку с итоговой выручкой
    # В отчете Ozon это колонка "Итого к начислению, руб." (13-я колонка, индекс 12)
    revenue_col = None

    # Сначала ищем точное совпадение
    for col_idx, header in headers.items():
        if 'итого к начислению' in header:
            revenue_col = col_idx
            break

    # Если не нашли, ищем по номерам колонок из структуры отчета
    if revenue_col is None and sku_col is not None:
        # В стандартном отчете Ozon:
        # Колонка 1 (индекс 0): № п/п
        # Колонка 2 (индекс 1): Название товара
        # Колонка 3 (индекс 2): Артикул
        # Колонка 13 (индекс 12): Итого к начислению, руб.
        potential_revenue_col = sku_col + 10  # 10 колонок после артикула
        if potential_revenue_col < len(df_raw.columns):
            # Проверяем, что в этой колонке есть числовые данные
            sample_values = []
            for r in range(header_row + 2, min(header_row + 5, len(df_raw))):
                val = to_num(df_raw.iloc[r, potential_revenue_col])
                if val > 0:
                    sample_values.append(val)

            if sample_values:
                revenue_col = potential_revenue_col

    return sku_col, revenue_col


def process_ozon_report(df_raw):
    """
    Обрабатывает отчет Ozon в формате "Позаказный отчет о реализации"
    """
    # Ищем строку с заголовками (где есть "Артикул" и "SKU")
    header_row = None

    for r in range(min(len(df_raw), 100)):
        for c in range(len(df_raw.columns) - 1):
            val = str(df_raw.iloc[r, c]).strip().lower()
            next_val = str(df_raw.iloc[r, c + 1]).strip().lower()

            if val == "артикул" and next_val == "sku":
                header_row = r
                break
        if header_row is not None:
            break

    if header_row is None:
        return None

    # Находим нужные колонки
    sku_col, revenue_col = find_sku_and_revenue_columns(df_raw, header_row)

    if sku_col is None:
        return None

    # Если не нашли колонку с выручкой, используем резервный метод
    if revenue_col is None:
        # Ищем любую колонку с числами после 10-й позиции от артикула
        for c in range(sku_col + 8, len(df_raw.columns)):
            sample_val = to_num(df_raw.iloc[header_row + 2, c])
            if sample_val > 0:
                revenue_col = c
                break

    if revenue_col is None:
        return None

    # Собираем данные
    result_data = []

    for r in range(header_row + 2, len(df_raw)):
        sku = str(df_raw.iloc[r, sku_col]).strip()

        # Пропускаем пустые строки и итоги
        if sku.lower() in ['nan', '', 'итого', 'всего']:
            # Проверяем, есть ли еще данные
            has_more_data = False
            for check_r in range(r + 1, min(r + 5, len(df_raw))):
                check_sku = str(df_raw.iloc[check_r, sku_col]).strip()
                if check_sku.lower() not in ['nan', '', 'итого', 'всего']:
                    has_more_data = True
                    break
            if not has_more_data:
                break
            continue

        # Пропускаем слишком длинные числовые строки (например, штрих-коды)
        if sku.isdigit() and len(sku) > 8:
            continue

        # Получаем выручку
        revenue = to_num(df_raw.iloc[r, revenue_col])

        if revenue > 0:  # Добавляем только строки с положительной выручкой
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
                # Проверяем, что рядом есть SKU или это компенсационный отчет
                for c2 in range(len(df_raw.columns)):
                    if "компенсац" in str(df_raw.iloc[max(0, r - 2), c2]).lower():
                        header_row = r
                        break
                if header_row is None:
                    header_row = r
                break
        if header_row is not None:
            break

    if header_row is None:
        return None

    # Находим колонки: артикул и итого к начислению
    sku_col = None
    revenue_col = None

    for c in range(len(df_raw.columns)):
        header = str(df_raw.iloc[header_row, c]).strip().lower()
        if header == "артикул":
            sku_col = c
        elif "итого к начислению" in header:
            revenue_col = c

    if sku_col is None or revenue_col is None:
        return None

    # Собираем данные
    result_data = []

    for r in range(header_row + 1, len(df_raw)):
        sku = str(df_raw.iloc[r, sku_col]).strip()

        if sku.lower() in ['nan', '', 'итого', 'всего', 'всего к начислению:']:
            break

        revenue = to_num(df_raw.iloc[r, revenue_col])

        if revenue > 0:
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

        # Приоритетные листы для обработки
        priority_sheets = []
        other_sheets = []

        for sheet_name in excel_file.sheet_names:
            sheet_lower = sheet_name.lower()
            # Пропускаем заведомо ненужные листы
            skip_keywords = ['биллинг', 'взаиморасчет', 'перевыставлен', 'лояльност', 'штраф', 'механик']
            if any(keyword in sheet_lower for keyword in skip_keywords):
                continue

            # Приоритетные листы с отчетами о реализации
            if any(keyword in sheet_lower for keyword in ['реализац', 'компенсац']):
                priority_sheets.append(sheet_name)
            else:
                other_sheets.append(sheet_name)

        # Обрабатываем сначала приоритетные листы
        for sheet_name in priority_sheets + other_sheets:
            df_raw = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name, header=None, dtype=str)

            # Пробуем разные обработчики
            result = process_ozon_report(df_raw)
            if result is None:
                result = process_compensation_report(df_raw)

            if result is not None and not result.empty:
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
st.markdown("Специальный алгоритм: поиск по связке 'Артикул + SKU' и отступ 2 строки.")

uploaded_files = st.file_uploader("Загрузите Excel или CSV", accept_multiple_files=True,
                                  type=['xlsx', 'xls', 'csv'])

if uploaded_files:
    all_data = []

    with st.spinner('Обрабатываю файлы...'):
        for f in uploaded_files:
            content = f.read()
            result = process_report_multiple_sheets(content, f.name)
            if result is not None and not result.empty:
                all_data.append(result)

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
                    for sku in missing:
                        col1, col2 = st.columns([3, 1])
                        col1.write(f"**{sku}**")
                        new_v = col2.number_input(f"Цена закупки", key=f"cost_{sku}", min_value=0.0, step=100.0)
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

        # Таблица с результатами
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

        # Дополнительная информация
        with st.expander("📊 Детальная аналитика"):
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Топ-5 товаров по прибыли")
                top_products = full_df.nlargest(5, 'Прибыль')[['Артикул', 'Прибыль']]
                st.dataframe(top_products, use_container_width=True, hide_index=True)

            with col2:
                st.subheader("Товары с отрицательной прибылью")
                negative_profit = full_df[full_df['Прибыль'] < 0][['Артикул', 'Прибыль']]
                if not negative_profit.empty:
                    st.dataframe(negative_profit, use_container_width=True, hide_index=True)
                else:
                    st.success("Все товары прибыльные!")

    else:
        st.error("❌ Не удалось найти данные в загруженных файлах.")
        st.info("""
        **Проверьте, что файлы содержат:**
        - Для Ozon: ячейки "Артикул" и "SKU" рядом в одной строке
        - Отчеты о реализации или компенсациях

        **Поддерживаемые форматы:** Excel (.xlsx, .xls), CSV
        """)