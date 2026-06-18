"""
ContaBu — listas de comercios para desambiguar por monto (idea #14).

Un mismo tipo de comercio mapea a categorías distintas según el MONTO, y eso se resuelve
de forma **determinista** (antes de la IA) con dos umbrales editables por empresa:
- **Supermercado:** compra pequeña → comida (meal); grande → gasto personal (retiro del dueño).
- **Gasolinera:** poco → combustible de maquinaria (Equipment Fuel, COGS); mucho → gasolina del
  vehículo (Vehicle Fuel, OpEx).

Estas listas son un punto de partida; se curan/expanden con el tiempo (y, a futuro, por empresa).
El match es por **subcadena normalizada** (ver `rules.normalize`): "SHELL OIL 4471" → contiene "SHELL".
NOTA: se evalúa GASOLINERA antes que SUPERMERCADO (ej. "COSTCO GAS" es gasolinera, "COSTCO" es super).
"""

# Gasolineras (marcas comunes en EE. UU.). Se evalúan PRIMERO.
GAS_STATIONS: list[str] = [
    "Costco Gas", "Sams Club Gas", "Shell", "Chevron", "Exxon", "Mobil", "BP", "Texaco",
    "Arco", "Valero", "Conoco", "Phillips 66", "Sunoco", "Marathon", "Speedway", "Circle K",
    "Citgo", "Gulf", "Sinclair", "QuikTrip", "Wawa", "Sheetz", "Murphy USA", "Pilot", "Loves",
    "76 Gas", "Kwik Trip", "RaceTrac", "Cumberland Farms",
]

# Supermercados / tiendas de comestibles.
SUPERMARKETS: list[str] = [
    "Walmart", "Costco", "Sams Club", "Target", "Kroger", "Safeway", "Albertsons", "Publix",
    "Aldi", "Trader Joe", "Whole Foods", "Food 4 Less", "Vons", "Ralphs", "H-E-B", "HEB",
    "Wegmans", "Sprouts", "Food Lion", "Giant", "Stop & Shop", "Meijer", "WinCo",
    "Smart & Final", "Grocery Outlet", "Save A Lot", "Fred Meyer", "Hy-Vee", "Lidl",
]
