from typing import Optional, Tuple, Union

class Order:
    """
    Class that stores orders gotten from sheet
    """
    def __init__(self, order_id: Union[int, list], customer: str, boxes: int, latitude: float, longitude: float, city: str = "", bin_location: str = ""):
        self.order_id = order_id  # ID given by spreadsheet
        self.customer = customer  # Name of customer given by spreadsheet
        self.boxes = boxes  # number of boxes, given by spreadsheet
        self.latitude = latitude 
        self.longitude = longitude  # latitude and longitude, gotten from system
        self.city = city  # City from Column C
        self.bin_location = bin_location  # Bin from Column G
        self.timewindow = (0, 1000000)  # time range that an order can be delivered to

    @staticmethod
    def create(
        order_id: Union[int, list],
        customer: str,
        boxes: int,
        latitude: float,
        longitude: float,
        timewindow: Optional[Tuple[float, float]] = None,
        city: str = "",
        bin_location: str = ""
    ) -> "Order":
        if timewindow is not None:
            return TimedOrder(order_id, customer, boxes, latitude, longitude, timewindow, city, bin_location)
        else:
            return Order(order_id, customer, boxes, latitude, longitude, city, bin_location)

    def __repr__(self):
        return f"Order({self.order_id}, {self.customer}, {self.boxes}, {self.latitude}, {self.longitude}, city='{self.city}', bin='{self.bin_location}')"


class TimedOrder(Order):
    """
    Special orders that require a timeframe to deliver
    """
    def __init__(self, order_id: Union[int, list], customer: str, boxes: int, latitude: float, longitude: float, timewindow: Tuple, city: str = "", bin_location: str = ""):
        super().__init__(order_id, customer, boxes, latitude, longitude, city, bin_location)
        self.timewindow = timewindow

    def __repr__(self):
        return f"TimedOrder({self.order_id}, {self.customer}, {self.boxes}, {self.latitude}, {self.longitude}, {self.timewindow}, city='{self.city}', bin='{self.bin_location}')"