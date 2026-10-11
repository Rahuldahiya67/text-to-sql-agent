"""Create the demo analytics database: a fictional industrial-goods distributor.

Tables: regions, plants, customers, products, transporters, orders, order_items, shipments.
The data is synthetic and deterministic (fixed random seed), so tests and eval results are stable.
Works on SQLite and PostgreSQL because it only uses SQLAlchemy Core.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from sqlalchemy import (
    Column, Date, Float, ForeignKey, Integer, MetaData, Numeric, String, Table, create_engine, insert,
)

metadata = MetaData()

regions = Table(
    "regions", metadata,
    Column("region_id", Integer, primary_key=True),
    Column("name", String(20), nullable=False),
)
plants = Table(
    "plants", metadata,
    Column("plant_id", Integer, primary_key=True),
    Column("name", String(40), nullable=False),
    Column("city", String(40), nullable=False),
    Column("region_id", Integer, ForeignKey("regions.region_id"), nullable=False),
)
customers = Table(
    "customers", metadata,
    Column("customer_id", Integer, primary_key=True),
    Column("name", String(60), nullable=False),
    Column("city", String(40), nullable=False),
    Column("region_id", Integer, ForeignKey("regions.region_id"), nullable=False),
    Column("segment", String(20), nullable=False),
)
products = Table(
    "products", metadata,
    Column("product_id", Integer, primary_key=True),
    Column("name", String(60), nullable=False),
    Column("category", String(30), nullable=False),
    Column("unit_price", Numeric(12, 2), nullable=False),
)
transporters = Table(
    "transporters", metadata,
    Column("transporter_id", Integer, primary_key=True),
    Column("name", String(60), nullable=False),
)
orders = Table(
    "orders", metadata,
    Column("order_id", Integer, primary_key=True),
    Column("order_date", Date, nullable=False),
    Column("customer_id", Integer, ForeignKey("customers.customer_id"), nullable=False),
    Column("plant_id", Integer, ForeignKey("plants.plant_id"), nullable=False),
    Column("status", String(20), nullable=False),
)
order_items = Table(
    "order_items", metadata,
    Column("order_item_id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("orders.order_id"), nullable=False),
    Column("product_id", Integer, ForeignKey("products.product_id"), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("unit_price", Numeric(12, 2), nullable=False),
)
shipments = Table(
    "shipments", metadata,
    Column("shipment_id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("orders.order_id"), nullable=False, unique=True),
    Column("transporter_id", Integer, ForeignKey("transporters.transporter_id"), nullable=False),
    Column("dispatch_date", Date, nullable=False),
    Column("promised_date", Date, nullable=False),
    Column("delivery_date", Date),
    Column("freight_cost", Float, nullable=False),
)

REGIONS = ["North", "South", "East", "West"]
PLANTS = [("Gurugram Plant", "Gurugram", 1), ("Pune Plant", "Pune", 4), ("Chennai Plant", "Chennai", 2)]
CITIES = {
    1: ["Delhi", "Jaipur", "Lucknow", "Chandigarh", "Ludhiana"],
    2: ["Chennai", "Bengaluru", "Hyderabad", "Kochi", "Coimbatore"],
    3: ["Kolkata", "Bhubaneswar", "Patna", "Guwahati", "Ranchi"],
    4: ["Mumbai", "Pune", "Ahmedabad", "Indore", "Surat"],
}
SEGMENTS = ["Distributor", "OEM", "Retail", "Government"]
CATALOG = {
    "Motors": [("Induction Motor 5HP", 18500), ("Induction Motor 10HP", 32000), ("Servo Motor 2kW", 41000)],
    "Pumps": [("Centrifugal Pump 3HP", 14200), ("Submersible Pump 5HP", 22800), ("Booster Pump 1HP", 6900)],
    "Switchgear": [("MCB 32A Pack", 2400), ("ACB 1600A", 165000), ("Contactor 40A", 3800)],
    "Cables": [("Copper Cable 4sqmm (100m)", 9800), ("Armoured Cable 16sqmm (100m)", 38500)],
    "Transformers": [("Distribution Transformer 100kVA", 285000), ("Control Transformer 500VA", 5400)],
}
TRANSPORTERS = ["Shree Ram Logistics", "Bharat Roadways", "Speedline Carriers", "Om Sai Transport", "Delta Freight"]
CUSTOMER_WORDS = (["Apex", "Sunrise", "Vertex", "Pioneer", "Global", "Prime", "Metro", "Unity", "Zenith", "Royal",
                   "Galaxy", "Crown", "Allied", "Supreme", "Eastern"],
                  ["Traders", "Engineering", "Industries", "Electricals", "Infra", "Enterprises"])

START, END = date(2025, 1, 1), date(2026, 6, 30)


def build_database(url: str, n_orders: int = 4000, seed: int = 42) -> dict[str, int]:
    rng = random.Random(seed)
    engine = create_engine(url)
    metadata.drop_all(engine)
    metadata.create_all(engine)

    region_rows = [{"region_id": i, "name": n} for i, n in enumerate(REGIONS, start=1)]
    plant_rows = [{"plant_id": i, "name": n, "city": c, "region_id": r} for i, (n, c, r) in enumerate(PLANTS, start=1)]

    customer_rows, used = [], set()
    while len(customer_rows) < 60:
        name = f"{rng.choice(CUSTOMER_WORDS[0])} {rng.choice(CUSTOMER_WORDS[1])}"
        if name in used:
            continue
        used.add(name)
        region = rng.choices([1, 2, 3, 4], weights=[35, 25, 15, 25])[0]
        customer_rows.append({
            "customer_id": len(customer_rows) + 1, "name": name, "city": rng.choice(CITIES[region]),
            "region_id": region, "segment": rng.choices(SEGMENTS, weights=[40, 30, 20, 10])[0],
        })

    product_rows = []
    for category, items in CATALOG.items():
        for name, price in items:
            product_rows.append({"product_id": len(product_rows) + 1, "name": name, "category": category, "unit_price": price})

    transporter_rows = [{"transporter_id": i, "name": n} for i, n in enumerate(TRANSPORTERS, start=1)]
    # transporter reliability: chance of late delivery
    late_prob = {1: 0.08, 2: 0.18, 3: 0.12, 4: 0.30, 5: 0.15}

    order_rows, item_rows, ship_rows = [], [], []
    span = (END - START).days
    for oid in range(1, n_orders + 1):
        # mild growth + seasonality: more orders later in the period and before the monsoon
        while True:
            d = START + timedelta(days=rng.randint(0, span))
            if rng.random() < 0.6 + 0.4 * ((d - START).days / span) * (1.15 if d.month in (2, 3, 4) else 1.0):
                break
        cust = rng.choice(customer_rows)
        # orders are served from the plant in (or nearest to) the customer's region
        plant = {1: 1, 2: 3, 3: 1, 4: 2}[cust["region_id"]] if rng.random() < 0.8 else rng.randint(1, 3)
        status = "delivered"
        if d > END - timedelta(days=12):
            status = "in_transit"
        if rng.random() < 0.04:
            status = "cancelled"
        order_rows.append({"order_id": oid, "order_date": d, "customer_id": cust["customer_id"], "plant_id": plant, "status": status})

        for _ in range(rng.choices([1, 2, 3, 4], weights=[45, 30, 15, 10])[0]):
            prod = rng.choice(product_rows)
            qty = rng.randint(1, 3) if prod["unit_price"] > 100000 else rng.randint(1, 40)
            price = round(prod["unit_price"] * rng.uniform(0.92, 1.0), 2)   # negotiated discounts
            item_rows.append({"order_item_id": len(item_rows) + 1, "order_id": oid, "product_id": prod["product_id"],
                              "quantity": qty, "unit_price": price})

        if status != "cancelled":
            trans = rng.randint(1, 5)
            dispatch = d + timedelta(days=rng.randint(0, 3))
            transit = rng.randint(2, 6)
            promised = dispatch + timedelta(days=transit)
            delivery = None
            if status == "delivered":
                delay = rng.randint(1, 5) if rng.random() < late_prob[trans] else -rng.randint(0, 1)
                delivery = promised + timedelta(days=delay)
            ship_rows.append({"shipment_id": len(ship_rows) + 1, "order_id": oid, "transporter_id": trans,
                              "dispatch_date": dispatch, "promised_date": promised, "delivery_date": delivery,
                              "freight_cost": round(rng.uniform(2500, 18000) * (1.2 if trans == 1 else 1.0), 2)})

    with engine.begin() as conn:
        for table, rows in [(regions, region_rows), (plants, plant_rows), (customers, customer_rows),
                            (products, product_rows), (transporters, transporter_rows), (orders, order_rows),
                            (order_items, item_rows), (shipments, ship_rows)]:
            for start in range(0, len(rows), 2000):
                conn.execute(insert(table), rows[start:start + 2000])
    engine.dispose()
    return {"orders": len(order_rows), "order_items": len(item_rows), "shipments": len(ship_rows),
            "customers": len(customer_rows), "products": len(product_rows)}
