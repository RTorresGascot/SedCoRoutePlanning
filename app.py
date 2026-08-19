import sys
import os
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
from datetime import datetime, timedelta

# Redirect stdout/stderr to print directly into the Tkinter Text box
class TextRedirector:
    def __init__(self, widget):
        self.widget = widget

    def write(self, str_val):
        self.widget.insert(tk.END, str_val)
        self.widget.see(tk.END)  # Auto-scroll to bottom

    def flush(self):
        pass


def run_routing_script(tab_name, log_box, run_btn):
    """Executes the route optimization pipeline in a background thread."""
    try:
        log_box.insert(tk.END, f"\n=== Starting Optimization for Tab: '{tab_name}' ===\n")
        
        # Import your existing scripts dynamically
        from loadSheets import load_orders_from_sheets, write_remaining_orders_to_sheets, write_route_to_sheets
        from localsolver import solve_multi_vehicle_vrptw
        from Orders import Order
        import math
        import webbrowser
        import folium
        from determineRoute import fetch_osrm_road_geometry
        from collections import defaultdict
        SHEET_NAME = "routetestdata"
        
        # Step 0: Load data
        orderList, skipped_day_orders, boxmax, min_start_minutes, max_end_minutes, run_date = load_orders_from_sheets(
            SHEET_NAME, tab_name=tab_name
        )

        origin_coords = (18.411141480447743, -66.194513270137)
        midnight_run_date = run_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # Step 1: Consolidate orders
        grouped = defaultdict(lambda: {
            "boxes": 0, 
            "box_list": [], 
            "ids": [], 
            "time_window": (0, 1440), 
            "lat": 0.0, 
            "lng": 0.0, 
            "name": "", 
            "city": "", 
            "bin": ""
        })

        for o in orderList:
            key = (o.customer, o.latitude, o.longitude)
            tw = getattr(o, "time_window", getattr(o, "timewindow", (0, 1440)))
            grouped[key]["name"] = o.customer
            grouped[key]["city"] = getattr(o, "city", "")
            grouped[key]["bin"] = getattr(o, "bin_location", "")
            grouped[key]["lat"] = o.latitude
            grouped[key]["lng"] = o.longitude
            grouped[key]["boxes"] += o.boxes
            grouped[key]["box_list"].append(o.boxes)
            grouped[key]["ids"].append(o.order_id)
            grouped[key]["time_window"] = (
                max(grouped[key]["time_window"][0], tw[0]),
                min(grouped[key]["time_window"][1], tw[1])
            )

        consolidated_orders = []
        for key, data in grouped.items():
            order_obj = Order.create(
                order_id=data["ids"],
                customer=data["name"],
                boxes=data["boxes"],
                latitude=data["lat"],
                longitude=data["lng"],
                timewindow=data["time_window"],
                city=data["city"],
                bin_location=data["bin"]
            )
            order_obj.box_list = data["box_list"]
            consolidated_orders.append(order_obj)

        # Step 2: Solve VRPTW
        total_boxes = sum(o.boxes for o in consolidated_orders)
        estimated_vehicles = max(1, math.ceil(total_boxes / boxmax))

        routes = solve_multi_vehicle_vrptw(
            origin_coords, consolidated_orders, boxmax,
            num_vehicles=estimated_vehicles, window_start_min=min_start_minutes, window_end_min=max_end_minutes
        )

        assigned_orders = set()
        all_generated_routes = []
        route_colors = ["blue", "green", "purple", "orange", "darkred", "cadetblue"]

        if routes:
            valid_route_counter = 1
            for scheduled_route, depart_minute, return_minute in routes:
                if not scheduled_route:
                    continue

                route_boxes = sum(order.boxes for order, _ in scheduled_route)
                departure_time = midnight_run_date + timedelta(minutes=depart_minute)
                return_time = midnight_run_date + timedelta(minutes=return_minute)

                print(f"\n=== Route #{valid_route_counter} Scheduled ({route_boxes}/{boxmax} boxes) ===")
                print(f"  🚚 Departure: {departure_time.strftime('%I:%M %p')} | Return: {return_time.strftime('%I:%M %p')}")

                for rank, (order, arrival_minute) in enumerate(scheduled_route, start=1):
                    assigned_orders.add(order)
                    eta_time = midnight_run_date + timedelta(minutes=arrival_minute)
                    id_str = ", ".join(map(str, order.order_id)) if isinstance(order.order_id, list) else str(order.order_id)
                    print(f"  Stop #{rank}: {order.customer} (Invoices: {id_str}) - ETA: {eta_time.strftime('%I:%M %p')}")

                write_route_to_sheets(SHEET_NAME, scheduled_route, depart_minute=depart_minute)
                all_generated_routes.append((valid_route_counter, scheduled_route, depart_minute, return_minute))
                valid_route_counter += 1

        # Step 3: Write remaining orders
        all_remaining_records = list(skipped_day_orders)
        unassigned_orders = [o for o in consolidated_orders if o not in assigned_orders]
        for order in unassigned_orders:
            id_str = ", ".join(map(str, order.order_id)) if isinstance(order.order_id, list) else str(order.order_id)
            all_remaining_records.append({
                "order_id": id_str, "customer": order.customer, "boxes": order.boxes,
                "reason": "Could not fit within vehicle capacity or shift time limits"
            })

        write_remaining_orders_to_sheets(SHEET_NAME, all_remaining_records)

        # Step 4: Map Visualization
        if all_generated_routes:
            m = folium.Map(location=origin_coords, zoom_start=10, tiles="OpenStreetMap")
            folium.Marker(origin_coords, popup="Depot", icon=folium.Icon(color="red", icon="home", prefix="fa")).add_to(m)

            for r_num, route, depart_min, return_min in all_generated_routes:
                color = route_colors[(r_num - 1) % len(route_colors)]
                optimized_orders = [item[0] for item in route]
                unique_ordered_coords = [(o.latitude, o.longitude) for o in optimized_orders]
                route_waypoints = [origin_coords] + unique_ordered_coords + [origin_coords]
                road_path_points = fetch_osrm_road_geometry(route_waypoints)

                for rank, (order, arrival_minute) in enumerate(route, start=1):
                    eta_time = midnight_run_date + timedelta(minutes=arrival_minute)
                    id_str = ", ".join(map(str, order.order_id)) if isinstance(order.order_id, list) else str(order.order_id)
                    folium.Marker(
                        location=(order.latitude, order.longitude),
                        popup=f"Route #{r_num} Stop #{rank}: {order.customer}<br>ETA: {eta_time.strftime('%I:%M %p')}",
                        tooltip=f"R#{r_num} Stop #{rank}: {order.customer}",
                        icon=folium.Icon(color=color, icon="info-sign")
                    ).add_to(m)

                folium.PolyLine(locations=road_path_points, color=color, weight=5, opacity=0.8).add_to(m)

            m.save("optimized_route.html")
            webbrowser.open("optimized_route.html")

        print("\n✅ Process Completed Successfully!")

    except Exception as e:
        print(f"\n❌ Error encountered during execution: {e}")
    finally:
        run_btn.config(state=tk.NORMAL)


