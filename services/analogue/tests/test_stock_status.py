from medstock_shared.stock import LOW_MAX, NORMAL_MAX, stock_fields, stock_status


def test_stock_status_thresholds():
    assert stock_status(0) == "none"
    assert stock_status(1) == "low"
    assert stock_status(LOW_MAX) == "low"
    assert stock_status(LOW_MAX + 1) == "normal"
    assert stock_status(NORMAL_MAX) == "normal"
    assert stock_status(NORMAL_MAX + 1) == "high"


def test_stock_fields_keep_quantity_and_in_stock():
    assert stock_fields(0) == {"quantity": 0, "in_stock": False, "stock_status": "none"}
    assert stock_fields(20) == {"quantity": 20, "in_stock": True, "stock_status": "low"}
    assert stock_fields(21) == {"quantity": 21, "in_stock": True, "stock_status": "normal"}
    assert stock_fields(100) == {"quantity": 100, "in_stock": True, "stock_status": "normal"}
    assert stock_fields(101) == {"quantity": 101, "in_stock": True, "stock_status": "high"}
