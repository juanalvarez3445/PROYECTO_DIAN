"""Pruebas del motor de conciliacion con IA, usando un cliente OpenAI simulado."""
from datetime import date

import pandas as pd

from config import Settings
from src.ai.conciliador_ia import DecisionMatch, conciliar_con_ia
from src.models import Factura


# --- Cliente OpenAI falso: devuelve decisiones en orden ---
class _Message:
    def __init__(self, parsed):
        self.parsed = parsed


class _Choice:
    def __init__(self, parsed):
        self.message = _Message(parsed)


class _Completion:
    def __init__(self, parsed):
        self.choices = [_Choice(parsed)]


class _Completions:
    def __init__(self, decisiones):
        self._dec = list(decisiones)
        self._i = 0
        self.llamadas = 0

    def parse(self, **kwargs):
        self.llamadas += 1
        d = self._dec[self._i]
        self._i += 1
        return _Completion(d)


class FakeClient:
    def __init__(self, decisiones):
        beta = type("B", (), {})()
        chat = type("C", (), {})()
        chat.completions = _Completions(decisiones)
        beta.chat = chat
        self.beta = beta


def _settings():
    return Settings(
        dian_tipo_doc_empresa="NIT", dian_doc_empresa="900", dian_tipo_doc_usuario="CC",
        dian_doc_usuario="1", dian_password="x", dian_url="http://x",
        openai_api_key="sk-test", openai_model="gpt-4o-mini",
        tolerancia_monto=100, ventana_dias=5,
    )


def _movs():
    return pd.DataFrame([
        {"fecha": date(2024, 6, 6), "descripcion": "COMPRA RAPPI SAS BOGOTA PSE", "valor": 3980.0, "tipo": "debito", "referencia": ""},
        {"fecha": date(2024, 2, 5), "descripcion": "COMPRA SODIMAC HOMECENTER", "valor": 564900.0, "tipo": "debito", "referencia": ""},
    ])


def _factura(nombre, total, f, num="X"):
    return Factura(numero_factura=num, nit_emisor="900", nombre_emisor=nombre, fecha_emision=f, total=total)


def test_ia_concilia_dos_facturas():
    facturas = [
        _factura("RAPPI S.A.S", 3980.0, date(2024, 6, 6), "R1"),
        _factura("SODIMAC COLOMBIA S.A.", 564900.0, date(2024, 2, 5), "S1"),
    ]
    cliente = FakeClient([
        DecisionMatch(indice_movimiento=0, confianza=0.95, motivo="RAPPI"),
        DecisionMatch(indice_movimiento=1, confianza=0.92, motivo="SODIMAC"),
    ])
    res = conciliar_con_ia(facturas, _movs(), _settings(), cliente=cliente)
    assert res.conciliadas == 2
    assert res.sin_match == 0
    # Solo 2 llamadas a la IA (una por factura con candidatos)
    assert cliente.beta.chat.completions.llamadas == 2


def test_ia_sin_candidatos_no_llama():
    # Factura cuyo monto no coincide con ningun movimiento -> sin_match sin llamar a la IA
    facturas = [_factura("OTRO PROVEEDOR", 12345.0, date(2024, 3, 1))]
    cliente = FakeClient([])  # no deberia usarse
    res = conciliar_con_ia(facturas, _movs(), _settings(), cliente=cliente)
    assert res.sin_match == 1
    assert cliente.beta.chat.completions.llamadas == 0


def test_ia_rechaza_por_baja_confianza():
    facturas = [_factura("RAPPI S.A.S", 3980.0, date(2024, 6, 6))]
    cliente = FakeClient([DecisionMatch(indice_movimiento=0, confianza=0.2, motivo="dudoso")])
    res = conciliar_con_ia(facturas, _movs(), _settings(), umbral_confianza=0.5, cliente=cliente)
    assert res.sin_match == 1


def test_ia_no_reutiliza_movimiento():
    # Dos facturas identicas compiten por el mismo unico movimiento
    facturas = [
        _factura("RAPPI S.A.S", 3980.0, date(2024, 6, 6), "R1"),
        _factura("RAPPI S.A.S", 3980.0, date(2024, 6, 6), "R2"),
    ]
    cliente = FakeClient([
        DecisionMatch(indice_movimiento=0, confianza=0.95, motivo="primera"),
        DecisionMatch(indice_movimiento=0, confianza=0.95, motivo="segunda"),  # ya usado
    ])
    res = conciliar_con_ia(facturas, _movs(), _settings(), cliente=cliente)
    assert res.conciliadas == 1
    assert res.sin_match == 1
