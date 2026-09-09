from app.catalog import get_vehicle, list_vehicles


def test_catalog_contains_exactly_three_supported_models():
    assert [(item.brand, item.model) for item in list_vehicles()] == [
        ("比亚迪", "海豹"),
        ("丰田", "卡罗拉"),
        ("本田", "思域"),
    ]
    assert get_vehicle("toyota-corolla") is not None
    assert get_vehicle("unknown") is None
