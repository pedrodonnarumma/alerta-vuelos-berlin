import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import alerta  # noqa: E402

CFG = alerta.cargar_config(Path(__file__).resolve().parents[1] / "config.yaml")

RESPUESTA = {
    "best_flights": [
        {"price": 709, "flights": [{"airline": "LATAM"}, {"airline": "Lufthansa"}], "layovers": [{}], "total_duration": 1020},
    ],
    "other_flights": [
        {"price": 585, "flights": [{"airline": "Iberia"}], "layovers": [{}, {}], "total_duration": 1200},
        {"flights": [{"airline": "X"}]},  # sin precio -> se ignora
    ],
    "price_insights": {"lowest_price": 585, "price_level": "low", "typical_price_range": [690, 820]},
    "search_metadata": {"google_flights_url": "https://www.google.com/travel/flights?x"},
}


def test_rotacion_cubre_todo_sin_repetir():
    hoy = date(2026, 11, 2)
    todas = alerta.todas_las_combinaciones(CFG, hoy)
    assert len(todas) == 61 * 2
    vistas = set()
    for i in range(40):
        vistas.update(alerta.combinaciones_de_hoy(CFG, date.fromordinal(hoy.toordinal() + i)))
    assert vistas == set(todas)
    assert len(alerta.combinaciones_de_hoy(CFG, hoy)) <= CFG["busquedas_por_corrida"]


def test_mejor_vuelo_y_exclusion():
    assert alerta.mejor_vuelo(RESPUESTA, CFG)["precio"] == 585
    cfg2 = {**CFG, "aerolineas_excluidas": ["iberia"]}
    assert alerta.mejor_vuelo(RESPUESTA, cfg2)["precio"] == 709


def test_reglas():
    h = {"observaciones": [], "alertas": {}}
    m = alerta.evaluar_oferta(585, "EZE", RESPUESTA["price_insights"], h, CFG)
    assert len(m) == 2  # tope + google
    assert alerta.evaluar_oferta(691, "EZE", {"price_level": "typical"}, h, CFG) == []
    # mediana: 20 obs a 709 -> 15 % abajo = 602.65
    h["observaciones"] = [{"origen": "SCL", "precio": 709}] * 20
    cfg_sin_tope = {**CFG, "reglas": {**CFG["reglas"], "precio_maximo": None}}
    assert alerta.evaluar_oferta(600, "SCL", {}, h, cfg_sin_tope)
    assert not alerta.evaluar_oferta(610, "SCL", {}, h, cfg_sin_tope)


def test_no_repite_alerta():
    h = {"observaciones": [], "alertas": {"EZE|2027-04-10": 590}}
    assert alerta.ya_avisado(h, "EZE|2027-04-10", 585, CFG)
    assert not alerta.ya_avisado(h, "EZE|2027-04-10", 560, CFG)


def test_main_end_to_end(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SERPAPI_KEY", "fake")
    monkeypatch.setattr(alerta, "HISTORIAL", tmp_path / "h.json")
    monkeypatch.setattr(alerta, "buscar", lambda *a, **k: RESPUESTA)
    assert alerta.main() == 0
    out = capsys.readouterr().out
    assert "Oferta" in out and "585" in out
    h = alerta.cargar_historial()
    assert len(h["observaciones"]) == CFG["busquedas_por_corrida"]
    assert len(h["alertas"]) == CFG["busquedas_por_corrida"]
