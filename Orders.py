from typing import Optional
class Order:
    """
    Class that stores orders gotten from sheet
    """
    def __init__(self, order_id: int, customer: str, boxes: int, latitude: float, longitude: float):
        self.order_id = order_id # ID given by spreadsheet
        self.customer = customer # Name of customer given by spreadsheet
        self.boxes = boxes #number of boxes, given by spreadsheet
        self.latitude = latitude 
        self.longitude = longitude #latitude and longitude, gotten from system
        self.timewindow = (0,1000000) #time range that an order can be delivered to
    def create(
            order_id: int,
            customer: str,
            boxes: int,
            latitude: float,
            longitude: float,
            timewindow: tuple[float, float] = None
    ) ->"Order":
        if timewindow is not None:
            return TimedOrder(order_id, customer, boxes, latitude, longitude, timewindow)
        else:
            return Order(order_id, customer, boxes, latitude, longitude)

    def __repr__(self):
        return f"Order({self.order_id}, {self.customer}, {self.boxes}, {self.latitude}, {self.longitude})"
class TimedOrder(Order):
    """
    Special orders that require a timeframe to deliver
    """
    def __init__(self, order_id: int, customer: str, boxes: int, latitude: float, longitude: float, timewindow: tuple):
        super().__init__(order_id, customer, boxes, latitude, longitude)
        self.timewindow = timewindow
    def __repr__(self):
        return f"TimedOrder({self.order_id}, {self.customer}, {self.boxes}, {self.latitude}, {self.longitude}, {self.timewindow})"