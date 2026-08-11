from collections import defaultdict
from itertools import combinations
from Orders import Order
from OptimizeRoute import optimize_route
from Route import Route
import webbrowser
import folium
import polyline

boxmax = 50
origin_coords = (18.411141480447743, -66.194513270137)

order1 = Order(1, "Customer A", 5, 18.117966580930734, -66.37987321534263)   
order2 = Order(2, "Customer B", 10, 18.397450479350653, -66.29071216626848)  
order3 = Order(3, "Customer C", 10, 18.397450479350653, -66.06217838193939)  
order4 = Order(4, "Customer D", 10, 18.20096817703958, -66.66457728078069)
order5 = Order(5, "Customer E", 2, 18.26384897178699, -66.97394302476046)
order6 = Order(6, "Customer F", 10, 18.087184971804557, -67.0282592816553)
order7 = Order(7, "Customer F", 8, 18.087184971804557, -67.0282592816553)

orderList = [order1, order2, order3, order4, order5, order6, order7]

def find_best_route(orders: list[Order], boxmax: int) -> list[Order]:
    # 1. Group orders by customer/location so they cannot be split
    customer_groups = defaultdict(list)
    for order in orders:
        # Group by customer identifier (or use (order.latitude, order.longitude) for location grouping)
        customer_groups[order.customer].append(order)
    
    # Each item in group_list is the FULL list of orders for a customer
    group_list = list(customer_groups.values())

    best_subset = []
    best_total_boxes = -1

    # 2. Find combinations of CUSTOMER GROUPS instead of individual orders
    for r in range(1, len(group_list) + 1):
        for group_combination in combinations(group_list, r):
            # Flatten chosen customer groups into a single list of orders
            combined_orders = [order for group in group_combination for order in group]
            total_boxes = sum(order.boxes for order in combined_orders)

            # Maximize box count strictly within vehicle capacity limit
            if total_boxes <= boxmax and total_boxes > best_total_boxes:
                best_total_boxes = total_boxes
                best_subset = combined_orders

    return best_subset

optimal_orders = find_best_route(orderList, boxmax)
remaining_orders = [order for order in orderList if order not in optimal_orders]
route = Route(optimal_orders)
route.boxCount = sum(order.boxes for order in route.orders)

print(f"Selected orders: {[f'{order.customer} (ID: {order.order_id}, {order.boxes} boxes)' for order in route.orders]}")
print(f"Total boxes in selected route: {route.boxCount}/{boxmax}")

# Run optimization
route_data, ordered_coords = optimize_route(
    origin=origin_coords,
    stops=[(order.latitude, order.longitude) for order in route.orders]
)

if ordered_coords and route_data:
    # 1. Safely Map Coordinates back to Orders (handles multiple orders at same lat/lng)
    coord_to_orders = defaultdict(list)
    for order in route.orders:
        coord_to_orders[(order.latitude, order.longitude)].append(order)

    # Filter adjacent duplicate coordinates to construct unique stop sequence
    unique_ordered_coords = []
    for coord in ordered_coords:
        if not unique_ordered_coords or unique_ordered_coords[-1] != coord:
            unique_ordered_coords.append(coord)

    print("\nOptimized Order Delivery Sequence:")
    rank = 1
    for coord in unique_ordered_coords:
        for order in coord_to_orders[coord]:
            print(f"  {rank}. {order.customer} (ID: {order.order_id}), {order.boxes} boxes")
            rank += 1

    print("\nRemaining orders that could not be included in the route due to box limit:")
    for order in remaining_orders:
        print(f"  {order.customer} (ID: {order.order_id}, {order.boxes} boxes)")

    # 2. Extract and Decode the Polyline Geometry directly from route_data
    try:
        encoded_path = route_data["routes"][0]["polyline"]["encodedPolyline"]
        road_path_points = polyline.decode(encoded_path)
    except (KeyError, IndexError):
        # Fallback to straight lines if polyline field is missing
        road_path_points = [origin_coords] + unique_ordered_coords + [origin_coords]

    # 3. Create Folium Map
    m = folium.Map(location=origin_coords, zoom_start=10, tiles="OpenStreetMap")

    # Add Origin / Depot Marker
    folium.Marker(
        location=origin_coords,
        popup="<b>Origin / Depot</b>",
        tooltip="Depot Start/End",
        icon=folium.Icon(color="red", icon="home", prefix="fa")
    ).add_to(m)

    # Add Stop Markers
    for rank, coord in enumerate(unique_ordered_coords, start=1):
        orders_at_stop = coord_to_orders[coord]
        popup_text = f"<b>Stop #{rank}</b><br>" + "<br>".join(
            [f"Customer: {o.customer} (ID: {o.order_id}) - {o.boxes} boxes" for o in orders_at_stop]
        )
        cust_labels = ", ".join(set(o.customer for o in orders_at_stop))

        folium.Marker(
            location=coord,
            popup=popup_text,
            tooltip=f"Stop #{rank}: {cust_labels}",
            icon=folium.Icon(color="blue", icon="info-sign")
        ).add_to(m)

    # Draw Driving Polyline
    folium.PolyLine(
        locations=road_path_points,
        color="blue",
        weight=5,
        opacity=0.8,
        tooltip="Driving Route"
    ).add_to(m)

    # Save and Open Map
    map_filename = "optimized_route.html"
    m.save(map_filename)
    print(f"\nMap saved successfully as '{map_filename}'. Launching browser...")
    webbrowser.open(map_filename)