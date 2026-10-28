#!/usr/bin/env python3
"""
Local standalone data generator for aeroLatinos fictitious airline dataset.
Generates same schemas/narratives as Databricks notebook, but writes to local landing folder.

Outputs:
  - aeropuertos → CSV
  - encuestas_nps → JSONL
  - aeronaves, puertas, pasajeros, vuelos, disrupciones, reservas → Parquet

Drops: scans_equipaje (not needed for local demo)
"""

import polars as pl
from mimesis import Person
from mimesis.locales import Locale

import random
import json
import os
import uuid
from datetime import datetime, timedelta, date

# Fixed seed for reproducibility (can change to random.seed(None) for randomness)
random.seed(42)

# Landing folder base path
LANDING_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "landing")

# Ensure landing folders exist
for tabla in ["aeropuertos", "aeronaves", "puertas", "pasajeros", "vuelos", "disrupciones", "reservas", "encuestas_nps"]:
    os.makedirs(f"{LANDING_BASE}/{tabla}", exist_ok=True)

print("=" * 80)
print("aeroLatinos — Data Generator (Local Standalone)")
print("=" * 80)

# ============================================================================
# Constants
# ============================================================================

HUBS = ["PTY", "LIM", "SCL"]

AEROPUERTOS = [
    ("PTY", "Tocumen Internacional",       "Ciudad de Panamá",     "PA", "America/Panama"),
    ("LIM", "Jorge Chávez",                 "Lima",                 "PE", "America/Lima"),
    ("SCL", "Arturo Merino Benítez",        "Santiago",             "CL", "America/Santiago"),
    ("MEX", "Benito Juárez",                "Ciudad de México",     "MX", "America/Mexico_City"),
    ("GRU", "Guarulhos",                    "São Paulo",            "BR", "America/Sao_Paulo"),
    ("EZE", "Ministro Pistarini",           "Buenos Aires",         "AR", "America/Argentina/Buenos_Aires"),
    ("BOG", "El Dorado",                    "Bogotá",               "CO", "America/Bogota"),
    ("UIO", "Mariscal Sucre",               "Quito",                "EC", "America/Guayaquil"),
    ("GYE", "José Joaquín de Olmedo",       "Guayaquil",            "EC", "America/Guayaquil"),
    ("SJO", "Juan Santamaría",              "San José",             "CR", "America/Costa_Rica"),
    ("GUA", "La Aurora",                    "Ciudad de Guatemala",  "GT", "America/Guatemala"),
    ("SAL", "Óscar Arnulfo Romero",         "San Salvador",         "SV", "America/El_Salvador"),
    ("TGU", "Toncontín",                    "Tegucigalpa",          "HN", "America/Tegucigalpa"),
    ("MGA", "Augusto C. Sandino",           "Managua",              "NI", "America/Managua"),
    ("HAV", "José Martí",                   "La Habana",            "CU", "America/Havana"),
    ("SDQ", "Las Américas",                 "Santo Domingo",        "DO", "America/Santo_Domingo"),
    ("SJU", "Luis Muñoz Marín",             "San Juan",             "PR", "America/Puerto_Rico"),
    ("MIA", "Miami Internacional",          "Miami",                "US", "America/New_York"),
    ("JFK", "John F. Kennedy",              "Nueva York",           "US", "America/New_York"),
    ("LAX", "Los Ángeles Intl",             "Los Ángeles",          "US", "America/Los_Angeles"),
    ("YYZ", "Pearson",                      "Toronto",              "CA", "America/Toronto"),
    ("MAD", "Barajas",                      "Madrid",               "ES", "Europe/Madrid"),
    ("CCS", "Simón Bolívar",                "Caracas",              "VE", "America/Caracas"),
    ("MDE", "José María Córdova",           "Medellín",             "CO", "America/Bogota"),
    ("CTG", "Rafael Núñez",                 "Cartagena",            "CO", "America/Bogota"),
    ("POS", "Piarco Intl",                  "Puerto España",        "TT", "America/Port_of_Spain"),
    ("BZE", "Philip S.W. Goldson",          "Ciudad de Belice",     "BZ", "America/Belize"),
    ("ASU", "Silvio Pettirossi",            "Asunción",             "PY", "America/Asuncion"),
    ("MVD", "Carrasco",                     "Montevideo",           "UY", "America/Montevideo"),
    ("LPB", "El Alto",                      "La Paz",               "BO", "America/La_Paz"),
    ("VVI", "Viru Viru",                    "Santa Cruz",           "BO", "America/La_Paz"),
    ("CUN", "Cancún",                       "Cancún",               "MX", "America/Cancun"),
    ("AUA", "Reina Beatrix",                "Oranjestad",           "AW", "America/Aruba"),
]

MODELOS_AERONAVE = [
    ("B737-800",  160),
    ("B737-MAX9", 180),
    ("B787-8",    250),
    ("A320-200",  170),
    ("A321neo",   200),
]

TIERS_LEALTAD = ["Ninguno", "Plata", "Oro", "Platino", "Diamante"]
PESOS_TIER    = [0.60, 0.20, 0.12, 0.06, 0.02]

CLASES_TARIFA = ["Y", "J", "F"]
PESOS_CLASE   = [0.85, 0.12, 0.03]

CANALES_VENTA = ["Web", "App", "Agencia", "CallCenter"]
PESOS_CANAL   = [0.45, 0.30, 0.15, 0.10]

ESTADOS_CHECKIN = ["NoRegistrado", "Registrado", "Abordado", "NoShow"]
PESOS_CHECKIN   = [0.05, 0.15, 0.78, 0.02]

TIPOS_DISRUPCION = ["Clima", "Tecnico", "ControlAereo", "Huelga", "Seguridad"]

N_PASAJEROS            = 50_000
N_VUELOS_POR_DIA       = 50
N_DIAS                 = 90
N_AERONAVES            = 45
N_PUERTAS_POR_HUB      = 20
N_DISRUPCIONES_MENORES = 150

FECHA_FIN    = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
FECHA_INICIO = FECHA_FIN - timedelta(days=N_DIAS)

print(f"📅 Date range: {FECHA_INICIO.date()} → {FECHA_FIN.date()}")
print(f"🌱 Seed: 42 (fixed for reproducibility)")

# ============================================================================
# 1. Aeropuertos (CSV)
# ============================================================================

print("\n[1/8] Generating aeropuertos...")

