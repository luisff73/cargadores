
import os
import re
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

WAHA_URL = os.getenv("WAHA_URL", "").rstrip("/")
WAHA_API_KEY = os.getenv("WAHA_API_KEY")
WAHA_SESSION = os.getenv("WAHA_SESSION", "default")

DURACION_HORAS = 4
INTERVALO_MINUTOS = 3

# Evitan que la web se use para enviar mensajes masivos desde el número.
MAX_VIGILANCIAS_TOTALES = 30
MAX_VIGILANCIAS_POR_TELEFONO = 3
MAX_ACTIVACIONES_POR_IP = 10
VENTANA_IP_SEGUNDOS = 60 * 60

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
activaciones_por_ip = {}
vigilancia_lock = Lock()


class ErrorVigilancia(Exception):
    """Error con un mensaje apto para mostrar al usuario."""

    def __init__(self, mensaje, estado):
        super().__init__(mensaje)
        self.estado = estado


# --------------------------------------------------
# WHATSAPP (WAHA)
# --------------------------------------------------

def normalizar_telefono(texto):
    """Devuelve el número internacional solo con dígitos, sin el +."""
    limpio = re.sub(r"[\s().-]", "", texto or "")

    if limpio.startswith("+"):
        digitos = limpio[1:]
    elif limpio.startswith("00"):
        digitos = limpio[2:]
    elif re.fullmatch(r"[67]\d{8}", limpio):
        digitos = "34" + limpio
    elif re.fullmatch(r"34[67]\d{8}", limpio):
        digitos = limpio
    else:
        digitos = ""

    if not re.fullmatch(r"[1-9]\d{7,14}", digitos):
        raise ErrorVigilancia("El número de WhatsApp no es válido.", 400)

    return digitos


def _cabeceras_waha():
    return {"X-Api-Key": WAHA_API_KEY}


def numero_tiene_whatsapp(telefono):

    respuesta = requests.get(
        f"{WAHA_URL}/api/contacts/check-exists",
        params={"phone": telefono, "session": WAHA_SESSION},
        headers=_cabeceras_waha(),
        timeout=20
    )

    respuesta.raise_for_status()

    return respuesta.json().get("numberExists") is True


def enviar_whatsapp(telefono, mensaje):

    respuesta = requests.post(
        f"{WAHA_URL}/api/sendText",
        json={
            "session": WAHA_SESSION,
            "chatId": f"{telefono}@c.us",
            "text": mensaje
        },
        headers=_cabeceras_waha(),
        timeout=20
    )

    respuesta.raise_for_status()


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


def comprobar_iberdrola(cupr_id):

    cargador = consultar_cargador(cupr_id)

    if cargador is None:

        print(
            f"[VIGILANCIA] No aparece el cargador {cupr_id}.",
            flush=True
        )

        return False

    return hay_toma_libre(cargador)


# --------------------------------------------------
# VIGILAR CARGADOR
# --------------------------------------------------

