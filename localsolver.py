import json
from collections import defaultdict
from urllib import request
from ortools.constraint_solver import routing_enums_pb2, pywrapcp


def get_duration_matrix_osrm(locations):
    coord_str = ";".join([f"{lng},{lat}" for lat, lng in locations])
    url = f"http://router.project-osrm.org/table/v1/driving/{coord_str}?annotations=duration"

    req = request.Request(url, headers={"User-Agent": "RouteOptimizer/1.0"})
    n = len(locations)
    matrix = [[0] * n for _ in range(n)]

    try:
        with request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            durations = data.get("durations", [])
            for i in range(n):
                for j in range(n):
                    matrix[i][j] = int(durations[i][j] // 60)
    except Exception as e:
        print(f"OSRM Request failed: {e}. Falling back to straight-line estimation.")
        for i in range(n):
            for j in range(n):
                lat1, lon1 = locations[i]
                lat2, lon2 = locations[j]
                dist = ((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) ** 0.5
                matrix[i][j] = int(dist * 100)

    return matrix


def solve_multi_vehicle_vrptw(origin, orders, box_max, num_vehicles, window_start_min=360, window_end_min=1020):
    """
    Solves VRPTW where routes can start anytime between window_start_min (default 6:00 AM / 360m)
    and must finish back at depot by window_end_min (default 5:00 PM / 1020m).
    """
    locations = [origin] + [(o.latitude, o.longitude) for o in orders]
    time_matrix = get_duration_matrix_osrm(locations)

    manager = pywrapcp.RoutingIndexManager(len(locations), num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # 1. Travel Time & Geographic Cost Callback
    def time_callback(from_idx, to_idx):
        from_node = manager.IndexToNode(from_idx)
        to_node = manager.IndexToNode(to_idx)
        service_time = 10 if from_node != 0 else 0
        return time_matrix[from_node][to_node] + service_time

    def clustering_cost_callback(from_idx, to_idx):
        from_node = manager.IndexToNode(from_idx)
        to_node = manager.IndexToNode(to_idx)
        travel_mins = time_matrix[from_node][to_node]
        return int(travel_mins ** 1.3) if from_node != 0 else travel_mins

    transit_time_cb_idx = routing.RegisterTransitCallback(time_callback)
    clustering_cost_cb_idx = routing.RegisterTransitCallback(clustering_cost_callback)

    routing.SetArcCostEvaluatorOfAllVehicles(clustering_cost_cb_idx)

    # 2. Fixed Cost per Vehicle
    FIXED_VEHICLE_COST = 500000
    routing.SetFixedCostOfAllVehicles(FIXED_VEHICLE_COST)

    # 3. Time Window Dimension (Bounded between window_start_min and window_end_min)
    time_dim_name = "Time"
    routing.AddDimension(
        transit_time_cb_idx,
        180,              # Max wait time allowed (mins)
        window_end_min,   # Absolute hard ceiling (e.g., 5:00 PM / 1020 mins)
        False,
        time_dim_name,
    )
    time_dimension = routing.GetDimensionOrDie(time_dim_name)

    # Enforce Shift Range per vehicle
    for vehicle_id in range(num_vehicles):
        start_index = routing.Start(vehicle_id)
        end_index = routing.End(vehicle_id)
        
        # Vehicle can depart anytime within the allowed shift window
        time_dimension.CumulVar(start_index).SetRange(window_start_min, window_end_min)
        # Vehicle MUST return to depot by window_end_min
        time_dimension.CumulVar(end_index).SetRange(window_start_min, window_end_min)

    # 4. Box Capacity Dimension
    def demand_callback(from_idx):
        from_node = manager.IndexToNode(from_idx)
        return 0 if from_node == 0 else orders[from_node - 1].boxes

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx, 0, [box_max] * num_vehicles, True, "Capacity"
    )

    # 5. Order Time Windows & Disjunctions
    BASE_PENALTY = 1000000
    for idx, order in enumerate(orders, start=1):
        node_idx = manager.NodeToIndex(idx)
        start, end = getattr(order, "time_window", getattr(order, "timewindow", (0, 1440)))

        # Constrain order window within shift limits
        safe_start = int(max(start, window_start_min))
        safe_end = int(min(end, window_end_min))

        if safe_start >= safe_end:
            safe_start = window_start_min
            safe_end = window_end_min

        try:
            time_dimension.CumulVar(node_idx).SetRange(safe_start, safe_end)
        except Exception:
            time_dimension.CumulVar(node_idx).SetRange(window_start_min, window_end_min)

        routing.AddDisjunction([node_idx], BASE_PENALTY * max(1, order.boxes))

    # 6. Link Duplicate Locations
    location_groups = defaultdict(list)
    for idx, order in enumerate(orders, start=1):
        key = (order.latitude, order.longitude)
        location_groups[key].append(manager.NodeToIndex(idx))

    for loc, node_indices in location_groups.items():
        if len(node_indices) > 1:
            for i in range(len(node_indices) - 1):
                routing.AddPickupAndDelivery(node_indices[i], node_indices[i + 1])
                routing.Solver().Add(
                    routing.VehicleVar(node_indices[i]) == routing.VehicleVar(node_indices[i + 1])
                )

    # 7. Solver Parameters
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = 5

    solution = routing.SolveWithParameters(search_params)
    if not solution:
        return []

    # Extract routes, exact departure times, and return times
    all_routes = []
    for vehicle_id in range(num_vehicles):
        index = routing.Start(vehicle_id)
        start_minute = solution.Min(time_dimension.CumulVar(index))
        route_stops = []
        
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != 0:
                order = orders[node - 1]
                time_var = time_dimension.CumulVar(index)
                arrival_minute = solution.Min(time_var)
                route_stops.append((order, arrival_minute))
            index = solution.Value(routing.NextVar(index))
            
        if route_stops:
            end_index = routing.End(vehicle_id)
            return_minute = solution.Min(time_dimension.CumulVar(end_index))
            all_routes.append((route_stops, start_minute, return_minute))

    return all_routes