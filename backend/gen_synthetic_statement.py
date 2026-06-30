#!/usr/bin/env python3
"""Genera un extracto bancario SINTÉTICO y realista por sector.

Default: cleaning, 2 meses. Salida (en backend/private/, gitignored):
  - synthetic_<sector>_gold.csv : formato del SET DE ORO (etiquetado) -> enchufa a eval_engine.py --gold
  - synthetic_<sector>_raw.csv  : formato banco crudo (date/desc/amount/type) -> probar la ingestión/app

Datos 100% inventados (nombres, comercios, montos). NO son reales.
Uso:  cd backend && .venv/bin/python gen_synthetic_statement.py
"""
import csv
import os
import random
import string
from datetime import date, timedelta

random.seed(42)

SECTOR = "cleaning"
START = date(2026, 4, 1)
MONTHS = 2
PUB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private")
os.makedirs(PUB, exist_ok=True)
GOLD = os.path.join(PUB, f"synthetic_{SECTOR}_gold.csv")
RAW = os.path.join(PUB, f"synthetic_{SECTOR}_raw.csv")

CITIES = ["CONCORD CA", "RICHMOND CA", "OAKLAND CA", "SAN JOSE CA", "HAYWARD CA",
          "FREMONT CA", "ANTIOCH CA", "PITTSBURG CA", "SAN LEANDRO CA"]
CLIENTS = ["MARIA GONZALEZ", "SUNRISE PROPERTY MGMT", "GOLDEN GATE REALTY", "ANDERSON HOME",
           "BAY AREA DENTAL", "PIZZA PALACE LLC", "OAK STREET OFFICES", "LINDA TRAN",
           "RIVERSIDE APARTMENTS", "DR PATEL CLINIC", "GREENFIELD SCHOOL", "JAMES O CONNOR"]
WORKERS = ["Lucia", "Marco", "Esperanza", "Tito", "Rosa", "Kevin"]


def code(n=12):
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(n))


def loc():
    return f"{random.choice(CITIES)} {random.randint(100000, 999999)}"


def mdd(d):
    return d.strftime("%m/%d")


import re


def payee_of(merchant):
    """Deriva un beneficiario limpio del nombre crudo del comercio (consistente con la descripción)."""
    base = re.split(r"[#*]", merchant)[0]
    base = re.sub(r"\b[A-Z]?\d{2,}\b", "", base)  # quita números de tienda/ref
    base = re.sub(r"\s+", " ", base).strip(" -")
    return base.title() if base else merchant


txns = []  # cada uno: dict con las columnas del set de oro


def add(d, desc, amt, btype, at, cat, payee, chk="", notes=""):
    txns.append({"date": d, "description": desc, "amount": round(amt, 2), "bank_type": btype,
                 "check_number": chk, "correct_account_type": at, "correct_category": cat,
                 "correct_payee": payee, "notes": notes})


