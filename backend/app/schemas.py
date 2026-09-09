from pydantic import BaseModel


class Vehicle(BaseModel):
    id: str
    brand: str
    model: str
    year: int
