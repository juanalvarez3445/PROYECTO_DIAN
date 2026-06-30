"""CLI del agente de facturas recibidas DIAN + consolidacion bancaria.

Comandos:
  extraer       login al portal DIAN, descarga XML/PDF de facturas recibidas y
                las estructura (XML directo; PDF via OpenAI).
  consolidar    carga un extracto bancario y genera el reporte Excel cruzando
                facturas contra movimientos.
  procesar-pdf  estructura un PDF suelto con OpenAI (utilidad/diagnostico).

Ejemplos:
  python main.py extraer --desde 2026-01-01 --hasta 2026-01-31 --headed
  python main.py consolidar --extracto data/extractos/mi_extracto.xlsx
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

# Forzar UTF-8 en la salida para evitar UnicodeEncodeError en la consola de Windows
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

import typer
from rich import print as rprint
from rich.table import Table

from config import DESCARGAS_DIR, settings
from src.ai.extractor import extraer_factura_de_pdf, extraer_pdfs_directorio
from src.banco.loader import cargar_extracto
from src.consolidacion.conciliador import conciliar
from src.dian.parser import parsear_directorio
from src.models import Factura
from src.reporte.excel import generar_reporte

app = typer.Typer(add_completion=False, help=__doc__)

# Donde se guardan las facturas estructuradas entre comandos
FACTURAS_JSON = DESCARGAS_DIR / "_facturas.json"


def _parse_fecha(valor: str) -> date:
    return datetime.strptime(valor, "%Y-%m-%d").date()


def _guardar_facturas(facturas: list[Factura]) -> None:
    datos = [f.model_dump(mode="json") for f in facturas]
    FACTURAS_JSON.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def _cargar_facturas() -> list[Factura]:
    if not FACTURAS_JSON.exists():
        return []
    datos = json.loads(FACTURAS_JSON.read_text(encoding="utf-8"))
    return [Factura.model_validate(d) for d in datos]


@app.command()
def extraer(
    desde: str = typer.Option(..., "--desde", help="Fecha inicial AAAA-MM-DD"),
    hasta: str = typer.Option(..., "--hasta", help="Fecha final AAAA-MM-DD"),
    headed: bool = typer.Option(True, "--headed/--headless", help="Navegador visible (recomendado para captcha/2FA)"),
    usar_openai_pdf: bool = typer.Option(True, "--openai-pdf/--no-openai-pdf", help="Procesar con OpenAI los PDF sin XML"),
    inspeccionar: bool = typer.Option(False, "--inspeccionar", help="Tras el login, volcar estructura de la pagina y detenerse (para ajustar selectores)"),
):
    """Descarga las facturas recibidas de la DIAN y las estructura."""
    faltantes = settings.validar_dian()
    if faltantes:
        rprint(f"[red]Faltan credenciales DIAN en .env:[/] {', '.join(faltantes)}")
        raise typer.Exit(1)

    f_desde, f_hasta = _parse_fecha(desde), _parse_fecha(hasta)

    # Import diferido del scraper (depende de Playwright)
    from src.dian.scraper import extraer_facturas

    rprint(f"[cyan]Extrayendo facturas recibidas del {f_desde} al {f_hasta}...[/]")
    archivos = extraer_facturas(settings, f_desde, f_hasta, headed=headed, inspeccionar=inspeccionar)
    if inspeccionar:
        rprint("[yellow]Modo inspeccion completado. Revisa la salida de arriba.[/]")
        raise typer.Exit(0)
    rprint(f"[green]Descargados {len(archivos)} archivos en {DESCARGAS_DIR}[/]")

    # Parsear XML (fuente primaria)
    facturas = parsear_directorio(DESCARGAS_DIR)
    rprint(f"[green]{len(facturas)} facturas parseadas desde XML.[/]")

    # PDF sin XML -> OpenAI
    if usar_openai_pdf and not settings.validar_openai():
        pdf_facturas = extraer_pdfs_directorio(DESCARGAS_DIR, settings, solo_sin_xml=True)
        if pdf_facturas:
            rprint(f"[green]{len(pdf_facturas)} facturas extraidas de PDF con OpenAI.[/]")
            facturas.extend(pdf_facturas)

    # Deduplicar por clave
    unicas: dict[str, Factura] = {}
    for f in facturas:
        unicas[f.clave()] = f
    facturas = list(unicas.values())

    _guardar_facturas(facturas)
    rprint(f"[bold green]Total: {len(facturas)} facturas estructuradas y guardadas.[/]")
    _tabla_facturas(facturas)


@app.command()
def informe(
    archivo: Path = typer.Option(..., "--archivo", exists=True, help="Ruta del 'Informe de facturas adquiridas' (.xlsx) descargado de MUISCA"),
):
    """Carga el informe de facturas recibidas (Excel de MUISCA) y lo deja listo para consolidar."""
    from src.dian.informe import cargar_informe_adquiridas

    rprint(f"[cyan]Leyendo informe DIAN: {archivo.name}[/]")
    facturas = cargar_informe_adquiridas(archivo)
    if not facturas:
        rprint("[yellow]No se encontraron facturas en el informe.[/]")
        raise typer.Exit(1)
    _guardar_facturas(facturas)
    total = sum(f.total for f in facturas)
    rprint(f"[bold green]{len(facturas)} facturas recibidas cargadas. Total: ${total:,.0f}[/]")
    _tabla_facturas(facturas)
    rprint("\n[dim]Siguiente paso:[/] python main.py consolidar --extracto <tu_extracto_bancario>")


@app.command("generar-ejemplo")
def generar_ejemplo(
    salida: Path = typer.Option(Path("data/extractos/extracto_ejemplo.xlsx"), "--salida", help="Ruta del extracto de ejemplo a crear"),
    n: int = typer.Option(30, "--n", help="Cuantas facturas incluir como movimientos conciliables"),
):
    """Genera un extracto bancario de EJEMPLO a partir de las facturas cargadas (para probar la conciliacion)."""
    from src.banco.ejemplo import generar_extracto_ejemplo

    facturas = _cargar_facturas()
    if not facturas:
        rprint("[yellow]No hay facturas cargadas. Ejecuta primero 'informe'.[/]")
        raise typer.Exit(1)
    ruta = generar_extracto_ejemplo(facturas, salida, n_conciliables=n)
    rprint(f"[bold green]Extracto de ejemplo creado:[/] {ruta}")
    rprint(f"[dim]Ahora corre:[/] python main.py consolidar --extracto \"{ruta}\"")


@app.command()
def consolidar(
    extracto: Path = typer.Option(..., "--extracto", exists=True, help="Archivo del extracto bancario (CSV/Excel/PDF)"),
    solo_debitos: bool = typer.Option(True, "--solo-debitos/--todos", help="Cruzar solo contra debitos (compras)"),
    motor: str = typer.Option("auto", "--motor", help="Motor de conciliacion: 'ia' (OpenAI), 'reglas' o 'auto'"),
):
    """Concilia las facturas contra un extracto bancario y genera el Excel.

    Motor 'ia' usa OpenAI para decidir cada cruce (entiende descripciones
    desordenadas). 'reglas' usa logica deterministica. 'auto' usa IA si hay
    OPENAI_API_KEY configurada, si no cae a reglas.
    """
    facturas = _cargar_facturas()
    if not facturas:
        rprint("[yellow]No hay facturas guardadas. Ejecuta primero 'informe' o 'extraer'.[/]")
        raise typer.Exit(1)

    rprint(f"[cyan]Cargando extracto bancario: {extracto.name}[/]")
    movimientos = cargar_extracto(extracto)
    rprint(f"[green]{len(movimientos)} movimientos cargados.[/]")

    # Elegir motor
    hay_openai = not settings.validar_openai()
    usar_ia = motor == "ia" or (motor == "auto" and hay_openai)
    if motor == "ia" and not hay_openai:
        rprint("[red]Motor 'ia' requiere OPENAI_API_KEY en .env.[/]")
        raise typer.Exit(1)

    if usar_ia:
        from src.ai.conciliador_ia import conciliar_con_ia

        rprint("[cyan]Conciliando con OpenAI (esto puede tardar segun el numero de facturas)...[/]")
        conciliacion = conciliar_con_ia(
            facturas,
            movimientos,
            settings,
            tolerancia_monto=settings.tolerancia_monto,
            ventana_dias=settings.ventana_dias,
            solo_debitos=solo_debitos,
        )
    else:
        rprint("[cyan]Conciliando con motor de reglas...[/]")
        conciliacion = conciliar(
            facturas,
            movimientos,
            tolerancia_monto=settings.tolerancia_monto,
            ventana_dias=settings.ventana_dias,
            solo_debitos=solo_debitos,
        )

    ruta = generar_reporte(facturas, conciliacion)
    rprint(
        f"[bold green]Reporte generado:[/] {ruta}\n"
        f"  Motor: {'OpenAI' if usar_ia else 'reglas'} | "
        f"Conciliadas: {conciliacion.conciliadas} | Sin match: {conciliacion.sin_match}"
    )


@app.command("procesar-pdf")
def procesar_pdf(archivo: Path = typer.Argument(..., exists=True, help="PDF de factura a estructurar")):
    """Estructura un PDF de factura con OpenAI e imprime el resultado (diagnostico)."""
    if settings.validar_openai():
        rprint("[red]Falta OPENAI_API_KEY en .env.[/]")
        raise typer.Exit(1)
    factura = extraer_factura_de_pdf(archivo, settings)
    if factura is None:
        rprint("[yellow]No se pudo extraer la factura del PDF.[/]")
        raise typer.Exit(1)
    rprint(factura.model_dump())


def _tabla_facturas(facturas: list[Factura]) -> None:
    if not facturas:
        return
    tabla = Table(title="Facturas recibidas")
    for col in ("Numero", "Proveedor", "NIT", "Fecha", "Total"):
        tabla.add_column(col)
    for f in facturas[:30]:
        tabla.add_row(
            str(f.numero_factura or ""),
            str(f.nombre_emisor or ""),
            str(f.nit_emisor or ""),
            str(f.fecha_emision or ""),
            f"{f.total:,.2f}",
        )
    rprint(tabla)
    if len(facturas) > 30:
        rprint(f"[dim]... y {len(facturas) - 30} mas[/]")


if __name__ == "__main__":
    app()