def days_in(month_idx):
    m = START.month + month_idx
    y = START.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    nxt = date(y + (m // 12), (m % 12) + 1, 1)
    first = date(y, m, 1)
    return [first + timedelta(days=i) for i in range((nxt - first).days)]


# ---- Catálogos de comercios por categoría (sector cleaning) ----
SUPPLIES = ["THE HOME DEPOT #0634", "WALMART SUPERCENTER", "ULINE SHIP SUPPLIES",
            "COSTCO WHSE #0421", "GRAINGER", "SMART & FINAL", "AMAZON.COM*CLEANSUP"]
TOOLS = ["THE HOME DEPOT #0639", "AMAZON.COM*VACPARTS", "NACECARE SOLUTIONS"]
FUEL = ["CHEVRON 0376762", "SHELL OIL 574444", "ARCO#07144", "76 - SAN CARLOS", "VALERO #2231"]
MEALS = ["STARBUCKS STORE 06662", "MCDONALD'S F1234", "CHIPOTLE 1881", "SUBWAY 33012",
         "TAQUERIA LOS REYES", "PANERA BREAD #204"]
INSURANCE = ["NEXT INSURANCE", "HISCOX INS", "BTIS DIRECTP", "THE HARTFORD"]
MARKETING = ["FACEBK *ADS7K2", "YELP* INC ADS", "GOOGLE *ADS 8829", "THUMBTACK INC", "NEXTDOOR ADS"]
SOFTWARE = ["JOBBER SOFTWARE", "ZENMAID", "SQSP* INV", "GOOGLE *WORKSPACE", "INTUIT *QBOOKS"]
PHONE = ["T-MOBILE *PCS SVC", "VERIZON WIRELESS", "COMCAST CABLE COMM"]
UNIFORMS = ["CINTAS CORP", "BULKAPPAREL.COM"]


def desc_card(merchant, d):
    return f"{merchant} {loc()} {mdd(d - timedelta(days=1))}"


def ach(coname, descr, payroll_name="GREENLEAF CLEANING IN"):
    return (f"ORIG CO NAME:{coname} ORIG ID:{random.randint(1000000000,9999999999)} "
            f"DESC DATE:{random.randint(100000,999999)} CO ENTRY DESCR:{descr} SEC:CCD "
            f"TRACE#:{random.randint(10**14,10**15-1)} IND NAME:{payroll_name} TRN: {code(10)}TC")


check_no = 1041

for mi in range(MONTHS):
    days = days_in(mi)

    # --- INGRESOS: clientes, 2-4 por semana ---
    for wk in range(0, len(days), 7):
        for _ in range(random.randint(2, 4)):
            d = days[min(wk + random.randint(0, 6), len(days) - 1)]
            client = random.choice(CLIENTS)
            amt = random.choice([180, 220, 250, 300, 350, 420, 500, 650, 800, 1200])
            rail = random.choice(["zelle", "zelle", "check_dep", "ach", "ach"])
            if rail == "zelle":
                add(d, f"Zelle payment from {client} {code()}", amt, "QUICKPAY_CREDIT",
                    "REVENUE", "Cleaning Revenue", client)
            elif rail == "check_dep":
                add(d, f"REMOTE ONLINE DEPOSIT # {random.randint(1,9)}", amt, "CHECK_DEPOSIT",
                    "REVENUE", "Cleaning Revenue", "", chk=str(random.randint(1, 9)),
                    notes="deposito de cheque de cliente")
            else:
                add(d, ach("DEPOSIT SERVICE", "CLEANING"), amt, "ACH_CREDIT",
                    "REVENUE", "Cleaning Revenue", client)

    # --- NÓMINA quincenal (Direct Labor) ---
    for day in (5, 20):
        d = date(days[0].year, days[0].month, day)
        add(d, ach("ONLINE PAYROLL", "PAYROLL"), -random.choice([900, 1100, 1300, 1500]),
            "ACH_DEBIT", "COGS", "Direct Labor", "Payroll", notes="nomina de personal de limpieza")

    # --- Pagos a trabajadores/subcontratistas (Zelle / cheque) ---
    for _ in range(random.randint(2, 4)):
        d = random.choice(days)
        w = random.choice(WORKERS)
        amt = -random.choice([120, 160, 200, 240, 300, 380])
        if random.random() < 0.5:
            add(d, f"Zelle payment to {w} {random.randint(20000000000,29999999999)}", amt,
                "QUICKPAY_DEBIT", "COGS", "Subcontractors", w,
                notes="Zelle saliente a persona - subcontratista/labor")
        else:
            global_chk = check_no
            check_no += 1
            add(d, f"CHECK {global_chk}", amt, "CHECK_PAID", "COGS", "Subcontractors", "",
                chk=str(global_chk), notes="cheque sin beneficiario - subcontratista/labor")

    # --- Insumos de limpieza (varias por mes) ---
    for _ in range(random.randint(4, 6)):
        d = random.choice(days)
        m = random.choice(SUPPLIES)
        add(d, desc_card(m, d), -random.choice([18.4, 27.9, 43.5, 62.1, 88.0, 124.3]),
            "DEBIT_CARD", "COGS", "Cleaning Supplies", payee_of(m))

    # --- Herramientas/equipo chico ---
    if random.random() < 0.7:
        d = random.choice(days)
        m = random.choice(TOOLS)
        add(d, desc_card(m, d), -random.choice([45.0, 92.5, 160.8, 210.0]),
            "DEBIT_CARD", "COGS", "Small Tools & Equipment", payee_of(m))

    # --- Combustible (Vehicle Fuel, OpEx) ---
    for _ in range(random.randint(3, 5)):
        d = random.choice(days)
        m = random.choice(FUEL)
        add(d, desc_card(m, d), -random.choice([42.0, 55.3, 61.7, 68.9, 74.2]),
            "DEBIT_CARD", "OPERATING_EXPENSE", "Vehicle Fuel", payee_of(m))

    # --- Comidas ---
    for _ in range(random.randint(2, 4)):
        d = random.choice(days)
        m = random.choice(MEALS)
        add(d, desc_card(m, d), -random.choice([8.4, 14.7, 22.9, 36.8, 48.5]),
            "DEBIT_CARD", "OPERATING_EXPENSE", "Meals", payee_of(m))

    # --- Recurrentes mensuales ---
    d = date(days[0].year, days[0].month, random.randint(8, 14))
    add(d, f"PG E EZ PAY {loc()} {mdd(d)}", -random.choice([95.4, 121.4, 168.3]),
        "DEBIT_CARD", "OPERATING_EXPENSE", "Utilities", "PG&E")

    d = date(days[0].year, days[0].month, random.randint(15, 20))
    m = random.choice(PHONE)
    add(d, desc_card(m, d), -random.choice([85.0, 110.0, 140.0]),
        "DEBIT_CARD", "OPERATING_EXPENSE", "Telephone & Internet", payee_of(m))

    d = date(days[0].year, days[0].month, random.randint(3, 9))
    ins = random.choice(INSURANCE)
    add(d, ach(ins, "PREM-PYMT"), -random.choice([180.0, 245.0, 304.7]),
        "ACH_DEBIT", "OPERATING_EXPENSE", "Insurance", payee_of(ins))

    for sw in random.sample(SOFTWARE, k=random.randint(2, 3)):
        d = random.choice(days)
        add(d, desc_card(sw, d), -random.choice([8.4, 15.0, 29.0, 49.0]),
            "DEBIT_CARD", "OPERATING_EXPENSE", "Software",
            {"JOBBER SOFTWARE": "Jobber", "ZENMAID": "ZenMaid", "SQSP* INV": "Squarespace",
             "GOOGLE *WORKSPACE": "Google Workspace", "INTUIT *QBOOKS": "QuickBooks"}[sw])

    # --- Marketing (1-2 por mes) ---
    for _ in range(random.randint(1, 2)):
        d = random.choice(days)
        m = random.choice(MARKETING)
        add(d, desc_card(m, d), -random.choice([25.0, 60.0, 120.0, 0.71]),
            "DEBIT_CARD", "OPERATING_EXPENSE", "Marketing", payee_of(m))

    # --- Uniformes (ocasional) ---
    if random.random() < 0.5:
        d = random.choice(days)
        m = random.choice(UNIFORMS)
        add(d, desc_card(m, d), -random.choice([54.0, 88.0, 113.4]),
            "DEBIT_CARD", "OPERATING_EXPENSE", "Uniforms", payee_of(m))

    # --- NON_PL: retiro del dueño (Remitly), tarjeta, transferencia, préstamo ---
    for _ in range(random.randint(1, 3)):
        d = random.choice(days)
        add(d, f"RMTLY* {code(5)} REMITLY.COM WA {mdd(d)}", -random.choice([52.99, 102.99, 202.99]),
            "DEBIT_CARD", "NON_PL", "Owner Draw", "Remitly",
            notes="remesa - retiro/personal del dueno")

    d = date(days[0].year, days[0].month, 1)
    add(d, f"Payment to Chase card ending in 6694 {mdd(d)}", -random.choice([300.0, 500.0, 750.0]),
        "LOAN_PMT", "NON_PL", "Credit Card Payment", "Chase")

    if random.random() < 0.6:
        d = random.choice(days)
        add(d, f"Online Transfer to SAV ...8163 transaction#: {random.randint(20000000000,29999999999)} {mdd(d)}",
            -random.choice([300.0, 500.0, 1000.0]), "ACCT_XFER", "NON_PL", "Transfer", "",
            notes="transferencia entre cuentas propias")

    d = random.choice(days)
    add(d, ach("ALLY BANK LOAN", "LOAN PMT", "GREENLEAF CLEANING IN"), -random.choice([350.0, 480.0]),
        "ACH_DEBIT", "NON_PL", "Loan Principal Payment", "Ally Bank",
        notes="pago de prestamo - separar interes")

    # --- OTHER_EXPENSE: impuestos/licencias ---
    if mi == 0:
        d = date(days[0].year, days[0].month, 15)
        add(d, ach("FRANCHISE TAX BO", "PAYMENTS"), -800.0, "ACH_DEBIT",
            "OTHER_EXPENSE", "Taxes & Licenses", "CA Franchise Tax Board")
    d = random.choice(days)
    add(d, f"CA DMV FEE 678-731 {loc()} {mdd(d)}", -random.choice([5.97, 183.0]),
        "DEBIT_CARD", "OTHER_EXPENSE", "Taxes & Licenses", "CA DMV")

    # --- OTHER_INCOME: cashback/reembolso ---
    if random.random() < 0.7:
        d = random.choice(days)
        add(d, f"Offer: {random.choice(['Chevron','Costco'])}", round(random.uniform(1.5, 6.0), 2),
            "MISC_CREDIT", "OTHER_INCOME", "Refunds & Rebates", random.choice(["Chevron", "Costco"]))

    # --- Comisión bancaria ---
    d = date(days[0].year, days[0].month, days[-1].day)
    add(d, "MONTHLY SERVICE FEE", -15.0, "FEE_TRANSACTION", "OPERATING_EXPENSE", "Bank Fees", "Chase")

# Ordenar por fecha
txns.sort(key=lambda t: t["date"])

# --- Escribir set de oro (etiquetado) ---
cols = ["date", "description", "amount", "bank_type", "check_number",
        "correct_account_type", "correct_category", "correct_payee", "notes"]
with open(GOLD, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for t in txns:
        row = dict(t)
        row["date"] = t["date"].strftime("%m/%d/%Y")
        w.writerow(row)

# --- Escribir crudo (formato banco para probar ingestión) ---
with open(RAW, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Date", "Description", "Amount", "Type", "Check Number"])
    for t in txns:
        w.writerow([t["date"].strftime("%m/%d/%Y"), t["description"], f'{t["amount"]:.2f}',
                    t["bank_type"], t["check_number"]])

# Resumen
from collections import Counter
by_at = Counter(t["correct_account_type"] for t in txns)
print(f"OK: {len(txns)} transacciones · sector={SECTOR} · {MONTHS} meses")
print("  por seccion:", dict(by_at))
print(f"  -> {GOLD}")
print(f"  -> {RAW}")