def start_processing():
    tab_name = tab_entry.get().strip()
    if not tab_name:
        log_box.insert(tk.END, "⚠️ Please enter a sheet tab name before running.\n")
        return

    run_btn.config(state=tk.DISABLED)
    
    # Run in a background thread so the UI window doesn't freeze during optimization
    thread = threading.Thread(target=run_routing_script, args=(tab_name, log_box, run_btn))
    thread.daemon = True
    thread.start()


# --- GUI WINDOW BUILD ---
root = tk.Tk()
root.title("Route Optimizer Dashboard")
root.geometry("750x550")

# Input Frame
input_frame = ttk.Frame(root, padding=10)
input_frame.pack(fill=tk.X)

ttk.Label(input_frame, text="Google Sheet Tab Name:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky=tk.W, padx=5)
tab_entry = ttk.Entry(input_frame, width=30, font=("Arial", 10))
tab_entry.insert(0, "Orders")
tab_entry.grid(row=0, column=1, padx=5)

run_btn = ttk.Button(input_frame, text="Generate Routes", command=start_processing)
run_btn.grid(row=0, column=2, padx=10)

# Console Output Box
output_frame = ttk.LabelFrame(root, text=" Optimization Output Log ", padding=10)
output_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

log_box = scrolledtext.ScrolledText(output_frame, wrap=tk.WORD, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff00")
log_box.pack(fill=tk.BOTH, expand=True)

# Redirect standard output (print statements) to the GUI text box
sys.stdout = TextRedirector(log_box)
sys.stderr = TextRedirector(log_box)

root.mainloop()