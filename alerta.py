"""Alerta de ofertas de vuelos solo ida hacia Berlín (Google Flights vía SerpApi).

Cada corrida:
  1. Elige un grupo rotativo de combinaciones (origen, fecha) para no pasarse
     del cupo gratuito de SerpApi.
  2. Consulta el precio más barato que cumpla los filtros.
  3. Guarda el historial en data/historial.json (el workflow lo commitea).
  4. Si el precio es una oferta según config.yaml, avisa por Telegram.

Variables de entorno:
  SERPAPI_KEY          (obligatoria)
  TELEGRAM_BOT_TOKEN   (opcional; sin esto solo imprime)
  TELEGRAM_CHAT_ID     (opcional)
  FORZAR_TODAS=1       (opcional; consulta todas las combinaciones, ojo con el cupo)
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

RAIZ = Path(__file__).parent
HISTORIAL = RAIZ / "data" / "historial.json"
SERPAPI_URL = "https://serpapi.com/search.json"


# --------------------------------------------------------------------------- #
# Configuración y estado
# --------------------------------------------------------------------------- #
def cargar_config(path: Path = RAIZ / "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cargar_historial() -> dict:
    if HISTORIAL.exists():
        return json.loads(HISTORIAL.read_text(encoding="utf-8"))
    return {"observaciones": [], "alertas": {}}


def guardar_historial(h: dict) -> None:
    HISTORIAL.parent.mkdir(parents=True, exist_ok=True)
    HISTORIAL.write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Rotación de búsquedas
# --------------------------------------------------------------------------- #
def todas_las_combinaciones(cfg: dict, hoy: date) -> list[tuple[str, str]]:
    desde = max(date.fromisoformat(cfg["fecha_desde"]), hoy + timedelta(days=1))
    hasta = date.fromisoformat(cfg["fecha_hasta"])
    fechas = []
    d = desde
    while d <= hasta:
        fechas.append(d.isoformat())
        d += timedelta(days=1)
    # Intercalar orígenes para que cada corrida cubra ambos
    return [(o, f) for f in fechas for o in cfg["origenes"]]


def combinaciones_de_hoy(cfg: dict, hoy: date) -> list[tuple[str, str]]:
    todas = todas_las_combinaciones(cfg, hoy)
    if not todas:
        return []
    if os.getenv("FORZAR_TODAS") == "1":
        return todas
    n = cfg["busquedas_por_corrida"]
    # Paso coprimo con el total para que cada corrida tome fechas dispersas
    # (no 8 días seguidos) y en ~total/n días se cubra todo.
    total = len(todas)
    grupos = max(1, -(-total // n))
    turno = hoy.toordinal() % grupos
    return [todas[i] for i in range(turno, total, grupos)][:n]


# --------------------------------------------------------------------------- #
# Consulta a SerpApi
# --------------------------------------------------------------------------- #
def buscar(origen: str, fecha: str, cfg: dict, api_key: str) -> dict:
    params = {
        "engine": "google_flights",
        "departure_id": origen,
        "arrival_id": cfg["destino"],
        "outbound_date": fecha,
        "type": 2,  # 2 = solo ida
        "adults": 1,
        "currency": cfg.get("moneda", "USD"),
        "hl": cfg.get("idioma", "es"),
        "api_key": api_key,
    }
    if cfg.get("max_escalas") is not None:
        # SerpApi: 0 = cualquiera, 1 = directo, 2 = <=1 escala, 3 = <=2 escalas
        params["stops"] = min(int(cfg["max_escalas"]) + 1, 3)
    r = requests.get(SERPAPI_URL, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"])
    return data


def mejor_vuelo(data: dict, cfg: dict) -> dict | None:
    excluidas = {a.lower() for a in cfg.get("aerolineas_excluidas") or []}
    candidatos = []
    for v in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        precio = v.get("price")
        if not isinstance(precio, (int, float)):
            continue
        aerolineas = sorted({t.get("airline", "?") for t in v.get("flights", [])})
        if excluidas & {a.lower() for a in aerolineas}:
            continue
        candidatos.append(
            {
                "precio": precio,
                "aerolineas": aerolineas,
                "escalas": len(v.get("layovers") or []),
                "duracion_min": v.get("total_duration"),
            }
        )
    return min(candidatos, key=lambda c: c["precio"]) if candidatos else None


# --------------------------------------------------------------------------- #
# Reglas de oferta
# --------------------------------------------------------------------------- #
def mediana_origen(historial: dict, origen: str) -> tuple[float | None, int]:
    precios = [o["precio"] for o in historial["observaciones"] if o["origen"] == origen]
    return (statistics.median(precios) if precios else None), len(precios)


def evaluar_oferta(precio: float, origen: str, insights: dict, historial: dict, cfg: dict) -> list[str]:
    reglas = cfg["reglas"]
    motivos = []

    tope = reglas.get("precio_maximo")
    if tope is not None and precio <= tope:
        motivos.append(f"≤ tu tope de {tope}")

    med, n = mediana_origen(historial, origen)
    pct = reglas.get("porcentaje_bajo_mediana")
    if pct and med and n >= reglas.get("min_observaciones_mediana", 15):
        limite = med * (1 - pct / 100)
        if precio <= limite:
            motivos.append(f"{(1 - precio / med) * 100:.0f} % bajo la mediana observada ({med:.0f}, n={n})")

    if reglas.get("usar_price_insights_google") and insights:
        rango = insights.get("typical_price_range") or []
        if insights.get("price_level") == "low" and len(rango) == 2 and precio < rango[0]:
            motivos.append(f"Google lo marca bajo (rango típico {rango[0]}–{rango[1]})")

    return motivos


def ya_avisado(historial: dict, clave: str, precio: float, cfg: dict) -> bool:
    previo = historial["alertas"].get(clave)
    if previo is None:
        return False
    return precio > previo - cfg.get("bajada_minima_para_reavisar", 20)


# --------------------------------------------------------------------------- #
# Notificación
# --------------------------------------------------------------------------- #
def notificar(texto: str) -> None:
    print(texto)
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        print(f"[telegram] error {r.status_code}: {r.text}", file=sys.stderr)


def armar_mensaje(origen: str, fecha: str, vuelo: dict, moneda: str, motivos: list[str], url: str | None) -> str:
    dur = vuelo.get("duracion_min")
    dur_txt = f"{dur // 60}h{dur % 60:02d}" if isinstance(dur, int) else "?"
    lineas = [
        f"✈️ <b>Oferta {origen} → BER</b> el {fecha}",
        f"<b>{moneda} {vuelo['precio']}</b> · {', '.join(vuelo['aerolineas'])}",
        f"{vuelo['escalas']} escala(s) · {dur_txt}",
        "Por qué: " + "; ".join(motivos),
        "⚠️ Verificá que la tarifa incluya valija despachada antes de comprar.",
    ]
    if url:
        lineas.append(f'<a href="{url}">Abrir en Google Flights</a>')
    return "\n".join(lineas)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        print("Falta SERPAPI_KEY", file=sys.stderr)
        return 1

    cfg = cargar_config()
    historial = cargar_historial()
    hoy = datetime.now(timezone.utc).date()
    combos = combinaciones_de_hoy(cfg, hoy)
    print(f"Consultando {len(combos)} combinaciones: {combos}")

    errores = 0
    for origen, fecha in combos:
        try:
            data = buscar(origen, fecha, cfg, api_key)
        except Exception as e:  # noqa: BLE001
            errores += 1
            print(f"[{origen} {fecha}] error: {e}", file=sys.stderr)
            continue

        vuelo = mejor_vuelo(data, cfg)
        if not vuelo:
            print(f"[{origen} {fecha}] sin resultados que cumplan filtros")
            continue

        insights = data.get("price_insights") or {}
        # Evaluar contra el historial ANTES de agregar esta observación
        motivos = evaluar_oferta(vuelo["precio"], origen, insights, historial, cfg)

        historial["observaciones"].append(
            {
                "fecha_consulta": hoy.isoformat(),
                "origen": origen,
                "fecha_vuelo": fecha,
                "precio": vuelo["precio"],
                "aerolineas": vuelo["aerolineas"],
                "price_level": insights.get("price_level"),
            }
        )
        print(f"[{origen} {fecha}] {vuelo['precio']} {vuelo['aerolineas']} -> {motivos or 'no es oferta'}")

        clave = f"{origen}|{fecha}"
        if motivos and not ya_avisado(historial, clave, vuelo["precio"], cfg):
            url = (data.get("search_metadata") or {}).get("google_flights_url")
            notificar(armar_mensaje(origen, fecha, vuelo, cfg.get("moneda", "USD"), motivos, url))
            historial["alertas"][clave] = vuelo["precio"]

    guardar_historial(historial)
    # Falla el job solo si TODO falló (ej. key inválida o cupo agotado)
    return 1 if combos and errores == len(combos) else 0


if __name__ == "__main__":
    sys.exit(main())
