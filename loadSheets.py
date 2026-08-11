import os
import time
from datetime import datetime, timedelta

import gspread
from gspread.exceptions import APIError
from gspread_formatting import (
    Border,
    Borders,
    CellFormat,
    Color,
    TextFormat,
    format_cell_range,
    set_column_width,
)
from Orders import Order


def api_retry(func, max_retries=5, initial_delay=5.0):
    """Retries a gspread API call if it encounters a 429 Quota Exceeded error."""
    delay = initial_delay
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except APIError as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            is_rate_limit = status_code == 429 or "429" in str(e) or "Quota exceeded" in str(e)

            if is_rate_limit:
                if attempt == max_retries:
                    raise e
                print(f"⚠️ Google Sheets Rate Limit Hit (429). Waiting {delay:.1f}s before retry ({attempt}/{max_retries})...")
                time.sleep(delay)
                delay *= 2
            else:
                raise e


def convertStringToTime(time_str: str) -> int:
    """Converts a 'HH:MM:SS' or 'HH:MM' string to total minutes from midnight."""
    if not time_str:
        return 0
    try:
        dt = datetime.strptime(time_str, "%H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return 0
    return dt.hour * 60 + dt.minute


def convertStringToDate(date_str: str) -> datetime:
    """Parses date strings formatted as Month, Day, Year or returns datetime.now() if blank/invalid."""
    if not date_str:
        return datetime.now()

    cleaned_str = str(date_str).strip()

    formats_to_try = [
        "%m/%d/%Y",      # 8/11/2026 or 08/11/2026
        "%m-%d-%Y",      # 8-11-2026 or 08-11-2026
        "%m/%d/%y",      # 8/11/26
        "%B %d, %Y",     # August 11, 2026
        "%b %d, %Y",     # Aug 11, 2026
    ]

    for fmt in formats_to_try:
        try:
            return datetime.strptime(cleaned_str, fmt)
        except ValueError:
            continue

    print(f"Warning: Could not parse date string '{date_str}'. Falling back to current system date.")
    return datetime.now()


def is_order_allowed_today(delivery_days_str: str, target_date: datetime = None) -> bool:
    """
    Checks if an order's delivery days string permits delivery on target_date.
    Examples of allowed day strings: 'Mon, Wed, Fri', 'Monday', 'Weekdays', 'Anyday', ''
    """
    if not delivery_days_str or delivery_days_str.strip().lower() in ["any", "anyday", "all", ""]:
        return True

    if target_date is None:
        target_date = datetime.now()

    today_name = target_date.strftime("%A").lower()  # e.g., 'monday'
    today_short = target_date.strftime("%a").lower()  # e.g., 'mon'

    days_lower = delivery_days_str.lower()

    if "weekday" in days_lower and target_date.weekday() < 5:
        return True
    if "weekend" in days_lower and target_date.weekday() >= 5:
        return True

    return today_name in days_lower or today_short in days_lower


def load_orders_from_sheets(sheet_name: str, tab_name: str = "Orders", creds_file: str = "credentials.json", run_date: datetime = None):
    """
    Loads orders, max box capacity, shift windows, and targeted route date from Google Sheets.
    Control Cells in Google Sheets:
      - J2: Max Box Capacity (e.g. 50)
      - J4: Min Start Time / Earliest Departure (e.g. 06:00)
      - J5: Max End Time / Latest Return (e.g. 17:00)
      - J6: Route Target Date (e.g. 8/11/2026)
    """
    if not os.path.exists(creds_file):
        raise FileNotFoundError(f"Credentials file '{creds_file}' not found.")

    gc = gspread.service_account(filename=creds_file)
    spreadsheet = api_retry(lambda: gc.open(sheet_name))

    try:
        sheet = spreadsheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"Warning: Tab '{tab_name}' not found in '{sheet_name}'. Falling back to the first sheet.")
        sheet = spreadsheet.sheet1

    # 1. Fetch Control Parameters (J2, J4, J5)
    try:
        raw_boxmax = api_retry(lambda: sheet.acell("J2").value)
        boxmax = int(raw_boxmax) if raw_boxmax else 50
    except (ValueError, TypeError):
        boxmax = 50

    raw_min_start = api_retry(lambda: sheet.acell("J4").value)
    min_start_minutes = convertStringToTime(raw_min_start) if raw_min_start else 360

    raw_max_end = api_retry(lambda: sheet.acell("J5").value)
    max_end_minutes = convertStringToTime(raw_max_end) if raw_max_end else 1020

    # 2. Fetch Route Target Date from Cell J6
    raw_target_date = api_retry(lambda: sheet.acell("J6").value)
    
    if run_date is None:
        run_date = convertStringToDate(raw_target_date)

    print(f"🗓️ Route Execution Date set to: {run_date.strftime('%A, %B %d, %Y')}")

    rows = api_retry(lambda: sheet.get_all_values())
    if not rows or len(rows) < 2:
        return [], [], boxmax, min_start_minutes, max_end_minutes, run_date

    header_row_index = 0
    for idx, row in enumerate(rows):
        row_str = " ".join(row).lower()
        if "order number" in row_str or "invoice" in row_str or "customer" in row_str:
            header_row_index = idx
            break

    headers = [str(h).strip().lower() for h in rows[header_row_index]]
    active_order_list = []
    skipped_orders_data = []

    for row_idx, row in enumerate(rows[header_row_index + 1:], start=header_row_index + 2):
        if not any(row):
            continue

        row_dict = {headers[i]: row[i].strip() for i in range(min(len(headers), len(row))) if headers[i]}

        try:
            order_id = int(
                row_dict.get("order number") 
                or row_dict.get("invoice") 
                or row_dict.get("order_id") 
                or 0
            )
            customer = str(row_dict.get("customer name") or row_dict.get("customer") or "")
            boxes = int(row_dict.get("boxes") or 0)
            latitude = float(row_dict.get("latitude") or 0.0)
            longitude = float(row_dict.get("longitude") or 0.0)

            delivery_days = row_dict.get("delivery days") or row_dict.get("deliverydays") or row_dict.get("days") or ""

            raw_start = row_dict.get("deliver time start") or row_dict.get("delivertimestart")
            raw_end = row_dict.get("deliver time end") or row_dict.get("delivertimeend")

            timewindow = (0, 1440)
            if raw_start and raw_end:
                timewindow = (convertStringToTime(raw_start), convertStringToTime(raw_end))

            order = Order.create(
                order_id=order_id,
                customer=customer,
                boxes=boxes,
                latitude=latitude,
                longitude=longitude,
                timewindow=timewindow
            )

            if is_order_allowed_today(delivery_days, run_date):
                active_order_list.append(order)
            else:
                skipped_orders_data.append({
                    "order_id": order_id,
                    "customer": customer,
                    "boxes": boxes,
                    "reason": f"Not scheduled for delivery on {run_date.strftime('%A')} ({delivery_days})"
                })

        except (ValueError, TypeError) as e:
            print(f"Skipping row {row_idx} due to parsing error: {e}")

    return active_order_list, skipped_orders_data, boxmax, min_start_minutes, max_end_minutes, run_date