df_aeropuertos = pl.DataFrame({
    "codigo_iata":  [a[0] for a in AEROPUERTOS],
    "nombre":       [a[1] for a in AEROPUERTOS],
    "ciudad":       [a[2] for a in AEROPUERTOS],
    "pais":         [a[3] for a in AEROPUERTOS],
    "zona_horaria": [a[4] for a in AEROPUERTOS],
    "es_hub":       [a[0] in HUBS for a in AEROPUERTOS],
})

ruta_aeropuertos = f"{LANDING_BASE}/aeropuertos/aeropuertos.csv"
df_aeropuertos.write_csv(ruta_aeropuertos)
print(f"  ✅ aeropuertos.csv: {len(df_aeropuertos)} rows")

# ============================================================================
# 2. Aeronaves (Parquet)
# ============================================================================

print("\n[2/8] Generating aeronaves...")

aeronaves = []
for i in range(1, N_AERONAVES + 1):
    modelo, capacidad = random.choice(MODELOS_AERONAVE)
    aeronaves.append({
        "id_aeronave":      f"AL-{i:03d}",
        "matricula":        f"HP-{random.randint(1000, 9999)}AL",
        "modelo":           modelo,
        "capacidad_total":  capacidad,
        "ano_fabricacion":  random.randint(2005, 2024),
        "estado":           random.choices(
                                ["Activa", "Mantenimiento", "Retirada"],
                                weights=[0.85, 0.10, 0.05]
                            )[0],
    })

df_aeronaves = pl.DataFrame(aeronaves)
ruta_aeronaves = f"{LANDING_BASE}/aeronaves/aeronaves.parquet"
df_aeronaves.write_parquet(ruta_aeronaves)
print(f"  ✅ aeronaves.parquet: {len(df_aeronaves)} rows")

# ============================================================================
# 3. Puertas (Parquet)
# ============================================================================

print("\n[3/8] Generating puertas...")

puertas = []
for hub in HUBS:
    for i in range(1, N_PUERTAS_POR_HUB + 1):
        letra    = "A" if i <= 10 else "B"
        numero   = i if i <= 10 else i - 10
        terminal = "T1" if letra == "A" else "T2"
        puertas.append({
            "id_puerta":       f"{hub}-{letra}{numero:02d}",
            "aeropuerto_iata": hub,
            "numero_puerta":   f"{letra}{numero:02d}",
            "terminal":        terminal,
        })

df_puertas = pl.DataFrame(puertas)
ruta_puertas = f"{LANDING_BASE}/puertas/puertas.parquet"
df_puertas.write_parquet(ruta_puertas)
print(f"  ✅ puertas.parquet: {len(df_puertas)} rows")

# ============================================================================
# 4. Pasajeros (Parquet) — Multi-language with Mimesis
# ============================================================================

print("\n[4/8] Generating pasajeros...")

persona_es = Person(Locale.ES)
persona_pt = Person(Locale.PT_BR)
persona_en = Person(Locale.EN)
persona_fr = Person(Locale.FR)

PAISES_RES   = ["PA", "PE", "CL", "MX", "BR", "AR", "CO", "EC", "CR", "US", "ES", "FR"]
PESOS_PAISES = [0.18, 0.14, 0.10, 0.10, 0.08, 0.07, 0.09, 0.05, 0.06, 0.06, 0.03, 0.04]

MILLAS_MEDIA_TIER = {
    "Ninguno":  0,
    "Plata":    15_000,
    "Oro":      40_000,
    "Platino":  80_000,
    "Diamante": 150_000,
}

def persona_por_pais(pais):
    if pais == "BR":
        return persona_pt
    if pais == "US":
        return persona_en
    if pais == "FR":
        return persona_fr
    return persona_es

pasajeros = []
for _ in range(N_PASAJEROS):
    pais = random.choices(PAISES_RES, weights=PESOS_PAISES)[0]
    p    = persona_por_pais(pais)
    tier = random.choices(TIERS_LEALTAD, weights=PESOS_TIER)[0]
    pasajeros.append({
        "id_pasajero":        str(uuid.uuid4()),
        "nombre":             p.first_name(),
        "apellido":           p.last_name(),
        "email":              p.email(),
        "telefono":           p.telephone(),
        "pais_residencia":    pais,
        "fecha_nacimiento":   p.birthdate(min_year=1950, max_year=2008).isoformat(),
        "tier_lealtad":       tier,
        "millas_acumuladas":  max(0, int(random.gauss(MILLAS_MEDIA_TIER[tier], 5_000))),
        "fecha_registro":     (date(2020, 1, 1) + timedelta(days=random.randint(0, 2_000))).isoformat(),
    })

df_pasajeros = pl.DataFrame(pasajeros)
ruta_pasajeros = f"{LANDING_BASE}/pasajeros/pasajeros.parquet"
df_pasajeros.write_parquet(ruta_pasajeros)
print(f"  ✅ pasajeros.parquet: {len(df_pasajeros)} rows")

# ============================================================================
# 5. Vuelos (Parquet)
# ============================================================================

print("\n[5/8] Generating vuelos...")

aeronaves_activas = [a["id_aeronave"] for a in aeronaves if a["estado"] == "Activa"]
no_hubs           = [a[0] for a in AEROPUERTOS if a[0] not in HUBS]

def elegir_ruta():
    r = random.random()
    if r < 0.40:
        hub = "PTY"
    elif r < 0.70:
        hub = "LIM"
    elif r < 0.90:
        hub = "SCL"
    else:
        origen, destino = random.sample(no_hubs, 2)
        return origen, destino
    otro = random.choice([a for a in (no_hubs + HUBS) if a != hub])
    if random.random() < 0.5:
        return hub, otro
    return otro, hub

vuelos     = []
dia_actual = FECHA_INICIO

