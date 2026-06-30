"""Carga centralizada de configuracion y rutas del proyecto.

Lee variables de entorno desde un archivo .env (ver .env.example) y expone
constantes y un objeto `settings` usado por el resto de modulos.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Raiz del proyecto = carpeta que contiene este archivo
BASE_DIR = Path(__file__).resolve().parent

# Carga .env si existe (no falla si no esta presente)
load_dotenv(BASE_DIR / ".env")

# --- Rutas de datos ---
DATA_DIR = BASE_DIR / "data"
DESCARGAS_DIR = DATA_DIR / "descargas"   # XML/PDF bajados de la DIAN
EXTRACTOS_DIR = DATA_DIR / "extractos"   # extractos bancarios de entrada
SALIDA_DIR = DATA_DIR / "salida"         # reportes Excel generados
SESSION_FILE = BASE_DIR / "storage_state.json"  # sesion de Playwright reutilizable
PERFIL_DIR = BASE_DIR / ".perfil_navegador"     # perfil persistente del navegador (cookies/cache)


def _crear_directorios() -> None:
    """Crea los directorios de datos si no existen."""
    for d in (DESCARGAS_DIR, EXTRACTOS_DIR, SALIDA_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _get(nombre: str, default: str | None = None) -> str | None:
    valor = os.getenv(nombre, default)
    return valor.strip() if isinstance(valor, str) else valor


@dataclass(frozen=True)
class Settings:
    # DIAN
    dian_tipo_doc_empresa: str
    dian_doc_empresa: str
    dian_tipo_doc_usuario: str
    dian_doc_usuario: str
    dian_password: str
    dian_url: str
    # OpenAI
    openai_api_key: str | None
    openai_model: str
    # Conciliacion
    tolerancia_monto: float
    ventana_dias: int

    def validar_dian(self) -> list[str]:
        """Devuelve la lista de campos DIAN faltantes (vacia si esta completo)."""
        faltantes = []
        if not self.dian_doc_empresa:
            faltantes.append("DIAN_DOC_EMPRESA")
        if not self.dian_doc_usuario:
            faltantes.append("DIAN_DOC_USUARIO")
        if not self.dian_password:
            faltantes.append("DIAN_PASSWORD")
        return faltantes

    def validar_openai(self) -> list[str]:
        return [] if self.openai_api_key else ["OPENAI_API_KEY"]


def cargar_settings() -> Settings:
    _crear_directorios()
    return Settings(
        dian_tipo_doc_empresa=_get("DIAN_TIPO_DOC_EMPRESA", "NIT") or "NIT",
        dian_doc_empresa=_get("DIAN_DOC_EMPRESA", "") or "",
        dian_tipo_doc_usuario=_get("DIAN_TIPO_DOC_USUARIO", "CC") or "CC",
        dian_doc_usuario=_get("DIAN_DOC_USUARIO", "") or "",
        dian_password=_get("DIAN_PASSWORD", "") or "",
        dian_url=_get("DIAN_URL", "https://catalogo-vpfe.dian.gov.co")
        or "https://catalogo-vpfe.dian.gov.co",
        openai_api_key=_get("OPENAI_API_KEY"),
        openai_model=_get("OPENAI_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
        tolerancia_monto=float(_get("CONCILIACION_TOLERANCIA_MONTO", "100") or "100"),
        ventana_dias=int(_get("CONCILIACION_VENTANA_DIAS", "5") or "5"),
    )


# Instancia global usada por los modulos
settings = cargar_settings()
