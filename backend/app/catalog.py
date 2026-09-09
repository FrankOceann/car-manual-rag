from app.schemas import Vehicle


SUPPORTED_VEHICLES: tuple[Vehicle, ...] = (
    Vehicle(id="byd-seal", brand="比亚迪", model="海豹", year=2024),
    Vehicle(id="toyota-corolla", brand="丰田", model="卡罗拉", year=2024),
    Vehicle(id="honda-civic", brand="本田", model="思域", year=2024),
)


def list_vehicles() -> tuple[Vehicle, ...]:
    return SUPPORTED_VEHICLES


def get_vehicle(vehicle_id: str) -> Vehicle | None:
    return next((vehicle for vehicle in SUPPORTED_VEHICLES if vehicle.id == vehicle_id), None)