def write_remaining_orders_to_sheets(sheet_name: str, remaining_orders: list, creds_file: str = "credentials.json"):
    """
    Clears and populates the 'Remaining orders' tab with all orders that were skipped
    due to off-day schedules or capacity/time constraints.
    """
    if not os.path.exists(creds_file):
        raise FileNotFoundError(f"Credentials file '{creds_file}' not found.")

    gc = gspread.service_account(filename=creds_file)
    spreadsheet = api_retry(lambda: gc.open(sheet_name))

    target_tab_name = "Remaining orders"
    worksheets = api_retry(lambda: spreadsheet.worksheets())
    existing_worksheets = {ws.title: ws for ws in worksheets}

    if target_tab_name in existing_worksheets:
        worksheet = existing_worksheets[target_tab_name]
        api_retry(lambda: worksheet.clear())
    else:
        worksheet = api_retry(lambda: spreadsheet.add_worksheet(title=target_tab_name, rows=500, cols=10))

    headers = ["Order ID / Invoice", "Customer Name", "Boxes", "Reason Not Scheduled"]
    table_data = [headers]

    for item in remaining_orders:
        table_data.append([
            str(item.get("order_id", "")),
            str(item.get("customer", "")),
            str(item.get("boxes", "")),
            str(item.get("reason", ""))
        ])

    api_retry(lambda: worksheet.update(values=table_data, range_name="A1"))

    # Header Styling (FIXED: foregroundColor instead of color)
    std_border = Border(style="SOLID", color=Color(0, 0, 0))
    inner_grid = Borders(top=std_border, bottom=std_border, left=std_border, right=std_border)

    header_format = CellFormat(
        backgroundColor=Color(0.9, 0.4, 0.4),
        textFormat=TextFormat(bold=True, foregroundColor=Color(1, 1, 1)),
        borders=inner_grid
    )
    api_retry(lambda: format_cell_range(worksheet, "A1:D1", header_format))

    api_retry(lambda: set_column_width(worksheet, "A", 140))
    api_retry(lambda: set_column_width(worksheet, "B", 350))
    api_retry(lambda: set_column_width(worksheet, "C", 90))
    api_retry(lambda: set_column_width(worksheet, "D", 350))

    print(f"Successfully wrote {len(remaining_orders)} remaining orders to tab '{target_tab_name}'.")