while dia_actual < FECHA_FIN:
    for _ in range(N_VUELOS_POR_DIA):
        origen, destino = elegir_ruta()

        hora_salida   = random.randint(5, 22)
        minuto_salida = random.choice([0, 15, 30, 45])
        salida_prog   = dia_actual.replace(hour=hora_salida, minute=minuto_salida)

        duracion_min = random.randint(60, 8 * 60)
        llegada_prog = salida_prog + timedelta(minutes=duracion_min)

        estado = random.choices(
            ["Programado", "EnRuta", "Aterrizado", "Retrasado", "Cancelado", "Desviado"],
            weights=[0.02, 0.02, 0.85, 0.07, 0.03, 0.01]
        )[0]

        if estado == "Aterrizado":
            delay = random.randint(-5, 30)
            salida_real  = salida_prog  + timedelta(minutes=delay)
            llegada_real = llegada_prog + timedelta(minutes=delay + random.randint(-5, 10))
        elif estado == "Retrasado":
            delay = random.randint(60, 240)
            salida_real  = salida_prog  + timedelta(minutes=delay)
            llegada_real = llegada_prog + timedelta(minutes=delay)
        elif estado == "EnRuta":
            salida_real  = salida_prog + timedelta(minutes=random.randint(-5, 15))
            llegada_real = None
        else:
            salida_real  = None
            llegada_real = None

        puerta = f"{origen}-{random.choice(['A','B'])}{random.randint(1, 20):02d}" if origen in HUBS else None

        vuelos.append({
            "id_vuelo":            str(uuid.uuid4()),
            "numero_vuelo":        f"AL{random.randint(1000, 9999)}",
            "origen_iata":         origen,
            "destino_iata":        destino,
            "fecha_prog_salida":   salida_prog.isoformat(),
            "fecha_prog_llegada":  llegada_prog.isoformat(),
            "fecha_real_salida":   salida_real.isoformat()  if salida_real  else None,
            "fecha_real_llegada":  llegada_real.isoformat() if llegada_real else None,
            "id_aeronave":         random.choice(aeronaves_activas),
            "estado":              estado,
            "id_puerta_salida":    puerta,
            "id_disrupcion":       None,
        })
    dia_actual += timedelta(days=1)

print(f"  ✅ vuelos generated (pre-disruptions): {len(vuelos)} rows")

# ============================================================================
# 6. Disrupciones (Parquet)
# ============================================================================

print("\n[6/8] Generating disrupciones...")

disrupciones = []

disrupciones_mayores = [
    {
        "dia_offset":     15, "hub": "PTY", "tipo": "Clima", "duracion_horas": 8,
        "descripcion":    "Tormenta tropical que cerró el aeropuerto por 8 horas, afectando operaciones en toda la red.",
    },
    {
        "dia_offset":     42, "hub": "LIM", "tipo": "Tecnico", "duracion_horas": 6,
        "descripcion":    "Falla técnica en sistema de combustible afectó el despacho de vuelos saliendo de Lima.",
    },
    {
        "dia_offset":     67, "hub": "SCL", "tipo": "ControlAereo", "duracion_horas": 5,
        "descripcion":    "Huelga parcial de controladores aéreos en Santiago causó cancelaciones en cascada.",
    },
]

ids_mayores = []
for d in disrupciones_mayores:
    inicio = FECHA_INICIO + timedelta(days=d["dia_offset"], hours=random.randint(6, 18))
    fin    = inicio + timedelta(hours=d["duracion_horas"])
    id_d   = str(uuid.uuid4())
    ids_mayores.append((id_d, d["hub"], inicio, fin))
    disrupciones.append({
        "id_disrupcion":        id_d,
        "tipo":                 d["tipo"],
        "aeropuerto_afectado":  d["hub"],
        "timestamp_inicio":     inicio.isoformat(),
        "timestamp_fin":        fin.isoformat(),
        "severidad":            "Alta",
        "descripcion":          d["descripcion"],
    })

for _ in range(N_DISRUPCIONES_MENORES):
    dia    = random.randint(0, N_DIAS - 1)
    inicio = FECHA_INICIO + timedelta(days=dia, hours=random.randint(0, 22))
    fin    = inicio + timedelta(hours=random.randint(1, 3))
    tipo   = random.choice(TIPOS_DISRUPCION)
    disrupciones.append({
        "id_disrupcion":        str(uuid.uuid4()),
        "tipo":                 tipo,
        "aeropuerto_afectado":  random.choice([a[0] for a in AEROPUERTOS]),
        "timestamp_inicio":     inicio.isoformat(),
        "timestamp_fin":        fin.isoformat(),
        "severidad":            random.choice(["Media", "Baja"]),
        "descripcion":          f"Disrupción menor tipo {tipo}.",
    })

df_disrupciones = pl.DataFrame(disrupciones, infer_schema_length=None)
ruta_disrupciones = f"{LANDING_BASE}/disrupciones/disrupciones.parquet"
df_disrupciones.write_parquet(ruta_disrupciones)
print(f"  ✅ disrupciones.parquet: {len(df_disrupciones)} rows (3 major)")

# Mark affected flights
vuelos_afectados = 0
for id_d, hub, ini, fin in ids_mayores:
    for v in vuelos:
        sp = datetime.fromisoformat(v["fecha_prog_salida"])
        if v["origen_iata"] == hub and ini <= sp <= fin:
            v["id_disrupcion"] = id_d
            v["estado"] = random.choices(["Retrasado", "Cancelado"], weights=[0.7, 0.3])[0]
            if v["estado"] == "Cancelado":
                v["fecha_real_salida"]  = None
                v["fecha_real_llegada"] = None
            else:
                delay = random.randint(120, 300)
                v["fecha_real_salida"]  = (sp + timedelta(minutes=delay)).isoformat()
                lp = datetime.fromisoformat(v["fecha_prog_llegada"])
                v["fecha_real_llegada"] = (lp + timedelta(minutes=delay)).isoformat()
            vuelos_afectados += 1

df_vuelos = pl.DataFrame(vuelos, infer_schema_length=None)
ruta_vuelos = f"{LANDING_BASE}/vuelos/vuelos.parquet"
df_vuelos.write_parquet(ruta_vuelos)
print(f"  ✅ vuelos.parquet: {len(df_vuelos)} rows ({vuelos_afectados} affected by disruptions)")

# ============================================================================
# 7. Reservas (Parquet)
# ============================================================================

print("\n[7/8] Generating reservas...")

ids_pasajeros     = [p["id_pasajero"] for p in pasajeros]
capacidad_por_aer = {a["id_aeronave"]: a["capacidad_total"] for a in aeronaves}

def generar_pnr():
    return "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=6))

reservas = []
for v in vuelos:
    cap          = capacidad_por_aer[v["id_aeronave"]]
    load_factor  = random.uniform(0.70, 0.95)
    n_reservas   = int(cap * load_factor)
    pax_vuelo    = random.sample(ids_pasajeros, min(n_reservas, len(ids_pasajeros)))
    sp           = datetime.fromisoformat(v["fecha_prog_salida"])
    es_cancelado = v["estado"] == "Cancelado"

    for pax_id in pax_vuelo:
        reservas.append({
            "pnr":              generar_pnr(),
            "id_pasajero":      pax_id,
            "id_vuelo":         v["id_vuelo"],
            "clase_tarifa":     random.choices(CLASES_TARIFA, weights=PESOS_CLASE)[0],
            "fecha_reserva":    (sp - timedelta(days=random.randint(1, 120))).date().isoformat(),
            "canal_venta":      random.choices(CANALES_VENTA, weights=PESOS_CANAL)[0],
            "precio_usd":       round(random.uniform(80, 1500), 2),
            "asiento":          None if es_cancelado else f"{random.randint(1, 40)}{random.choice('ABCDEF')}",
            "estado_checkin":   "NoRegistrado" if es_cancelado else random.choices(ESTADOS_CHECKIN, weights=PESOS_CHECKIN)[0],
        })

