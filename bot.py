
import os
import json
import time
import requests
from dotenv import load_dotenv

# --------------------------------------------------
# CONFIGURACION
# --------------------------------------------------

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

URL_IBERDROLA = (
    "https://www.iberdrola.es/o/webclipb/iberdrola/"
    "puntosrecargacontroller/getDatosPuntoRecarga"
)

ARCHIVO_SELECCION = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "seleccion.json"
)

DURACION_HORAS = 4
INTERVALO_MINUTOS = 5

URL_WEB = (
    "https://www.iberdrola.es/movilidad-electrica/"
    "productos/puntos-de-recarga"
)

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Origin": "https://www.iberdrola.es",
    "Referer": URL_WEB,
    "X-Requested-With": "XMLHttpRequest"
}

SESSION = requests.Session()

SESSION.headers.update(HEADERS)
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/156.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
})


# --------------------------------------------------
# LEER SELECCION.JSON
# --------------------------------------------------

def cargar_seleccion():

    if not os.path.exists(ARCHIVO_SELECCION):
        raise FileNotFoundError(
            "No se encuentra el archivo seleccion.json."
        )

    with open(
        ARCHIVO_SELECCION,
        "r",
        encoding="utf-8"
    ) as archivo:
        datos = json.load(archivo)

    if not isinstance(datos, list) or not datos:
        raise ValueError(
            "seleccion.json está vacío o no tiene el formato correcto."
        )

    cargadores = {}

    for item in datos:

        cupr_id = item.get("cuprId")

        if cupr_id is None:
            continue

        cupr_id = int(cupr_id)

        cargadores[cupr_id] = {
            "cuprId": cupr_id,
            "nombre": item.get("nombre", "Cargador sin nombre"),
            "direccion": item.get("direccion", ""),
            "localidad": item.get("localidad", "")
        }

    if not cargadores:
        raise ValueError(
            "No hay identificadores cuprId válidos en seleccion.json."
        )

    return cargadores


# --------------------------------------------------
# TELEGRAM
# --------------------------------------------------

def enviar_telegram(mensaje):

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
# IDENTIFICAR CARGADOR EN RESPUESTA IBERDROLA
# --------------------------------------------------

def obtener_cupr_id(cargador):

    location = cargador.get("locationData") or {}

    return (
        cargador.get("cuprId")
        or location.get("cuprId")
    )


# --------------------------------------------------
# CONSULTA IBERDROLA
# --------------------------------------------------

def consultar_cargadores(cargadores):

    # Accedemos primero a la web para obtener las cookies
    pagina = SESSION.get(
        URL_WEB,
        timeout=30
    )

    print("Página Iberdrola:", pagina.status_code)
    pagina.raise_for_status()

    payload = {
        "dto": {
            "cuprId": list(cargadores.keys())
        },
        "language": "es"
    }

    respuesta = SESSION.post(
        URL_IBERDROLA,
        json=payload,
        timeout=30
    )

    print("Respuesta API:", respuesta.status_code)

    if respuesta.status_code != 200:
        print("Contenido devuelto por Iberdrola:")
        print(respuesta.text[:1500])
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

    encontrados = {}

    for item in entidades:

        cupr_id = obtener_cupr_id(item)

        if cupr_id is not None:
            encontrados[int(cupr_id)] = item

    return encontrados


# --------------------------------------------------
# ANALISIS DE DISPONIBILIDAD
# --------------------------------------------------

def analizar_cargador(cargador):

    estados = []

    for logical in cargador.get("logicalSocket") or []:

        fisicas = logical.get("physicalSocket") or []

        if fisicas:

            for toma in fisicas:

                status = toma.get("status") or {}
                estado = status.get("statusCode")

                if estado:
                    codigo = toma.get("physicalSocketCode", "?")
                    estados.append((codigo, estado))

        else:

            status = logical.get("status") or {}
            estado = status.get("statusCode")

            if estado:
                estados.append(("?", estado))

    # Si hay estados individuales, tienen prioridad
    if estados:

        disponibles = [
            codigo
            for codigo, estado in estados
            if estado == "AVAILABLE"
        ]

        hay_disponibilidad = len(disponibles) > 0

        resumen = ", ".join(
            f"{codigo}: {estado}"
            for codigo, estado in estados
        )

        return (
            hay_disponibilidad,
            resumen,
            disponibles,
            "tomas individuales"
        )

    # Si no hay estados individuales, usamos el estado general
    cp_status = cargador.get("cpStatus") or {}
    estado_general = cp_status.get("statusCode")

    if estado_general == "AVAILABLE":

        return (
            True,
            "Cargador disponible",
            ["Disponibilidad general"],
            "estado general"
        )

    elif estado_general == "OCCUPIED":

        return (
            False,
            "Cargador ocupado",
            [],
            "estado general"
        )

    # No tenemos información suficiente
    return None, "Estado desconocido", [], "sin información"