def write_route_to_sheets(sheet_name: str, scheduled_route: list, depart_minute: int = 480, creds_file: str = "credentials.json"):
    """Appends new routes including ETA at destination and expanded customer name column."""
    if not scheduled_route:
        return

    if not os.path.exists(creds_file):
        raise FileNotFoundError(f"Credentials file '{creds_file}' not found.")

    gc = gspread.service_account(filename=creds_file)
    spreadsheet = api_retry(lambda: gc.open(sheet_name))

    target_tab_name = "Routes"
    worksheets = api_retry(lambda: spreadsheet.worksheets())
    existing_worksheets = {ws.title: ws for ws in worksheets}
    
    if target_tab_name in existing_worksheets:
        worksheet = existing_worksheets[target_tab_name]
    else:
        worksheet = api_retry(lambda: spreadsheet.add_worksheet(title=target_tab_name, rows=500, cols=10))

    existing_rows = api_retry(lambda: worksheet.get_all_values())
    is_empty = len(existing_rows) == 0

    midnight_today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    depart_time_str = (midnight_today + timedelta(minutes=depart_minute)).strftime("%I:%M %p")

    headers = ["Type", "Projected ETA", "Invoices", "Customer", "Boxes", "Total Boxes", "route start"]

    if is_empty:
        header_row_idx = 1
        current_row = 2
        table_data = [headers]
    else:
        header_row_idx = len(existing_rows) + 3
        current_row = header_row_idx + 1
        table_data = [["", "", "", "", "", "", ""], ["", "", "", "", "", "", ""], headers]

    time_row_idx = current_row
    data_row_indices = []
    grand_total_boxes = 0

    for rank, (order, arrival_minute) in enumerate(scheduled_route, start=1):
        tw = getattr(order, "time_window", getattr(order, "timewindow", (0, 1440)))
        delivery_type = "Anytime" if tw == (0, 1440) else f"{tw[0]}m - {tw[1]}m"

        eta_time_str = (midnight_today + timedelta(minutes=arrival_minute)).strftime("%I:%M %p")
        customer_name = order.customer
        total_boxes_sum = order.boxes
        grand_total_boxes += total_boxes_sum

        if isinstance(order.order_id, list) and hasattr(order, "box_list"):
            total_invoices = len(order.order_id)
            for idx, (inv_id, ind_boxes) in enumerate(zip(order.order_id, order.box_list)):
                total_boxes_display = f"{total_boxes_sum} BOXES" if (idx == total_invoices - 1) else ""
                col_g_val = depart_time_str if current_row == time_row_idx else ""
                
                table_data.append([
                    delivery_type,
                    eta_time_str,
                    str(inv_id),
                    customer_name,
                    ind_boxes,
                    total_boxes_display,
                    col_g_val
                ])
                data_row_indices.append(current_row)
                current_row += 1
        else:
            col_g_val = depart_time_str if current_row == time_row_idx else ""
            table_data.append([
                delivery_type,
                eta_time_str,
                str(order.order_id),
                customer_name,
                order.boxes,
                f"{total_boxes_sum} BOXES",
                col_g_val
            ])
            data_row_indices.append(current_row)
            current_row += 1

        if rank < len(scheduled_route):
            table_data.append(["", "", "", "", "", "", ""])
            current_row += 1

    # Append bottom summary row for total box count
    total_summary_row = current_row
    table_data.append(["", "", "", "", "TOTAL BOXES:", f"{grand_total_boxes} BOXES", ""])

    append_start_row = 1 if is_empty else len(existing_rows) + 1
    total_rows = append_start_row + len(table_data) - 1

    api_retry(lambda: worksheet.update(values=table_data, range_name=f"A{append_start_row}"))

    # Borders Setup
    std_border = Border(style="SOLID", color=Color(0, 0, 0))
    thick_border = Border(style="SOLID_THICK", color=Color(0, 0, 0))
    inner_grid = Borders(top=std_border, bottom=std_border, left=std_border, right=std_border)

    # 1. Yellow formatting for Headers (Columns A through F)
    yellow_header_format = CellFormat(
        backgroundColor=Color(1, 1, 0),
        textFormat=TextFormat(bold=True),
        borders=inner_grid
    )
    api_retry(lambda: format_cell_range(worksheet, f"A{header_row_idx}:F{header_row_idx}", yellow_header_format))

    # 2. Format Column G: "route start" text cell and departure time cell below it
    g_text_format = CellFormat(
        textFormat=TextFormat(bold=True),
        borders=Borders(bottom=std_border)
    )
    api_retry(lambda: format_cell_range(worksheet, f"G{header_row_idx}", g_text_format))

    g_time_format = CellFormat(
        textFormat=TextFormat(bold=True)
    )
    api_retry(lambda: format_cell_range(worksheet, f"G{time_row_idx}", g_time_format))

    # 3. Apply thick outer border box enclosing G{header_row_idx} and G{time_row_idx} together
    thick_box = CellFormat(
        borders=Borders(
            top=thick_border,
            bottom=thick_border,
            left=thick_border,
            right=thick_border
        )
    )
    api_retry(lambda: format_cell_range(worksheet, f"G{header_row_idx}:G{time_row_idx}", thick_box))

    # 4. Format order data rows
    if data_row_indices:
        min_row, max_row = min(data_row_indices), max(data_row_indices)
        order_format = CellFormat(backgroundColor=Color(0.88, 0.88, 0.88), textFormat=TextFormat(bold=True), borders=inner_grid)
        total_format = CellFormat(backgroundColor=Color(0.88, 0.88, 0.88), textFormat=TextFormat(bold=True, foregroundColor=Color(0.8, 0, 0)), borders=inner_grid)
        
        api_retry(lambda: format_cell_range(worksheet, f"A{min_row}:E{max_row}", order_format))
        api_retry(lambda: format_cell_range(worksheet, f"F{min_row}:F{max_row}", total_format))

    # 5. Format Bottom Grand Total Row (Red Text & Thick Border Outline around F)
    grand_total_label_format = CellFormat(
        textFormat=TextFormat(bold=True)
    )
    api_retry(lambda: format_cell_range(worksheet, f"E{total_summary_row}", grand_total_label_format))

    grand_total_box_format = CellFormat(
        textFormat=TextFormat(bold=True, foregroundColor=Color(0.8, 0, 0)),
        borders=Borders(
            top=thick_border,
            bottom=thick_border,
            left=thick_border,
            right=thick_border
        )
    )
    api_retry(lambda: format_cell_range(worksheet, f"F{total_summary_row}", grand_total_box_format))

    # Table top bounding border
    api_retry(lambda: format_cell_range(worksheet, f"A{header_row_idx}:F{header_row_idx}", CellFormat(borders=Borders(top=thick_border))))

    # Column Widths
    api_retry(lambda: set_column_width(worksheet, "A", 110))
    api_retry(lambda: set_column_width(worksheet, "B", 130))
    api_retry(lambda: set_column_width(worksheet, "C", 110))
    api_retry(lambda: set_column_width(worksheet, "D", 450))
    api_retry(lambda: set_column_width(worksheet, "E", 110))
    api_retry(lambda: set_column_width(worksheet, "F", 130))
    api_retry(lambda: set_column_width(worksheet, "G", 140))

    print(f"Successfully appended route with ETA column to '{target_tab_name}' at row {header_row_idx}.")
    time.sleep(2.5)