def _vigilar(clave, comprobar, operador, nombre, direccion, localidad, telefono):

    duracion = DURACION_HORAS * 60 * 60
    intervalo = INTERVALO_MINUTOS * 60

    inicio = time.monotonic()
    final = inicio + duracion

    try:

        print(
            f"[VIGILANCIA] Iniciada: {nombre}",
            flush=True
        )

        while time.monotonic() < final:

            try:

                print(
                    f"[VIGILANCIA] Consultando {nombre}...",
                    flush=True
                )

                if comprobar():

                    print(
                        f"[VIGILANCIA] ¡Hay una toma libre en {nombre}!",
                        flush=True
                    )

                    enviar_whatsapp(
                        telefono,
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
                    f"[VIGILANCIA] Error consultando {operador}: {repr(error)}",
                    flush=True
                )

            restante = final - time.monotonic()

            if restante > 0:
                time.sleep(min(intervalo, restante))

        enviar_whatsapp(
            telefono,
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
            enviar_whatsapp(
                telefono,
                f"⚠️ Error en la vigilancia de {nombre}."
            )
        except Exception:
            pass

    finally:

        with vigilancia_lock:

            if vigilancias_activas.get(clave) is current_thread():
                vigilancias_activas.pop(clave, None)


# --------------------------------------------------
# INICIAR VIGILANCIA
# --------------------------------------------------

def _registrar_activacion(clave, telefono, ip):
    """Comprueba los límites. Debe llamarse con vigilancia_lock adquirido."""
    if len(vigilancias_activas) >= MAX_VIGILANCIAS_TOTALES:
        raise ErrorVigilancia(
            "Hay demasiadas vigilancias activas. Inténtalo más tarde.",
            429
        )

    del_telefono = sum(1 for c in vigilancias_activas if c[1] == telefono)

    if del_telefono >= MAX_VIGILANCIAS_POR_TELEFONO:
        raise ErrorVigilancia(
            "Este número ya tiene el máximo de vigilancias activas.",
            429
        )

    ahora = time.monotonic()
    historial = [
        momento for momento in activaciones_por_ip.get(ip, [])
        if ahora - momento < VENTANA_IP_SEGUNDOS
    ]

    if len(historial) >= MAX_ACTIVACIONES_POR_IP:
        activaciones_por_ip[ip] = historial
        raise ErrorVigilancia(
            "Has activado demasiadas vigilancias. Inténtalo más tarde.",
            429
        )

    historial.append(ahora)
    activaciones_por_ip[ip] = historial


def _lanzar_vigilancia(
    id_cargador, comprobar, operador, nombre, direccion, localidad,
    telefono, ip
):

    if not WAHA_URL or not WAHA_API_KEY:
        print("[VIGILANCIA] Falta WAHA_URL o WAHA_API_KEY.", flush=True)
        raise ErrorVigilancia("No se ha podido iniciar la vigilancia.", 503)

    telefono = normalizar_telefono(telefono)
    clave = (id_cargador, telefono)

    hilo = Thread(
        target=_vigilar,
        args=(clave, comprobar, operador, nombre, direccion, localidad, telefono),
        daemon=True
    )

    with vigilancia_lock:

        if clave in vigilancias_activas:
            return False

        _registrar_activacion(clave, telefono, ip)

        # Reserva el hueco mientras se envía el primer mensaje.
        vigilancias_activas[clave] = hilo

    # El primer envío se hace aquí para poder informar de un fallo al usuario.
    try:

        if not numero_tiene_whatsapp(telefono):
            raise ErrorVigilancia("Ese número no tiene WhatsApp.", 400)

        enviar_whatsapp(
            telefono,
            f"🟢 Vigilancia {operador} iniciada.\n\n"
            f"{nombre}\n"
            f"{direccion}\n"
            f"{localidad}\n\n"
            f"Comprobación cada {INTERVALO_MINUTOS} minutos.\n"
            f"Duración máxima: {DURACION_HORAS} horas."
        )

    except Exception as error:

        with vigilancia_lock:
            vigilancias_activas.pop(clave, None)

        if isinstance(error, ErrorVigilancia):
            raise

        print(
            f"[VIGILANCIA] Error enviando WhatsApp: {repr(error)}",
            flush=True
        )

        raise ErrorVigilancia(
            "No se ha podido enviar el WhatsApp. Inténtalo más tarde.",
            503
        )

    hilo.start()

    return True


def iniciar_vigilancia(cupr_id, telefono, ip, nombre, direccion="", localidad=""):

    cupr_id = int(cupr_id)

    return _lanzar_vigilancia(
        f"iberdrola:{cupr_id}",
        lambda: comprobar_iberdrola(cupr_id),
        "Iberdrola",
        nombre,
        direccion,
        localidad,
        telefono,
        ip
    )


def iniciar_vigilancia_fenie(codigo, comprobar, telefono, ip, nombre, direccion=""):

    return _lanzar_vigilancia(
        f"fenie:{codigo}",
        comprobar,
        "Feníe",
        nombre,
        direccion,
        "",
        telefono,
        ip
    )