df_reservas = pl.DataFrame(reservas, infer_schema_length=None)
ruta_reservas = f"{LANDING_BASE}/reservas/reservas.parquet"
df_reservas.write_parquet(ruta_reservas)
print(f"  ✅ reservas.parquet: {len(df_reservas)} rows")

# ============================================================================
# 8. Encuestas NPS (JSONL) — Curated multi-language comments
# ============================================================================

print("\n[8/8] Generating encuestas_nps...")

# ============================================================================
# 8. Encuestas NPS (JSONL) — v2: compositional comments + ops-conditioned NPS
#    v2 changes vs v1:
#    - NPS score depends on the flight's real operational outcome (delay),
#      with calibrated noise → route-level OTP↔NPS correlation ≈ 0.5
#    - Comments are slot-based compositions (thousands of distinct strings),
#      and delayed-flight detractors complain about the actual delay/route
# ============================================================================

def _hora_palabra(n_min):
    return random.choice(["dos", "tres", "cuatro", "cinco"])

# Flight-complaint detractors (used when the surveyed flight was delayed/cancelled)
DETRACTORES_VUELO = {
    "es": [
        "El vuelo {ruta} salió con {min} minutos de retraso y nadie explicó nada. Perdí mi conexión en {dest}. Inaceptable.",
        "Esperamos {hora} horas en el aeropuerto por el retraso del vuelo {ruta}. La información llegó tarde y por el canal equivocado. Muy mal manejo.",
        "El retraso de {min} minutos del vuelo {ruta} arruinó mi itinerario completo. Nadie de aeroLatinos asumió el costo. Decepcionado.",
        "Vuelo {ruta} con {min} minutos de demora y sin compensación. El proceso de reembolso lleva {dias} días sin respuesta. No recomiendo.",
        "La tripulación del vuelo {ruta} no dio información durante la espera de {min} minutos. La mesa de ayuda no respondió. Frustrante.",
        "Mi vuelo {ruta} se retrasó {min} minutos y perdí una reunión crítica. La aerolínea debería gestionar mejor sus operaciones.",
        "Demora de {min} minutos en el {ruta} y el embarque fue un caos. Nadie de la aerolínea se hizo responsable. Muy mala experiencia.",
        "El {ruta} salió {min} minutos tarde y mi equipaje llegó dañado. El reclamo en {canal} lleva {dias} días sin resolver. Pésimo.",
    ],
    "pt": [
        "O voo {ruta} saiu com {min} minutos de atraso e ninguém explicou nada. Perdi minha conexão em {dest}. Inaceitável.",
        "Esperamos {hora} horas no aeroporto pelo atraso do voo {ruta}. A informação chegou tarde e pelo canal errado. Péssimo gerenciamento.",
        "O atraso de {min} minutos do voo {ruta} arruinou meu itinerário completo. Ninguém da aeroLatinos assumiu o custo. Decepcionante.",
        "Voo {ruta} com {min} minutos de atraso e sem compensação. O reembolso levam {dias} dias sem resposta. Não recomendo.",
        "Minha voo {ruta} atrasou {min} minutos e perdi uma reunião crítica. A companhia deveria gerenciar melhor as operações.",
        "Atraso de {min} minutos no {ruta} e o embarque foi um caos. Ninguém assumiu responsabilidade. Péssima experiência.",
    ],
    "en": [
        "Flight {ruta} departed {min} minutes late with no explanation. I missed my connection in {dest}. Unacceptable.",
        "We waited {hora} hours at the airport for flight {ruta} delays. Information came late and through the wrong channel. Very poor handling.",
        "The {min}-minute delay of flight {ruta} ruined my whole itinerary. Nobody at aeroLatinos covered the cost. Disappointing.",
        "Flight {ruta} was {min} minutes late with no compensation. My refund claim has been open for {dias} days. Do not recommend.",
        "My flight {ruta} was delayed {min} minutes and I missed a critical meeting. This airline should manage operations better.",
        "{min}-minute delay on {ruta} and boarding was chaos. No one took responsibility. Very bad experience.",
    ],
    "fr": ["Le vol {ruta} est parti avec {min} minutes de retard et personne n'a rien expliqué. J'ai raté ma correspondance à {dest}. Inacceptable.", "Nous avons attendu {hora} heures à l'aéroport pour le retard du vol {ruta}. L'information est arrivée tard et par le mauvais canal. Très mauvaise gestion.", "Le retard de {min} minutes du vol {ruta} a ruiné tout mon itinéraire. Personne chez aeroLatinos n'a assumé le coût. Décevant.", "Vol {ruta} avec {min} minutes de retard et aucune compensation. Mon remboursement est en attente depuis {dias} jours. Je ne recommande pas.", "Mon vol {ruta} a été retardé de {min} minutes et j'ai manqué une réunion critique. La compagnie devrait mieux gérer ses opérations.", "Retard de {min} minutes sur le {ruta} et l'embarquement était un chaos. Personne n'a pris de responsabilité. Très mauvaise expérience."],
}

