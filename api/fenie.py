
from pathlib import Path
import json
import unicodedata
import time

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from vigilancia import ErrorVigilancia, iniciar_vigilancia_fenie


router = APIRouter(prefix="/api/fenie", tags=["Feníe Energía"])

# El catálogo está en la carpeta principal del proyecto.
CATALOGO = (
    Path(__file__).resolve().parent.parent
    / "fenie_catalogo_completo.json"
)

URL_ESTADO = "https://recarga.fenieenergia.es/connectors/{}/status"

TIMEOUT = 8

ESTADOS_NO_DISPONIBLES = {
    "Occupied",
    "Charging",
    "Finishing",
    "Preparing",
    "Reserved",
    "SuspendedEV",
    "SuspendedEVSE",
    "Faulted",
    "Unavailable",
}


class SolicitudVigilanciaFenie(BaseModel):
    codigo: str
    telefono: str


def normalizar(texto):
    """Normaliza texto para comparar localidades sin distinguir acentos."""
    texto = unicodedata.normalize("NFD", str(texto or ""))
    texto = "".join(
        caracter for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    return texto.casefold().strip()


def cargar_catalogo():
    """Carga el catálogo estático de Feníe."""
    if not CATALOGO.exists():
        raise HTTPException(
            status_code=500,
            detail="No se encuentra el catálogo de Feníe."
        )

    try:
        with CATALOGO.open("r", encoding="utf-8") as archivo:
            return json.load(archivo)

    except (OSError, json.JSONDecodeError) as error:
        print(f"[ERROR FENIE] Catálogo: {error}", flush=True)
        raise HTTPException(
            status_code=500,
            detail="No se ha podido leer el catálogo de Feníe."
        )


def consultar_estado(conector_id, sesion):
    """Consulta en directo el estado de un conector."""
    if not str(conector_id).isdigit():
        return None

    try:
        respuesta = sesion.get(
            URL_ESTADO.format(conector_id),
            timeout=TIMEOUT
        )
        respuesta.raise_for_status()

        datos = respuesta.json()
        estado = datos.get("status")

        if isinstance(estado, str):
            return estado

        return None

    except (requests.RequestException, ValueError) as error:
        print(
            f"[FENIE] No se pudo consultar el conector "
            f"{conector_id}: {error}",
            flush=True
        )
        return None


def hay_conector_libre(conectores):
    """Indica si alguno de los conectores está disponible ahora mismo."""
    with requests.Session() as sesion:
        for conector in conectores:
            if consultar_estado(conector.get("id"), sesion) == "Available":
                return True

            # Pequeña pausa para no sobrecargar el servicio externo.
            time.sleep(0.15)

    return False


def buscar_por_localidad(localidad):
    """Busca puntos de Feníe y consulta sus conectores en directo."""
    catalogo = cargar_catalogo()
    localidad_normalizada = normalizar(localidad)

    encontrados = []

    for punto in catalogo:
        direccion = normalizar(punto.get("direccion", ""))

        if localidad_normalizada in direccion:
            encontrados.append(punto)

    if not encontrados:
        return {
            "localidad": localidad,
            "total": 0,
            "cargadores": []
        }

    sesion = requests.Session()
    resultados = []

    try:
        for punto in encontrados:
            conectores = []

            for conector in punto.get("conectores", []):
                conector_id = conector.get("id")

                estado = consultar_estado(conector_id, sesion)

                conectores.append({
                    "id": conector_id,
                    "tipo": conector.get("tipo"),
                    "potencia": conector.get("potencia"),
                    "estado": estado
                })

                # Pequeña pausa para no sobrecargar el servicio externo.
                time.sleep(0.15)

            disponibles = sum(
                1 for c in conectores
                if c["estado"] == "Available"
            )

            desconocidos = sum(
                1 for c in conectores
                if c["estado"] is None
            )

            if disponibles > 0:
                disponible = True
            elif desconocidos > 0:
                disponible = None
            else:
                disponible = False

            resultados.append({
                "codigo": punto.get("codigo"),
                "nombre": punto.get("nombre"),
                "direccion": punto.get("direccion"),
                "latitud": punto.get("latitud"),
                "longitud": punto.get("longitud"),
                "precio_kwh": punto.get("precio_kwh"),
                "gratuito": punto.get("gratuito"),
                "estado_tarifa": punto.get("estado_tarifa"),
                "disponible": disponible,
                "tomas": len(conectores),
                "tomas_disponibles": disponibles,
                "conectores": conectores
            })

    finally:
        sesion.close()

    resultados.sort(
        key=lambda punto: (
            punto["disponible"] is not True,
            -punto["tomas_disponibles"]
        )
    )

    return {
        "localidad": localidad,
        "total": len(resultados),
        "cargadores": resultados
    }


@router.post("/vigilancia")
def activar_vigilancia_fenie(
    solicitud: SolicitudVigilanciaFenie,
    request: Request
):
    # Los datos del punto se toman del catálogo, no de la petición.
    punto = next(
        (
            p for p in cargar_catalogo()
            if p.get("codigo") == solicitud.codigo
        ),
        None
    )

    if punto is None:
        raise HTTPException(
            status_code=404,
            detail="No se encuentra el cargador de Feníe."
        )

    conectores = punto.get("conectores", [])

    try:
        iniciada = iniciar_vigilancia_fenie(
            solicitud.codigo,
            lambda: hay_conector_libre(conectores),
            solicitud.telefono,
            request.client.host if request.client else "desconocida",
            punto.get("nombre") or solicitud.codigo,
            punto.get("direccion") or ""
        )

    except ErrorVigilancia as error:
        raise HTTPException(status_code=error.estado, detail=str(error))

    return {
        "ok": True,
        "mensaje": (
            "Vigilancia iniciada. Te hemos enviado un WhatsApp."
            if iniciada
            else "Este cargador ya está vigilado para ese número."
        )
    }


@router.get("")
@router.get("/actualizar")
def obtener_cargadores_fenie(
    localidad: str = Query(..., min_length=2, max_length=25)
):
    localidad = localidad.strip()

    if not localidad:
        raise HTTPException(
            status_code=400,
            detail="Debes indicar una localidad."
        )

    if any(
        ord(caracter) < 32 or ord(caracter) == 127
        for caracter in localidad
    ):
        raise HTTPException(
            status_code=400,
            detail="La localidad contiene caracteres no permitidos."
        )

    try:
        return buscar_por_localidad(localidad)

    except HTTPException:
        raise

    except Exception as error:
        print(f"[ERROR FENIE] {error!r}", flush=True)
        raise HTTPException(
            status_code=502,
            detail="No se ha podido consultar Feníe Energía."
        )
