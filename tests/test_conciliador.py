"""Pruebas del conciliador bancario."""
from datetime import date

import pandas as pd

from src.consolidacion.conciliador import conciliar
from src.models import Factura


def _movimientos():
    return pd.DataFrame(
        [
            {"fecha": date(2026, 1, 18), "descripcion": "PAGO PROVEEDOR EJEMPLO SAS", "valor": 1190000.00, "tipo": "debito", "referencia": "TX1"},
            {"fecha": date(2026, 1, 20), "descripcion": "NOMINA", "valor": 5000000.00, "tipo": "debito", "referencia": "TX2"},
            {"fecha": date(2026, 1, 10), "descripcion": "INGRESO VENTAS", "valor": 1190000.00, "tipo": "credito", "referencia": "TX3"},
        ]
    )


def _factura(total=1190000.00, nit="901555444", nombre="Proveedor Ejemplo S.A.S.", f=date(2026, 1, 15)):
    return Factura(numero_factura="FE-9001", nit_emisor=nit, nombre_emisor=nombre, fecha_emision=f, total=total)


def test_concilia_por_monto_fecha_e_identidad():
    res = conciliar([_factura()], _movimientos(), tolerancia_monto=100, ventana_dias=5)
    assert res.conciliadas == 1
    r = res.resultados[0]
    assert r.estado == "conciliado"
    assert r.valor_movimiento == 1190000.00
    # debe elegir el debito al proveedor, no el credito de ventas
    assert "PROVEEDOR" in r.descripcion_movimiento.upper()


def test_sin_match_si_monto_lejano():
    res = conciliar([_factura(total=999.0)], _movimientos(), tolerancia_monto=100, ventana_dias=5)
    assert res.sin_match == 1
    assert res.resultados[0].estado == "sin_match"


def test_fuera_de_ventana_de_fecha():
    res = conciliar([_factura(f=date(2026, 3, 1))], _movimientos(), tolerancia_monto=100, ventana_dias=5)
    assert res.sin_match == 1


def test_no_reutiliza_movimiento():
    facturas = [_factura(), _factura()]
    res = conciliar(facturas, _movimientos(), tolerancia_monto=100, ventana_dias=5)
    # solo hay un debito que coincide; la segunda factura queda sin match
    assert res.conciliadas == 1
    assert res.sin_match == 1