# Channel/price/service detractors (used when the flight itself was fine)
DETRACTORES_CANAL = {
    "es": [
        "El call center me dejó {min2} minutos en espera para un {objeto}. {resolucion}. Muy mala experiencia.",
        "El precio que me mostraron cambió al momento del pago. Terminé pagando {pct}% más de lo esperado en el canal {canal}. No lo volveré a usar.",
        "Me cobraron equipaje extra de ${usd} sin avisarme antes. El canal web no mostraba la política claramente. Siento que me engañaron.",
        "El check-in web no reconoció mi reserva. Tuve que hacer fila de {min2} minutos en el aeropuerto. Perdí tiempo valioso.",
        "Compré clase ejecutiva pero la experiencia no lo justificó. {detalle_asiento}. Decepcionante para el precio pagado.",
        "El proceso de reembolso lleva más de {dias} días. Nadie del canal {canal} me da información clara. Inaceptable.",
        "La aplicación móvil se congeló {veces} veces durante la compra. Tuve que reintentar desde la web para conseguir mi PNR. Frustrante.",
        "Compré por la agencia y la confirmación llegó {dias}2 días tarde. Tuve que llamar varias veces para conseguir el boleto. Poco confiable.",
        "La clase económica es muy estrecha para vuelos largos. Con el retraso de {min2} minutos además, el precio no se compensa. No recomiendo.",
        "Recibí información contradicticia de {canal} y del call center antes de volar. Eso generó mucho estrés. Falta de coordinación.",
    ],
    "pt": [
        "O call center me deixou {min2} minutos na espera para {objeto_pt}. {resolucao_pt}. Péssima experiência.",
        "O preço mudou no momento do pagamento. Paguei {pct}% a mais do que o esperado no canal {canal}. Não usarei de novo.",
        "Cobraram bagagem extra de ${usd} sem aviso prévio. O site não mostrava a política com clareza. Senti que me enganaram.",
        "O check-in online não reconheceu minha reserva. Tive que enfrentar fila de {min2} minutos no aeroporto. Perdi tempo precioso.",
        "Paguei classe executiva mas o serviço não justificou. O assento não era o prometido. Decepcionante.",
        "O reembolso leva mais de {dias} dias. Ninguém do canal {canal} dá informação clara. Inaceitável.",
        "O aplicativo travou {veces} vezes durante a compra. Tive que recomeçar pelo site. Muito frustrante.",
        "Comprei pela agência e a confirmação chegou {dias}2 dias tarde. Tive que ligar várias vezes. Pouco confiável.",
    ],
    "en": [
        "Call center kept me on hold for {min2} minutes for {objeto_en}. {resolution_en}. Terrible experience.",
        "The price changed at payment without warning. I paid {pct}% more than expected through the {canal} channel. Will not use it again.",
        "They charged me ${usd} in extra luggage fees without prior notice. The web channel was not transparent. Felt misled.",
        "Online check-in did not recognize my booking. I had to stand in line for {min2} minutes at the airport. Wasted time.",
        "I paid for business class but the service did not justify it. The seat was not as promised. Disappointing.",
        "The refund process is taking over {dias} days. Nobody on the {canal} channel gives clear information. Unacceptable.",
        "The mobile app crashed {veces} times during checkout. I had to restart on the website. Very frustrating.",
        "I booked through the agency and confirmation arrived {dias}2 days late. Had to call multiple times. Unreliable.",
    ],
    "fr": ["Le call center m'a laissé {min2} minutes en attente pour {objeto_fr}. {resolution_fr}. Très mauvaise expérience.", "Le prix a changé au moment du paiement. J'ai payé {pct}% de plus que prévu via le canal {canal}. Je ne l'utiliserai plus.", "On m'a facturé {usd}$ de bagage supplémentaire sans préavis. Le site web n'affichait pas clairement la politique. Je me sens trompé.", "Le check-in en ligne n'a pas reconnu ma réservation. J'ai dû faire la queue {min2} minutes à l'aéroport. Temps perdu.", "J'ai payé la classe affaires mais l'expérience ne le justifiait pas. La siègerie n'était pas celle promise. Décevant.", "Le remboursement prend plus de {dias} jours. Personne du canal {canal} ne donne d'information claire. Inacceptable.", "L'application mobile a planté {veces} fois pendant l'achat. J'ai dû recommencer sur le site. Très frustrant.", "J'ai réservé via l'agence et la confirmation est arrivée {dias}2 jours en retard. J'ai dû appeler plusieurs fois. Peu fiable."],
}

PASIVOS = {
    "es": [
        "La compra por {canal} fue aceptable. Habría preferido menos pasos para confirmar. El precio era razonable.",
        "Llamé al call center y resolvieron mi consulta en {min2} minutos. No fue excelente pero funcionó. Volvería a llamar.",
        "El vuelo {ruta} salió casi a tiempo, con {min} minutos de diferencia. Cumplió lo básico. Sin mayores sorpresas.",
        "Todo el proceso tardó {min2} minutos más de lo esperado. El resultado fue aceptable. Lo volvería a considerar.",
        "Clase {clase} buena pero no extraordinaria. El precio era alto para lo que ofrecen. Valor discutible.",
        "Experiencia estándar para la industria en la ruta {ruta}. Nada que destacar. Cumple expectativas mínimas.",
        "La app funcionó bien la mayoría del tiempo. Un par de detalles al cargar tarifas hacia {dest}. Nada grave.",
        "El precio estuvo en línea con otras aerolíneas en clase {clase}. El vuelo cumplió lo básico. Equilibrado.",
        "El embarque del {ruta} fue normal, con {min} minutos de espera en puerta. Nada fuera de lo común.",
        "La confirmación por {canal} llegó en {min2} minutos. Proceso correcto sin brillo. Aceptable.",
    ],
    "pt": [
        "A compra pelo {canal} foi aceitável. Preferiria menos etapas. O preço estava razoável.",
        "Liguei ao call center e resolveram em {min2} minutos. Não foi excelente mas funcionou. Voltaria a ligar.",
        "O voo {ruta} saiu quase no horário, com {min} minutos de diferença. Cumpriu o básico. Sem surpresas.",
        "Todo o processo demorou mais do que o previsto. O resultado foi aceitável. Consideraria de novo.",
        "Classe {clase} boa mas não excepcional. Preço alto para o oferecido. Valor discutível.",
        "Experiência padrão para o setor. Nada se destaca. Cumpre o mínimo.",
    ],
    "en": [
        "Purchase through {canal} was acceptable. Would prefer fewer steps. The price was reasonable.",
        "I called the call center and they handled it in {min2} minutes. Not excellent but it worked. Would call again.",
        "Flight {ruta} left nearly on time, {min} minutes off. It covered the basics. No surprises.",
        "The whole process took longer than expected. The result was acceptable. Would consider again.",
        "{clase} class was good but not outstanding. Price was high for what is offered. Debatable value.",
        "Standard industry experience. Nothing stands out. Meets minimum expectations.",
    ],
    "fr": ["L'achat via {canal} était acceptable. J'aurais préféré moins d'étapes. Le prix était correct.", "J'ai appelé le call center et ils ont répondu en {min2} minutes. Pas excellent mais ça fonctionne. Je rappellerais.", "Le vol {ruta} est parti presque à l'heure, avec {min} minutes d'écart. L'essentiel était là. Sans surprise.", "Tout le processus a pris {min2} minutes de plus que prévu. Le résultat était acceptable. Je réutiliserais.", "Classe {clase} correcte mais pas extraordinaire. Le prix était élevé pour ce qui est offert. Valeur discutable.", "Expérience standard pour le secteur sur la route {ruta}. Rien à signaler. Le minimum est atteint."],
}