# --------------------------------------------------
# VIGILANCIA
# --------------------------------------------------

def vigilar():

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env"
        )

    cargadores = cargar_seleccion()

    estados_anteriores = {
        cupr_id: None
        for cupr_id in cargadores
    }

    duracion = DURACION_HORAS * 60 * 60
    intervalo = INTERVALO_MINUTOS * 60

    inicio = time.monotonic()
    final = inicio + duracion

    print("\n======================================")
    print(" VIGILANCIA DE CARGADORES IBERDROLA")
    print("======================================")

    print(f"Cargadores seleccionados: {len(cargadores)}")
    print(f"Duración: {DURACION_HORAS} horas")
    print(f"Intervalo: {INTERVALO_MINUTOS} minutos")

    for cargador in cargadores.values():
        print(
            f"- {cargador['nombre']} "
            f"(cuprId: {cargador['cuprId']})"
        )

    lista_nombres = "\n".join(
        f"• {c['nombre']} - {c['localidad']}"
        for c in cargadores.values()
    )

    enviar_telegram(
        "🟢 Bot Iberdrola iniciado.\n\n"
        f"Cargadores vigilados: {len(cargadores)}\n"
        f"{lista_nombres}\n\n"
        f"Duración: {DURACION_HORAS} horas.\n"
        f"Comprobación cada {INTERVALO_MINUTOS} minutos."
    )

    while time.monotonic() < final:

        hora = time.strftime("%H:%M:%S")

        print(f"\n[{hora}] Consultando Iberdrola...")

        try:

            encontrados = consultar_cargadores(cargadores)

            for cupr_id, configuracion in cargadores.items():

                cargador = encontrados.get(cupr_id)

                if cargador is None:

                    print(
                        f"AVISO: No aparece "
                        f"{configuracion['nombre']} "
                        f"(cuprId: {cupr_id})."
                    )
                    continue

                (
                    hay_disponibilidad,
                    resumen,
                    disponibles,
                    fuente
                ) = analizar_cargador(cargador)

                print(
                    f"{configuracion['nombre']}: "
                    f"{resumen} [{fuente}]"
                )

                # Si no hay información, no alteramos
                # el estado anterior ni enviamos avisos.
                if hay_disponibilidad is None:
                    continue

                anterior = estados_anteriores[cupr_id]

                # Detectar disponibilidad nueva
                if hay_disponibilidad and anterior is not True:

                    mensaje = (
                        "🔌 ¡CARGADOR DISPONIBLE!\n\n"
                        f"{configuracion['nombre']}\n"
                        f"{configuracion['direccion']}\n"
                        f"{configuracion['localidad']}\n\n"
                        f"Información: {resumen}"
                    )

                    enviar_telegram(mensaje)

                    print("¡Aviso enviado a Telegram!")

                estados_anteriores[cupr_id] = hay_disponibilidad

        except requests.RequestException as error:
            print(f"Error de conexión: {error}")

        except Exception as error:
            print(f"Error durante la consulta: {error}")

        restante = final - time.monotonic()

        if restante > 0:
            time.sleep(min(intervalo, restante))

    print("\nVigilancia finalizada.")

    enviar_telegram(
        "⏹ Vigilancia Iberdrola finalizada.\n"
        f"Han transcurrido las {DURACION_HORAS} horas configuradas."
    )


# --------------------------------------------------
# INICIO
# --------------------------------------------------

if __name__ == "__main__":

    try:
        vigilar()

    except KeyboardInterrupt:

        print("\nVigilancia interrumpida por el usuario.")

        try:
            enviar_telegram(
                "⏹ Vigilancia Iberdrola detenida manualmente."
            )
        except Exception:
            pass

    except Exception as error:

        print("\nNo se ha podido iniciar la vigilancia:")
        print(error)
