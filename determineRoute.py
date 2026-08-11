import json
import math
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta
from urllib import request

import folium
import polyline
from loadSheets import load_orders_from_sheets, write_remaining_orders_to_sheets, write_route_to_sheets
from localsolver import solve_multi_vehicle_vrptw
from Orders import Order


def fetch_osrm_road_geometry(coords):
    """Fetches turn-by-turn road geometry points for ordered coordinates using OSRM."""
    coord_str = ";".join([f"{lng},{lat}" for lat, lng in coords])
    url = f"http://router.project-osrm.org/route/v1/driving/{coord_str}?overview=full&geometries=polyline"

    req = request.Request(url, headers={"User-Agent": "RouteOptimizer/1.0"})

    try:
        with request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            encoded_geometry = data["routes"][0]["geometry"]
            return polyline.decode(encoded_geometry)
    except Exception as e:
        print(f"Failed to fetch road geometry ({e}). Falling back to straight lines.")
        return coords


def run_optimization(tab_name="Orders"):
    SHEET_NAME = "routetestdata"

    # --- STEP 0: FETCH ORDERS AND CONTROL PARAMETERS ---
    orderList, skipped_day_orders, boxmax, min_start_minutes, max_end_minutes, run_date = load_orders_from_sheets(
        SHEET_NAME, tab_name=tab_name
    )

    origin_coords = (18.411141480447743, -66.194513270137)  # Depot location
    midnight_run_date = run_date.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- STEP 1: CONSOLIDATE ACTIVE ORDERS BY CUSTOMER / LOCATION ---
    grouped = defaultdict(lambda: {
        "boxes": 0, 
        "box_list": [], 
        "ids": [], 
        "time_window": (0, 1440), 
        "lat": 0.0, 
        "lng": 0.0, 
        "name": ""
    })

    for o in orderList:
        key = (o.customer, o.latitude, o.longitude)
        tw = getattr(o, "time_window", getattr(o, "timewindow", (0, 1440)))
        
        grouped[key]["name"] = o.customer
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
            timewindow=data["time_window"]
        )
        order_obj.box_list = data["box_list"]
        consolidated_orders.append(order_obj)

    # --- STEP 2: SOLVE FLEET-WIDE ROUTES ---
    total_boxes = sum(o.boxes for o in consolidated_orders)
    estimated_vehicles = max(1, math.ceil(total_boxes / boxmax))

    all_generated_routes = []
    route_colors = ["blue", "green", "purple", "orange", "darkred", "cadetblue"]

    routes = solve_multi_vehicle_vrptw(
        origin_coords, 
        consolidated_orders, 
        boxmax, 
        num_vehicles=estimated_vehicles,
        window_start_min=min_start_minutes,
        window_end_min=max_end_minutes
    )

    assigned_orders = set()

    if routes:
        valid_route_counter = 1
        for scheduled_route, depart_minute, return_minute in routes:
            if not scheduled_route:
                continue

            route_boxes = sum(order.boxes for order, _ in scheduled_route)
            departure_time = midnight_run_date + timedelta(minutes=depart_minute)
            return_time = midnight_run_date + timedelta(minutes=return_minute)

            print(f"\n=== Route #{valid_route_counter} Scheduled ({route_boxes}/{boxmax} boxes) ===")
            print(f"  🗓️ Date: {run_date.strftime('%A, %B %d, %Y')}")
            print(f"  🚚 Departure from Depot: {departure_time.strftime('%I:%M %p')}")
            
            for rank, (order, arrival_minute) in enumerate(scheduled_route, start=1):
                assigned_orders.add(order)
                eta_time = midnight_run_date + timedelta(minutes=arrival_minute)
                
                tw = getattr(order, "time_window", getattr(order, "timewindow", (0, 1440)))
                if tw == (0, 1440):
                    window_str = "Anytime"
                else:
                    win_start = (midnight_run_date + timedelta(minutes=tw[0])).strftime("%I:%M %p")
                    win_end = (midnight_run_date + timedelta(minutes=tw[1])).strftime("%I:%M %p")
                    window_str = f"{win_start} - {win_end}"

                id_str = ", ".join(map(str, order.order_id)) if isinstance(order.order_id, list) else str(order.order_id)

                print(
                    f"  {rank}. {order.customer} (Order IDs: {id_str})"
                    f"\n     ETA: {eta_time.strftime('%I:%M %p')}"
                    f"\n     Boxes: {order.boxes} total | Time Window: {window_str}\n"
                )

            print(f"  🏁 Return to Depot ETA: {return_time.strftime('%I:%M %p')} (Shift Duration: {return_minute - depart_minute} mins)\n")

            write_route_to_sheets(SHEET_NAME, scheduled_route, depart_minute=depart_minute)
            all_generated_routes.append((valid_route_counter, scheduled_route, depart_minute, return_minute))
            valid_route_counter += 1

    # --- STEP 3: CONSOLIDATE & WRITE ALL REMAINING ORDERS ---
    all_remaining_records = list(skipped_day_orders)

    unassigned_orders = [o for o in consolidated_orders if o not in assigned_orders]
    for order in unassigned_orders:
        id_str = ", ".join(map(str, order.order_id)) if isinstance(order.order_id, list) else str(order.order_id)
        all_remaining_records.append({
            "order_id": id_str,
            "customer": order.customer,
            "boxes": order.boxes,
            "reason": "Could not fit within vehicle capacity or shift time limits"
        })

    write_remaining_orders_to_sheets(SHEET_NAME, all_remaining_records)

    # --- STEP 4: MAP VISUALIZATION ---
    if all_generated_routes:
        m = folium.Map(location=origin_coords, zoom_start=10, tiles="OpenStreetMap")
        
        folium.Marker(
            origin_coords, 
            popup="Origin / Depot", 
            icon=folium.Icon(color="red", icon="home", prefix="fa")
        ).add_to(m)

        for r_num, route, depart_min, return_min in all_generated_routes:
            color = route_colors[(r_num - 1) % len(route_colors)]
            optimized_orders = [item[0] for item in route]
            unique_ordered_coords = [(o.latitude, o.longitude) for o in optimized_orders]
            route_waypoints = [origin_coords] + unique_ordered_coords + [origin_coords]

            road_path_points = fetch_osrm_road_geometry(route_waypoints)

            for rank, (order, arrival_minute) in enumerate(route, start=1):
                eta_time = midnight_run_date + timedelta(minutes=arrival_minute)
                
                tw = getattr(order, "time_window", getattr(order, "timewindow", (0, 1440)))
                if tw == (0, 1440):
                    window_str = "Anytime"
                else:
                    win_start = (midnight_run_date + timedelta(minutes=tw[0])).strftime("%I:%M %p")
                    win_end = (midnight_run_date + timedelta(minutes=tw[1])).strftime("%I:%M %p")
                    window_str = f"{win_start} - {win_end}"

                id_str = ", ".join(map(str, order.order_id)) if isinstance(order.order_id, list) else str(order.order_id)
                
                folium.Marker(
                    location=(order.latitude, order.longitude),
                    popup=(
                        f"<b>Route #{r_num} - Stop #{rank}</b><br>"
                        f"Customer: {order.customer}<br>"
                        f"Order IDs: {id_str}<br>"
                        f"ETA: {eta_time.strftime('%I:%M %p')}<br>"
                        f"Total Boxes: {order.boxes}<br>"
                        f"Window: {window_str}"
                    ),
                    tooltip=f"R#{r_num} Stop #{rank}: {order.customer} ({order.boxes} boxes)",
                    icon=folium.Icon(color=color, icon="info-sign")
                ).add_to(m)

            folium.PolyLine(
                locations=road_path_points,
                color=color,
                weight=5,
                opacity=0.8,
                tooltip=f"Route #{r_num} Path"
            ).add_to(m)

        map_filename = "optimized_route.html"
        m.save(map_filename)
        webbrowser.open(map_filename)


if __name__ == "__main__":
    user_tab = input("Please put tab to use: ")
    run_optimization(user_tab)