
import os
import time
import requests

from pathlib import Path
from threading import Thread, Lock, current_thread
from dotenv import load_dotenv


# --------------------------------------------------
# CONFIGURACIÓN
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DURACION_HORAS = 4
INTERVALO_MINUTOS = 3

URL_WEB = (
    "https://www.iberdrola.es/movilidad-electrica/"
    "productos/puntos-de-recarga"
)

URL_IBERDROLA = (
    "https://www.iberdrola.es/o/webclipb/iberdrola/"
    "puntosrecargacontroller/getDatosPuntoRecarga"
)

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://www.iberdrola.es",
    "Referer": URL_WEB,
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}


# --------------------------------------------------
# CONTROL DE VIGILANCIAS ACTIVAS
# --------------------------------------------------

vigilancias_activas = {}
vigilancia_lock = Lock()


# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------

def enviar_telegram(mensaje):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Faltan las credenciales de Telegram."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    respuesta = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": mensaje
        },
        timeout=20
    )

    respuesta.raise_for_status()

    datos = respuesta.json()

    if not datos.get("ok"):
        raise RuntimeError(
            "Telegram no ha confirmado el envío."
        )


# --------------------------------------------------
# CONSULTAR UN CARGADOR
# --------------------------------------------------

def consultar_cargador(cupr_id):

    with requests.Session() as sesion:

        sesion.headers.update(HEADERS)

        pagina = sesion.get(
            URL_WEB,
            timeout=(10, 30)
        )

        pagina.raise_for_status()

        respuesta = sesion.post(
            URL_IBERDROLA,
            json={
                "dto": {
                    "cuprId": [cupr_id]
                },
                "language": "es"
            },
            timeout=(10, 30)
        )

        respuesta.raise_for_status()

        datos = respuesta.json()

        if datos.get("errorAjax") or datos.get("serviceException"):
            raise RuntimeError(
                "Iberdrola ha devuelto un error."
            )

        entidades = datos.get("entidad")

        if not isinstance(entidades, list):
            raise RuntimeError(
                "Respuesta de Iberdrola no válida."
            )

        for cargador in entidades:

            location = cargador.get("locationData") or {}

            identificador = (
                cargador.get("cuprId")
                or location.get("cuprId")
            )

            if identificador is not None and int(identificador) == cupr_id:
                return cargador

        return None


# --------------------------------------------------
# ANALIZAR DISPONIBILIDAD
# --------------------------------------------------

def hay_toma_libre(cargador):

    for logical in cargador.get("logicalSocket") or []:

        fisicas = logical.get("physicalSocket") or []

        if fisicas:

            for toma in fisicas:

                estado = (toma.get("status") or {}).get("statusCode")

                if estado == "AVAILABLE":
                    return True

        else:

            estado = (logical.get("status") or {}).get("statusCode")

            if estado == "AVAILABLE":
                return True

    # Si no hay estados individuales, consultar estado general
    estado_general = (
        cargador.get("cpStatus") or {}
    ).get("statusCode")

    if estado_general == "AVAILABLE":
        return True

    return False


# --------------------------------------------------
# VIGILAR CARGADOR
# --------------------------------------------------

def _vigilar(cupr_id, nombre, direccion, localidad):

    duracion = DURACION_HORAS * 60 * 60
    intervalo = INTERVALO_MINUTOS * 60

    inicio = time.monotonic()
    final = inicio + duracion

    try:

        print(
            f"[VIGILANCIA] Iniciada: {nombre} ({cupr_id})",
            flush=True
        )

        enviar_telegram(
            "🟢 Vigilancia Iberdrola iniciada.\n\n"
            f"{nombre}\n"
            f"{direccion}\n"
            f"{localidad}\n\n"
            f"Comprobación cada {INTERVALO_MINUTOS} minutos.\n"
            f"Duración máxima: {DURACION_HORAS} horas."
        )

        while time.monotonic() < final:

            try:

                print(
                    f"[VIGILANCIA] Consultando {nombre}...",
                    flush=True
                )

                cargador = consultar_cargador(cupr_id)

                if cargador is None:

                    print(
                        f"[VIGILANCIA] No aparece el cargador {cupr_id}.",
                        flush=True
                    )

                elif hay_toma_libre(cargador):

                    print(
                        f"[VIGILANCIA] ¡Hay una toma libre en {nombre}!",
                        flush=True
                    )

                    enviar_telegram(
                        "🔌 ¡YA HAY UNA TOMA LIBRE!\n\n"
                        f"{nombre}\n"
                        f"{direccion}\n"
                        f"{localidad}\n\n"
                        "¡Ya puedes acudir al cargador!"
                    )

                    return

                else:

                    print(
                        f"[VIGILANCIA] Todas las tomas siguen ocupadas.",
                        flush=True
                    )

            except Exception as error:

                print(
                    f"[VIGILANCIA] Error consultando Iberdrola: {repr(error)}",
                    flush=True
                )

            restante = final - time.monotonic()

            if restante > 0:
                time.sleep(min(intervalo, restante))

        enviar_telegram(
            "⏹ Vigilancia finalizada por tiempo.\n\n"
            f"{nombre}\n"
            "No se ha detectado ninguna toma libre."
        )

        print(
            f"[VIGILANCIA] Finalizada por tiempo: {nombre}",
            flush=True
        )

    except Exception as error:

        print(
            f"[VIGILANCIA] Error general: {repr(error)}",
            flush=True
        )

        try:
            enviar_telegram(
                f"⚠️ Error en la vigilancia de {nombre}."
            )
        except Exception:
            pass

    finally:

        with vigilancia_lock:

            if vigilancias_activas.get(cupr_id) is current_thread():
                vigilancias_activas.pop(cupr_id, None)


# --------------------------------------------------
# INICIAR VIGILANCIA
# --------------------------------------------------

def iniciar_vigilancia(cupr_id, nombre, direccion="", localidad=""):

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Faltan las credenciales de Telegram."
        )

    cupr_id = int(cupr_id)

    with vigilancia_lock:

        hilo_actual = vigilancias_activas.get(cupr_id)

        if hilo_actual and hilo_actual.is_alive():
            return False

        hilo = Thread(
            target=_vigilar,
            args=(cupr_id, nombre, direccion, localidad),
            daemon=True
        )

        vigilancias_activas[cupr_id] = hilo

        hilo.start()

    return True


# --------------------------------------------------
# CONSULTAR SI YA ESTÁ VIGILADO
# --------------------------------------------------

def esta_vigilado(cupr_id):

    with vigilancia_lock:

        hilo = vigilancias_activas.get(int(cupr_id))

        return hilo is not None and hilo.is_alive()
