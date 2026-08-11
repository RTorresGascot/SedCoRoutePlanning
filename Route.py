from Orders import Order
class Route:
    
    def __init__(self, orders: list[Order]):
        self.orders = orders
        self.boxCount = 0
        for order in orders:
            self.boxCount += order.boxes
    def __repr__(self):
        return f"Route({self.orders}, {self.boxCount})"