PROMOTORES_PUNTUAL = {
    "es": [
        "Mi vuelo {ruta} salió y llegó puntual. El personal fue atento y el precio justo. Recomiendo aeroLatinos sin dudar.",
        "El {ruta} salió exactamente a la hora programada. El embarque fue rápido y organizado. Excelente operación.",
        "Viaje cómodo y puntual en el {ruta}. Todo fluyó desde el check-in hasta el aterrizaje. Una aerolínea de primera.",
        "Puntualidad impecable en el {ruta}. Conexión protegida y equipaje a tiempo. Felicitaciones al equipo de operaciones.",
    ],
    "pt": [
        "Meu voo {ruta} partiu e chegou pontual. Atendimento atencioso e preço justo. Recomendo sem hesitar.",
        "O {ruta} saiu exatamente no horário. Embarque rápido e organizado. Operação excelente.",
        "Viagem confortável e pontual no {ruta}. Tudo fluiu do check-in ao pouso. Companhia de primeira.",
    ],
    "en": [
        "My flight {ruta} departed and arrived on time. Attentive staff and fair price. Recommend aeroLatinos without hesitation.",
        "The {ruta} left exactly on schedule. Boarding was fast and organized. Excellent operation.",
        "Comfortable and punctual trip on {ruta}. Everything flowed from check-in to landing. A top-tier airline.",
    ],
    "fr": ["Mon vol {ruta} est parti et arrivé à l'heure. Personnel attentionné et prix juste. Je recommande aeroLatinos sans hésiter.", "Le {ruta} est parti exactement à l'heure prévue. Embarquement rapide et organisé. Excellente opération.", "Voyage confortable et ponctuel sur le {ruta}. Tout s'est enchaîné du check-in à l'atterrissage. Une compagnie de premier ordre."],
}

PROMOTORES_SERVICIO = {
    "es": [
        "Excelente experiencia desde el canal {canal}. La compra fue rápida y el PNR llegó en {seg} segundos. Sin duda volveré a usarlo.",
        "La aplicación móvil es muy intuitiva. Pude comprar en menos de {min3} minutos. Recomiendo totalmente.",
        "El call center me atendió con profesionalismo. Resolvieron todo en {min2} minutos y con claridad. Excelente servicio.",
        "Clase {clase} superó mis expectativas en el {ruta}. El servicio a bordo fue excepcional. Pagaría otra vez por esta experiencia.",
        "Precio muy competitivo para la ruta {ruta}. La calidad del servicio estuvo por encima. Felicitaciones a aeroLatinos.",
        "Primera clase en el {ruta} realmente se siente premium. {detalle_crew}. Definitivamente volveré.",
        "Todo impecable desde la compra hasta el aterrizaje del {ruta}. El check-in fue cuestión de {seg} segundos. Una aerolínea de primera.",
        "El equipo de la aerolínea fue muy amable en todo el {ruta}. Viaje cómodo y tranquilo. Excelente elección.",
        "La app funcionó perfecta para gestionar mi vuelo a {dest}. Notificaciones puntuales en cada etapa. Gran producto.",
        "El proceso en el aeropuerto fue fluido, {min2} minutos de espera máxima. Personal atento y eficiente. Muy bien logrado.",
    ],
    "pt": [
        "Excelente experiência pelo canal {canal}. Compra rápida e o localizador chegou na hora. Voltarei a usar.",
        "O aplicativo é muito intuitivo. Comprei em menos de {min3} minutos. Recomendo totalmente.",
        "O call center me atendeu com profissionalismo. Resolveram tudo em {min2} minutos. Serviço excelente.",
        "Classe {clase} superou expectativas. Serviço a bordo excepcional. Pagaria novamente.",
        "Preço muito competitivo para a rota {ruta}. Qualidade acima do esperado. Parabéns à aeroLatinos.",
        "Primeira classe realmente premium. Cada detalhe cuidado com zelo. Definitivamente voltarei.",
    ],
    "en": [
        "Excellent experience through the {canal} channel. Fast purchase and instant PNR. Will definitely use it again.",
        "The mobile app is very intuitive. I booked in under {min3} minutes. Highly recommend.",
        "Call center handled me with professionalism. Everything solved in {min2} minutes. Excellent service.",
        "{clase} class exceeded my expectations. On-board service was exceptional. Would pay again.",
        "Very competitive price for route {ruta}. Service quality above average. Congrats to aeroLatinos.",
        "First class truly feels premium. Every detail handled with care. I will definitely return.",
    ],
    "fr": ["Excellente expérience via le canal {canal}. Achat rapide et PNR reçu en {seg} secondes. Je réutiliserai sans hésiter.", "L'application mobile est très intuitive. J'ai pu acheter en moins de {min3} minutes. Je recommande totalement.", "Le call center m'a reçu avec professionnalisme. Tout a été réglé en {min2} minutes et clairement. Excellent service.", "La classe {clase} a dépassé mes attentes sur le {ruta}. Le service à bord était exceptionnel. Je repaierais pour ça.", "Prix très compétitif pour la route {ruta}. La qualité du service était au-dessus. Félicitations à aeroLatinos.", "La première classe sur le {ruta} est vraiment premium. {detail_fr}. J'y reviendrai définitivement.", "Tout impeccable de l'achat à l'atterrissage du {ruta}. Check-in en {seg} secondes. Une compagnie de premier ordre.", "L'application a parfaitement géré mon vol vers {dest}. Notifications ponctuelles à chaque étape. Superbe produit."],
}

