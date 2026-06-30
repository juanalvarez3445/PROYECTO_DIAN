"""Automatizacion del portal de facturacion electronica de la DIAN (Playwright).

La DIAN no expone una API publica para consultar facturas recibidas, por lo que
se automatiza el navegador.

PROTECCIONES ANTI-BOT DEL PORTAL (verificadas en vivo)
------------------------------------------------------
El portal (https://catalogo-vpfe.dian.gov.co/User/Login) esta protegido por:
  - Cloudflare Turnstile ("Verifique que es un ser humano") en el login.
  - Azure WAF (desafio JS) en el login de Empresa (/User/CompanyLogin).

Por eso un login 100% automatico NO es viable ni recomendable. El modelo usado
es LOGIN ASISTIDO con sesion persistente:

  1. Se abre un navegador VISIBLE con un perfil persistente (.perfil_navegador),
     que conserva cookies entre corridas para no re-loguear cada vez.
  2. El USUARIO inicia sesion manualmente (entra como Empresa, pasa el captcha /
     WAF y cualquier 2FA).
  3. El script detecta que el login termino (ya no estamos en la pagina de
     Login) y TOMA EL CONTROL para navegar a "Documentos Recibidos", filtrar por
     fecha y descargar los XML/PDF.

Los selectores de NAVEGACION/DESCARGA (post-login) estan centralizados en
`Selectores` y se ajustan tras inspeccionar la zona privada del portal con el
modo de inspeccion (`extraer ... --inspeccionar`).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from config import DESCARGAS_DIR, PERFIL_DIR, Settings

URL_LOGIN_BASE = "/User/Login"
PUERTO_CDP = 9222

# Rutas habituales de Chrome en Windows
RUTAS_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def _buscar_chrome() -> Optional[str]:
    for ruta in RUTAS_CHROME:
        if ruta and os.path.exists(ruta):
            return ruta
    return None


def _cdp_listo(puerto: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/json/version", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


@dataclass
class Selectores:
    """Selectores post-login. Ajustar tras inspeccionar la zona privada."""

    # Navegacion a documentos recibidos (candidatos; refinar en vivo)
    menu_recibidos: str = (
        "a:has-text('Recibidos'), a:has-text('Documentos recibidos'), "
        "text=Recibidos"
    )
    # Filtros de fecha
    input_fecha_desde: str = "input[name*='startDate'], input[id*='desde'], #FechaInicial"
    input_fecha_hasta: str = "input[name*='endDate'], input[id*='hasta'], #FechaFinal"
    boton_buscar: str = "button:has-text('Buscar'), input[value='Buscar']"
    # Resultados / descargas
    filas_resultado: str = "table tbody tr"
    boton_descargar_xml: str = "a:has-text('XML'), button:has-text('XML')"
    boton_descargar_pdf: str = "a:has-text('PDF'), button:has-text('PDF')"
    boton_siguiente_pagina: str = "a[rel='next'], button:has-text('Siguiente'), a:has-text('>')"


class DianScraper:
    """Sesion de Playwright contra el portal DIAN con login asistido."""

    def __init__(
        self,
        settings: Settings,
        headed: bool = True,
        descargas_dir: Path = DESCARGAS_DIR,
        selectores: Optional[Selectores] = None,
    ) -> None:
        self.settings = settings
        self.headed = headed
        self.descargas_dir = descargas_dir
        self.sel = selectores or Selectores()
        self._descargas: list[Path] = []

    def __enter__(self) -> "DianScraper":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        PERFIL_DIR.mkdir(parents=True, exist_ok=True)
        self._chrome_proc: Optional[subprocess.Popen] = None

        chrome = _buscar_chrome()
        if chrome and self.headed:
            # MEJOR OPCION: lanzar el Chrome REAL del usuario con depuracion
            # remota y conectarse por CDP. Asi es un navegador 100% genuino, que
            # es lo que permite pasar Cloudflare Turnstile y el Azure WAF.
            self._lanzar_chrome_cdp(chrome)
        else:
            # Respaldo: Chromium empaquetado (puede fallar el captcha).
            if not chrome:
                print("[scraper] No se encontro Chrome instalado; usando Chromium (el captcha puede fallar).")
            self._lanzar_chromium()

        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()
        try:
            self._context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
        except Exception:
            pass
        return self

    def _lanzar_chrome_cdp(self, chrome: str) -> None:
        self._chrome_proc = subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={PUERTO_CDP}",
                f"--user-data-dir={PERFIL_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Esperar a que el endpoint CDP este listo (hasta ~20s)
        for _ in range(40):
            if _cdp_listo(PUERTO_CDP):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Chrome no expuso el puerto de depuracion a tiempo.")
        self._browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PUERTO_CDP}")
        self._context = self._browser.contexts[0]
        self._modo = "cdp"

    def _lanzar_chromium(self) -> None:
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PERFIL_DIR),
            headless=not self.headed,
            accept_downloads=True,
            user_agent=UA,
            locale="es-CO",
            viewport={"width": 1366, "height": 768},
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
            ignore_default_args=["--enable-automation"],
        )
        self._modo = "chromium"

    def __exit__(self, *exc) -> None:
        try:
            if getattr(self, "_modo", "") == "cdp":
                # No cerramos el contexto (es el Chrome real); cerramos conexion y proceso.
                try:
                    self._browser.close()
                except Exception:
                    pass
                if self._chrome_proc:
                    self._chrome_proc.terminate()
            else:
                self._context.close()
        finally:
            self._pw.stop()

    # ------------------------------------------------------------------ login
    def _en_login(self) -> bool:
        """True si NO estamos autenticados.

        Las paginas publicas (Login, CompanyLogin, SearchDocument, etc.) viven
        bajo /User/ y muestran las pestanas de acceso (Empresa/Persona/...).
        Mientras esas pestanas existan o sigamos bajo /User/, no hay sesion.
        """
        url = (self.page.url or "").lower()
        if "/user/" in url or "azure" in (self.page.title() or "").lower():
            return True
        try:
            return self.page.locator("a[href='/User/CompanyLogin']").count() > 0
        except Exception:
            return False

    def login(self, timeout_seg: int = 240) -> None:
        """Login ASISTIDO: el usuario entra a mano; el script espera y retoma.

        Reutiliza la sesion persistente si sigue valida. Si no, abre el login y
        espera (hasta `timeout_seg`) a que el usuario complete el ingreso.
        """
        self.page.goto(
            self.settings.dian_url + URL_LOGIN_BASE, wait_until="domcontentloaded", timeout=60_000
        )
        self.page.wait_for_timeout(4000)

        if not self._en_login():
            print("[scraper] Sesion reutilizada: ya estabas autenticado.")
            return

        if not self.headed:
            raise RuntimeError(
                "No hay sesion valida y el modo headless no permite login asistido. "
                "Ejecuta con --headed para iniciar sesion manualmente."
            )

        print("\n" + "=" * 64)
        print("  INICIA SESION MANUALMENTE EN LA VENTANA DEL NAVEGADOR")
        print("  1. Haz clic en la pestana 'Empresa'.")
        print("  2. Ingresa tus datos y resuelve el captcha / desafio.")
        print("  3. Cuando estes DENTRO del portal, el robot continua solo.")
        print(f"  (Tienes hasta {timeout_seg} segundos)")
        print("=" * 64 + "\n")

        # Esperar a que el usuario salga de la pagina de login
        limite = time.time() + timeout_seg
        while time.time() < limite:
            if not self._en_login():
                self.page.wait_for_timeout(2000)
                print("[scraper] Login detectado. Continuando automaticamente...")
                return
            self.page.wait_for_timeout(1500)

        raise TimeoutError(
            "No se detecto el ingreso a tiempo. Vuelve a ejecutar e inicia sesion mas rapido."
        )

    # ------------------------------------------------- inspeccion (diagnostico)
    def inspeccionar(self, etiqueta: str = "post-login") -> None:
        """Vuelca estructura y screenshot de la pagina actual (para ajustar selectores)."""
        print(f"\n===== INSPECCION [{etiqueta}] -> {self.page.url} =====")
        print("TITULO:", repr(self.page.title()))
        js = """
        () => Array.from(document.querySelectorAll('a,button,input,select')).map(el => ({
          tag: el.tagName.toLowerCase(), type: el.getAttribute('type')||'',
          id: el.id||'', name: el.getAttribute('name')||'',
          href: (el.getAttribute('href')||'').slice(0,70),
          text: (el.innerText||el.value||'').trim().slice(0,45)
        })).filter(c => c.id || c.name || c.text || c.href)
        """
        for c in self.page.evaluate(js):
            print(c)
        destino = self.descargas_dir / f"_inspeccion_{etiqueta}.png"
        self.page.screenshot(path=str(destino), full_page=True)
        (self.descargas_dir / f"_inspeccion_{etiqueta}.html").write_text(
            self.page.content(), encoding="utf-8"
        )
        print(f"[scraper] Screenshot y HTML guardados junto a {destino}")

    # ----------------------------------------------------------- navegacion
    def ir_a_recibidos(self) -> None:
        self._click_si_existe(self.sel.menu_recibidos)
        self.page.wait_for_load_state("networkidle")

    def filtrar_por_fechas(self, desde: date, hasta: date) -> None:
        self._fill_si_existe(self.sel.input_fecha_desde, desde.strftime("%Y-%m-%d"))
        self._fill_si_existe(self.sel.input_fecha_hasta, hasta.strftime("%Y-%m-%d"))
        self._click_si_existe(self.sel.boton_buscar)
        self.page.wait_for_load_state("networkidle")

    def descargar_documentos(self, max_paginas: int = 50) -> list[Path]:
        pagina = 0
        while pagina < max_paginas:
            pagina += 1
            filas = self.page.query_selector_all(self.sel.filas_resultado)
            print(f"[scraper] Pagina {pagina}: {len(filas)} documentos.")
            for fila in filas:
                self._descargar_de_fila(fila)
            siguiente = self.page.query_selector(self.sel.boton_siguiente_pagina)
            if not siguiente or not siguiente.is_enabled():
                break
            siguiente.click()
            self.page.wait_for_load_state("networkidle")
        return self._descargas

    def _descargar_de_fila(self, fila) -> None:
        for selector in (self.sel.boton_descargar_xml, self.sel.boton_descargar_pdf):
            try:
                boton = fila.query_selector(selector)
                if not boton:
                    continue
                with self.page.expect_download(timeout=30_000) as info:
                    boton.click()
                descarga = info.value
                destino = self.descargas_dir / descarga.suggested_filename
                descarga.save_as(str(destino))
                self._descargas.append(destino)
            except Exception as exc:  # noqa: BLE001
                print(f"[scraper] No se pudo descargar ({selector}): {exc}")

    # --------------------------------------------------- helpers tolerantes
    def _click_si_existe(self, selector: str, timeout: int = 8000) -> None:
        try:
            self.page.locator(selector).first.click(timeout=timeout)
        except Exception:
            pass

    def _fill_si_existe(self, selector: str, valor: str, timeout: int = 8000) -> None:
        if not valor:
            return
        try:
            self.page.locator(selector).first.fill(valor, timeout=timeout)
        except Exception:
            pass


def extraer_facturas(
    settings: Settings,
    desde: date,
    hasta: date,
    headed: bool = True,
    inspeccionar: bool = False,
) -> list[Path]:
    """login asistido -> recibidos -> filtrar -> descargar.

    Si `inspeccionar=True`, tras el login vuelca la estructura de la pagina y se
    detiene (sirve para ajustar los selectores la primera vez).
    """
    with DianScraper(settings, headed=headed) as scraper:
        scraper.login()
        if inspeccionar:
            scraper.inspeccionar("post-login")
            print("[scraper] Modo inspeccion: revisa los selectores y vuelve a correr sin --inspeccionar.")
            return []
        scraper.ir_a_recibidos()
        if inspeccionar:
            scraper.inspeccionar("recibidos")
        scraper.filtrar_por_fechas(desde, hasta)
        return scraper.descargar_documentos()