# Slot fillers (numbers/details vary per call → thousands of distinct strings)
_SLOTS = {
    "min2": lambda: random.randint(20, 70),
    "min3": lambda: random.choice([2, 3, 4, 5]),
    "hora": lambda: _hora_palabra(0),
    "dias": lambda: random.choice([21, 25, 30, 35, 40]),
    "dias2": lambda: random.choice([3, 4, 5, 6]),
    "pct": lambda: random.choice([8, 12, 15, 18, 20]),
    "usd": lambda: random.choice([35, 45, 55, 65]),
    "veces": lambda: random.choice(["dos", "tres"]),
    "canal": lambda: random.choice(["web", "la app", "agencia", "call center"]),
    "clase": lambda: random.choice(["económica", "ejecutiva"]),
    "objeto": lambda: random.choice(["cambio de vuelo", "reembolso", "asignación de asiento", "actualización de mi reserva"]),
    "objeto_pt": lambda: random.choice(["troca de voo", "reembolso", "assento", "atualizar reserva"]),
    "objeto_en": lambda: random.choice(["a flight change", "a refund", "a seat assignment", "a booking update"]),
    "resolucion": lambda: random.choice([
        "La agente no sabía resolver el tema",
        "Me transfirieron tres veces sin solución",
        "Tuve que repetir todo desde cero",
        "Me dieron información equivocada",
    ]),
    "resolucao_pt": lambda: random.choice([
        "A atendente não sabia resolver",
        "Me transferiram três vezes sem solução",
        "Tive que repetir tudo do zero",
    ]),
    "resolution_en": lambda: random.choice([
        "The agent could not resolve it",
        "I was transferred three times",
        "I had to start over from scratch",
    ]),
    "seg": lambda: random.choice([10, 15, 20, 30, 45]),
    "objeto_fr": lambda: random.choice(['un changement de vol', 'un remboursement', 'un siège', 'une modification de réservation']),
    "resolution_fr": lambda: random.choice(["L'agent ne savait pas résoudre", "On m'a transféré trois fois", "J'ai dû tout recommencer"]),
    "detail_fr": lambda: random.choice(['Chaque détail était soigné', "L'équipage était impeccable", "L'embarquement était parfait", "Le service à bord était d'un autre niveau"]),

    "detalle_crew": lambda: random.choice([
        "Cada detalle cuidado con esmero",
        "La tripulación fue impecable",
        "El embarque fue impecable",
        "La atención superó lo esperado",
        "El servicio a bordo fue de otro nivel",
    ]),
    "detalle_asiento": lambda: random.choice([
        "El asiento no era el prometido",
        "El espacio era muy reducido",
        "La pantalla no funcionaba",
        "El servicio a bordo fue mínimo",
    ]),
}

_CIERRES = {
    "es": ["Sin más.", "Ojalá lo mejoren.", "Espero una respuesta.", "Gracias por escuchar.",
           "Sigo esperando solución.", "Nada más que agregar.", "Comparto para que sirva.",
           "Esa es mi experiencia.", "Saludos.", "Quedo atento.", "Lo digo con respeto.",
           "Solo cuento los hechos.", "Es una pena.", "Me sorprendió gratamente.",
           "Sigan así.", "AeroLatinos puede mejorar.", "Se lo recomiendo a mi familia.",
           "Por eso sigo volando con ellos.", "No cambiaría nada.", "Ojalá siempre sea así.",
           "Volveré a elegirlos.", "Me quedé con buena impresión.", "Todo bien en general.",
           "Experiencia para repetir."],
    "pt": ["Sem mais.", "Espero que melhorem.", "Aguardo resposta.", "Obrigado por ouvir.",
           "Sigo aguardando solução.", "Nada mais a acrescentar.", "Essa é minha experiência.",
           "Abraços.", "Fico no aguardo.", "É uma pena.", "Continue assim.",
           "Voltarei a escolhê-los.", "Fiquei com boa impressão.", "Tudo bem em geral.",
           "Só relato os fatos.", "Compartilho para ajudar."],
    "en": ["Nothing else to add.", "Hope they improve.", "Awaiting a response.",
           "Thanks for listening.", "Still waiting for a fix.", "That was my experience.",
           "Regards.", "Sharing so it helps.", "Nothing more.", "Stay safe.",
           "Just reporting facts.", "Keep it up.", "I'll choose them again.",
           "Good impression overall.", "All good in general.", "A shame, really.",
           "Would recommend to family.", "Nothing I would change.", "Hope it stays this way.",
           "That's why I keep flying with them."],
    "fr": ["Rien de plus à ajouter.", "J'espère qu'ils s'améliorent.", "J'attends une réponse.", "Merci de m'écouter.", "Toujours en attente de solution.", "C'était mon expérience.", "Cordialement.", "Je partage pour aider.", "C'est dommage.", "Continuez ainsi.", "Je les choisirai encore.", "Bonne impression générale.", "Je rapporte juste les faits.", "À bientôt.", "Tout bien en général."],
}

def _comentar(pattern, vuelo, delay_min, idioma="es"):
    """Fill a comment pattern with per-call random slots + flight context + varying closer."""
    ruta = f"{vuelo['origen_iata']}-{vuelo['destino_iata']}"
    out = pattern.replace("{dias}2", "{dias2}")  # two-digit-day compounds
    slots = {k: f() for k, f in _SLOTS.items()}
    slots["ruta"] = ruta
    slots["dest"] = vuelo["destino_iata"]
    slots["min"] = int(max(delay_min if delay_min else random.randint(20, 90), 15))
    if not out.endswith(("!", "?", ".",)):
        out += "."
    return out.format(**slots) + " " + random.choice(_CIERRES[idioma])

# --- v2: NPS conditioned on the flight's real outcome (calibration knobs) ---
# (promoter, passive, detractor) probabilities per flight outcome category.
PROBS_NPS_V2 = {
    "ontime": (0.65, 0.29, 0.06),
    "minor":  (0.32, 0.42, 0.26),
    "major":  (0.06, 0.22, 0.72),
    "severe": (0.03, 0.11, 0.86),
}

def _cat_vuelo(vuelo):
    """Operational outcome category of the flight the passenger flew."""
    if vuelo["estado"] in ("Cancelado", "Desviado"):
        return "severe"
    sr = vuelo.get("fecha_real_salida")
    if not sr:
        return "ontime"  # Programado/EnRuta: near-zero departure jitter
    delay = (datetime.fromisoformat(sr) - datetime.fromisoformat(vuelo["fecha_prog_salida"])).total_seconds() / 60.0
    if delay >= 180:
        return "severe"
    if delay >= 60:
        return "major"
    if delay >= 15:
        return "minor"
    return "ontime"

def score_condicionado(vuelo):
    """v2: draw the NPS score biased by the flight's operational outcome."""
    p_pro, p_pas, p_det = PROBS_NPS_V2[_cat_vuelo(vuelo)]
    r = random.random()
    if r < p_pro:
        return random.choice([9, 10]), "promotor"
    if r < p_pro + p_pas:
        return random.choice([7, 8]), "pasivo"
    return random.randint(0, 6), "detractor"


TASA_RESPUESTA_ENCUESTA = 0.25
CANALES_RESPUESTA       = ["Email", "SMS", "App"]
PESOS_CANAL_RESPUESTA   = [0.50, 0.30, 0.20]

def idioma_por_pais(pais):
    if pais == "BR":
        return "pt"
    if pais in ("US", "CA"):
        return "en"
    if pais == "FR":
        return "fr"
    return "es"

pais_por_pasajero = {p["id_pasajero"]: p["pais_residencia"] for p in pasajeros}
vuelo_por_id = {v["id_vuelo"]: v for v in vuelos}

def _delay_min_vuelo(vuelo):
    """Departure delay in minutes (None when no real departure)."""
    sr = vuelo.get("fecha_real_salida")
    if not sr:
        return None
    return (datetime.fromisoformat(sr) - datetime.fromisoformat(vuelo["fecha_prog_salida"])).total_seconds() / 60.0

def comentario_v2(idioma, bucket, vuelo, cat, delay_min):
    """Pick and fill a comment aligned with the bucket AND the flight outcome."""
    if bucket == "detractor":
        if cat in ("major", "severe") and random.random() < 0.75:
            pool = DETRACTORES_VUELO[idioma]
        else:
            pool = DETRACTORES_CANAL[idioma]
    elif bucket == "promotor":
        if cat == "ontime" and random.random() < 0.55:
            pool = PROMOTORES_PUNTUAL[idioma]
        else:
            pool = PROMOTORES_SERVICIO[idioma]
    else:
        pool = PASIVOS[idioma]
    return _comentar(random.choice(pool), vuelo, delay_min, idioma)

encuestas = []
for r in reservas:
    if r["estado_checkin"] != "Abordado":
        continue
    if random.random() > TASA_RESPUESTA_ENCUESTA:
        continue

    vuelo      = vuelo_por_id[r["id_vuelo"]]
    pais       = pais_por_pasajero.get(r["id_pasajero"], "PA")
    idioma     = idioma_por_pais(pais)
    # v2: score conditioned on the flight's real outcome; comment aligned to it
    nps_score, bucket = score_condicionado(vuelo)
    cat        = _cat_vuelo(vuelo)
    delay_min  = _delay_min_vuelo(vuelo)
    comentario = comentario_v2(idioma, bucket, vuelo, cat, delay_min)

    # fecha 1-7 días post vuelo
    sp         = datetime.fromisoformat(vuelo["fecha_prog_salida"])
    fecha_enc  = sp + timedelta(days=random.randint(1, 7), hours=random.randint(0, 23))

    encuestas.append({
        "id_encuesta":     str(uuid.uuid4()),
        "pnr":             r["pnr"],
        "fecha_encuesta":  fecha_enc.isoformat(),
        "idioma":          idioma,
        "nps_score":       nps_score,
        "comentario":      comentario,
        "canal_respuesta": random.choices(CANALES_RESPUESTA, weights=PESOS_CANAL_RESPUESTA)[0],
    })

# Cap a ~20K encuestas: mantiene ágil la Sesión 4 (AI Functions corre LLMs por fila) y el archivo
# liviano. El muestreo aleatorio preserva la distribución de idioma/score y la integridad PNR.
N_ENCUESTAS_MAX = 20_000
if len(encuestas) > N_ENCUESTAS_MAX:
    encuestas = random.sample(encuestas, N_ENCUESTAS_MAX)

# Write JSONL (one JSON object per line)
ruta_encuestas = f"{LANDING_BASE}/encuestas_nps/encuestas_nps.jsonl"
with open(ruta_encuestas, "w") as f:
    for row in encuestas:
        f.write(json.dumps(row) + "\n")

print(f"  ✅ encuestas_nps.jsonl: {len(encuestas)} rows")

# ============================================================================
# v2 validation: route-level OTP↔NPS correlation + comment diversity
# ============================================================================
pnr_vuelo = {r["pnr"]: vuelo_por_id[r["id_vuelo"]] for r in reservas}

otp_ruta = {}  # ruta -> [a_tiempo, total_with_departure]
for v in vuelos:
    ruta = f"{v['origen_iata']}-{v['destino_iata']}"
    if v.get("fecha_real_salida"):
        otp_ruta.setdefault(ruta, [0, 0])
        otp_ruta[ruta][1] += 1
        d = (datetime.fromisoformat(v["fecha_real_salida"]) - datetime.fromisoformat(v["fecha_prog_salida"])).total_seconds() / 60.0
        if v["estado"] == "Aterrizado" and d < 15:
            otp_ruta[ruta][0] += 1

nps_ruta = {}
for e in encuestas:
    v = pnr_vuelo.get(e["pnr"])
    if not v:
        continue
    ruta = f"{v['origen_iata']}-{v['destino_iata']}"
    nps_ruta.setdefault(ruta, []).append(e["nps_score"])

pairs = [(otp_ruta[k][0] / otp_ruta[k][1], sum(nps_ruta[k]) / len(nps_ruta[k])) for k in nps_ruta if k in otp_ruta and otp_ruta[k][1] >= 5]
n = len(pairs)
if n >= 3:
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sx = (sum((p[0] - mx) ** 2 for p in pairs)) ** 0.5
    sy = (sum((p[1] - my) ** 2 for p in pairs)) ** 0.5
    r_pearson = cov / (sx * sy) if sx * sy else 0.0
else:
    r_pearson = float("nan")

sample = random.sample(encuestas, min(1000, len(encuestas)))
distinct = len(set(e["comentario"] for e in sample))
buckets = {}
for e in sample:
    b = "detractor" if e["nps_score"] <= 6 else ("pasivo" if e["nps_score"] <= 8 else "promotor")
    buckets[b] = buckets.get(b, 0) + 1

print("\n[v2 VALIDATION]")
print(f"  route-level OTP↔NPS Pearson r = {r_pearson:.3f}  (target ≈ 0.5) over {n} routes")
print(f"  comment diversity: {distinct} distinct of {len(sample)} sampled (target ≥ 800)")
print(f"  bucket mix in sample: {buckets}")
print(f"  total surveys: {len(encuestas)}")

# ============================================================================
# Summary
# ============================================================================

print("\n" + "=" * 80)
print("DATA GENERATION SUMMARY")
print("=" * 80)

summary = [
    ("aeropuertos (CSV)",   len(df_aeropuertos)),
    ("aeronaves",           len(df_aeronaves)),
    ("puertas",             len(df_puertas)),
    ("pasajeros",           len(df_pasajeros)),
    ("vuelos",              len(df_vuelos)),
    ("disrupciones",        len(df_disrupciones)),
    ("reservas",            len(df_reservas)),
    ("encuestas_nps",       len(encuestas)),
]

print("\n📊 Rows per dataset:\n")
for nombre, filas in summary:
    print(f"  {nombre:22s} → {filas:>10,} rows")

print("\n✅ All datasets generated successfully!")
