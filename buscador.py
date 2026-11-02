
import csv
import math
import requests
import json

# --------------------------------------------------
# CONFIGURACIÓN
# --------------------------------------------------

URL_WEB = (
    "https://www.iberdrola.es/"
    "movilidad-electrica/productos/puntos-de-recarga"
)

URL_API = (
    "https://www.iberdrola.es/o/webclipb/iberdrola/"
    "puntosrecargacontroller/getListarPuntosRecarga"
)

URL_API_ESTADO = (
    "https://www.iberdrola.es/o/webclipb/iberdrola/"
    "puntosrecargacontroller/getDatosPuntoRecarga"
)

URL_GEOCODING = "https://nominatim.openstreetmap.org/search"

# Radio inicial de búsqueda en kilómetros
RADIO_KM = 5

# Identificación del programa para el servicio de geocodificación
USER_AGENT = "BuscadorCargadoresIberdrola/1.0"


# --------------------------------------------------
# CONEXIÓN CON IBERDROLA
# --------------------------------------------------

def crear_sesion():
    sesion = requests.Session()

    sesion.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/156.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9",
        "Origin": "https://www.iberdrola.es",
        "Referer": URL_WEB,
        "X-Requested-With": "XMLHttpRequest"
    })

    # Visitar primero la web para obtener las cookies necesarias
    respuesta = sesion.get(URL_WEB, timeout=30)
    respuesta.raise_for_status()

    return sesion


# --------------------------------------------------
# BUSCAR COORDENADAS DE UNA LOCALIDAD
# --------------------------------------------------

def obtener_coordenadas(localidad):
    print("\nBuscando coordenadas de:", localidad)

    respuesta = requests.get(
        URL_GEOCODING,
        params={
            "q": localidad + ", Valencia, España",
            "format": "jsonv2",
            "limit": 1
        },
        headers={"User-Agent": USER_AGENT},
        timeout=20
    )

    respuesta.raise_for_status()
    resultados = respuesta.json()

    if not resultados:
        raise ValueError("No se ha encontrado esa localidad.")

    latitud = float(resultados[0]["lat"])
    longitud = float(resultados[0]["lon"])

    print("Coordenadas:", latitud, longitud)

    return latitud, longitud


# --------------------------------------------------
# CONSTRUIR EL ÁREA DE BÚSQUEDA
# --------------------------------------------------

def crear_limites(latitud, longitud, radio_km):
    margen_lat = radio_km / 111.0

    margen_lon = radio_km / (
        111.0 * math.cos(math.radians(latitud))
    )

    return {
        "latitudeMax": latitud + margen_lat,
        "latitudeMin": latitud - margen_lat,
        "longitudeMax": longitud + margen_lon,
        "longitudeMin": longitud - margen_lon
    }


# --------------------------------------------------
# CONSULTAR CARGADORES EN IBERDROLA
# --------------------------------------------------

def buscar_cargadores(sesion, limites):

    payload = {
        "dto": {
            "chargePointTypesCodes": ["P", "R", "I", "N"],
            "socketStatus": [],
            "advantageous": False,
            "connectorsType": [],
            "loadSpeed": [],
            **limites
        },
        "language": "es"
    }

    respuesta = sesion.post(
        URL_API,
        json=payload,
        timeout=30
    )

    respuesta.raise_for_status()

    datos = respuesta.json()

    if datos.get("serviceException"):
        raise RuntimeError(
            f"Error del servicio: {datos['serviceException']}"
        )

    if datos.get("errores"):
        raise RuntimeError(
            f"Errores devueltos: {datos['errores']}"
        )

    cargadores = datos.get("entidad") or []

    # Eliminar posibles duplicados por cuprId
    unicos = {}

    for cargador in cargadores:
        location = cargador.get("locationData") or {}
        cupr_id = location.get("cuprId")

        if cupr_id is not None:
            unicos[cupr_id] = cargador

    return list(unicos.values())

# --------------------------------------------------
# CONSULTAR DISPONIBILIDAD REAL
# --------------------------------------------------

def consultar_disponibilidad(sesion, cargadores):

    if not cargadores:
        return cargadores

    # Obtener cookies de Iberdrola antes de consultar
    pagina = sesion.get(URL_WEB, timeout=30)
    pagina.raise_for_status()

    cupr_ids = []

    for cargador in cargadores:
        location = cargador.get("locationData") or {}
        cupr_id = location.get("cuprId")

        if cupr_id is not None:
            cupr_ids.append(int(cupr_id))

    payload = {
        "dto": {
            "cuprId": cupr_ids
        },
        "language": "es"
    }

    respuesta = sesion.post(
        URL_API_ESTADO,
        json=payload,
        timeout=30
    )

    print("Consulta de disponibilidad:", respuesta.status_code)
    respuesta.raise_for_status()

    datos = respuesta.json()

    if datos.get("errorAjax") or datos.get("serviceException"):
        raise RuntimeError(
            "Iberdrola ha devuelto un error al consultar disponibilidad."
        )

    entidades = datos.get("entidad")

    if not isinstance(entidades, list):
        raise RuntimeError(
            "Respuesta de disponibilidad no válida."
        )

    encontrados = {}

    for item in entidades:
        location = item.get("locationData") or {}
        cupr_id = item.get("cuprId") or location.get("cuprId")

        if cupr_id is not None:
            encontrados[int(cupr_id)] = item

    # Asociar la información de disponibilidad a cada cargador
    for cargador in cargadores:

        location = cargador.get("locationData") or {}
        cupr_id = location.get("cuprId")

        estado_actual = encontrados.get(int(cupr_id))

        if estado_actual:
            cargador["datosDisponibilidad"] = estado_actual
        else:
            cargador["datosDisponibilidad"] = None

    return cargadores


# --------------------------------------------------
# ANALIZAR ESTADOS DE LAS TOMAS
# --------------------------------------------------

def analizar_disponibilidad(cargador):

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

    if estados:

        disponibles = [
            codigo
            for codigo, estado in estados
            if estado == "AVAILABLE"
        ]

        ocupadas = [
            codigo
            for codigo, estado in estados
            if estado == "OCCUPIED"
        ]

        resumen = ", ".join(
            f"{codigo}: {estado}"
            for codigo, estado in estados
        )

        return {
            "disponible": len(disponibles) > 0,
            "resumen": resumen,
            "tomas_totales": len(estados),
            "tomas_disponibles": len(disponibles),
            "tomas_ocupadas": len(ocupadas),
            "estado": (
                "AVAILABLE" if disponibles
                else "OCCUPIED" if ocupadas
                else "DESCONOCIDO"
            )
        }

    # Si no hay estados individuales, utilizar el general
    cp_status = cargador.get("cpStatus") or {}
    estado_general = cp_status.get("statusCode")

    if estado_general == "AVAILABLE":
        return {
            "disponible": True,
            "resumen": "Cargador disponible (estado general)",
            "tomas_totales": 0,
            "tomas_disponibles": 0,
            "tomas_ocupadas": 0,
            "estado": "AVAILABLE"
            
        }

    if estado_general == "OCCUPIED":
        return {
            "disponible": False,
            "resumen": "Cargador ocupado (estado general)",
            "tomas_totales": 0,
            "tomas_disponibles": 0,
            "tomas_ocupadas": 0,
            "estado": "OCCUPIED"
        }

    return {
        "disponible": None,
        "resumen": "Estado desconocido",
        "tomas_totales": 0,
        "tomas_disponibles": 0,
        "tomas_ocupadas": 0,
        "estado": "DESCONOCIDO"
    }


# --------------------------------------------------
# EXTRAER INFORMACIÓN DE CADA CARGADOR
# --------------------------------------------------

def corregir_texto(texto):

    if not isinstance(texto, str):
        return texto

    try:
        return texto.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto
        
def extraer_datos(cargador):

    location = cargador.get("locationData") or {}
    supply = location.get("supplyPointData") or {}
    address = supply.get("cpAddress") or {}

    # Obtener disponibilidad actual
    datos_estado = cargador.get("datosDisponibilidad")

    if datos_estado:
        disponibilidad = analizar_disponibilidad(datos_estado)
    else:
        disponibilidad = {
            "disponible": None,
            "resumen": "Sin información de disponibilidad",
            "tomas_totales": 0,
            "tomas_disponibles": 0,
            "tomas_ocupadas": 0,
            "estado": "DESCONOCIDO"
        }

    return {
        "nombre": corregir_texto(
            location.get("cuprName", "Sin nombre")
        ),
        "cuprId": location.get("cuprId", ""),
        "cpId": cargador.get("cpId", ""),
        "direccion": corregir_texto(
            (
                f"{address.get('streetName', '')}, "
                f"{address.get('streetNum', '')}, "
                f"{address.get('townName', '')}"
            ).strip(" ,")
        ),
        "localidad": address.get("townName", ""),
        "estado": disponibilidad["estado"],
        "disponible": disponibilidad["disponible"],
        "resumen": disponibilidad["resumen"],
        "tomas": (
                disponibilidad["tomas_totales"]
                if disponibilidad["tomas_totales"] > 0
                else cargador.get("socketNum", 0)
            ),
        "tomas_iber": cargador.get("socketNum", 0),
        "tomas_disponibles": disponibilidad["tomas_disponibles"],
        "tomas_ocupadas": disponibilidad["tomas_ocupadas"],
        "latitud": location.get("latitude", ""),
        "longitud": location.get("longitude", "")
    }


# --------------------------------------------------
# MOSTRAR RESULTADOS
# --------------------------------------------------


def mostrar_resultados(cargadores):

    if not cargadores:
        print("\nNo se han encontrado cargadores.")
        return []

    resultados = []

    print("\n" + "=" * 75)
    print(f"       CARGADORES ENCONTRADOS: {len(cargadores)}")
    print("=" * 75)

    for numero, cargador in enumerate(cargadores, start=1):

        datos = extraer_datos(cargador)
        resultados.append(datos)

        # Presentación del estado general
        if datos["disponible"] is True:
            estado_visual = "DISPONIBLE"
        elif datos["disponible"] is False:
            estado_visual = "OCUPADO"
        else:
            estado_visual = "DESCONOCIDO"

        print(f"\n[{numero}] {datos['nombre']}")
        print("-" * 75)

        print(f"  Localidad       : {datos['localidad']}")
        print(f"  Dirección      : {datos['direccion']}")
        print(f"  Estado general : {estado_visual}")

        print(
            f"  Tomas totales  : {datos['tomas']}"
        )
        print(f"  Tomas según Iberdrola: {datos['tomas_iber']}")
        print(
            f"  Tomas libres   : {datos['tomas_disponibles']}"
        )
        print(
            f"  Tomas ocupadas : {datos['tomas_ocupadas']}"
        )

        print(f"  Detalle        : {datos['resumen']}")
        print(f"  Identificador  : {datos['cuprId']}")

    print("\n" + "=" * 75)

    return resultados



# --------------------------------------------------
# EXPORTAR RESULTADOS A CSV
# --------------------------------------------------

def guardar_csv(resultados):

    if not resultados:
        return

    nombre_archivo = "cargadores_encontrados.csv"

    with open(
        nombre_archivo,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as archivo:

        campos = list(resultados[0].keys())

        escritor = csv.DictWriter(
            archivo,
            fieldnames=campos
        )

        escritor.writeheader()
        escritor.writerows(resultados)

    print(f"\nResultados guardados en: {nombre_archivo}")


# --------------------------------------------------
# PROGRAMA PRINCIPAL
# --------------------------------------------------


def main():

    print("\n======================================")
    print("   BUSCADOR DE CARGADORES IBERDROLA")
    print("======================================")

    localidad = input("\nIntroduce una localidad: ").strip()

    if not localidad:
        print("Debes introducir una localidad.")
        return

    try:
        latitud, longitud = obtener_coordenadas(localidad)

        limites = crear_limites(
            latitud,
            longitud,
            RADIO_KM
        )

        print(f"\nÁrea de búsqueda: {RADIO_KM} km")

        print("\nConectando con Iberdrola...")
        sesion = crear_sesion()

        print("Consultando cargadores...")
        cargadores = buscar_cargadores(sesion, limites)

        print("\nConsultando disponibilidad actual...")
        cargadores = consultar_disponibilidad(sesion, cargadores)

        # Ordenar: disponibles primero, ocupados después
        cargadores.sort(
            key=lambda c: (
                c.get("disponible") is not True,
                -c.get("tomas_disponibles", 0)
            )
        )

        resultados = mostrar_resultados(cargadores)

        if not resultados:
            return

        guardar_csv(resultados)

        # Aplicar filtro
        filtrados = filtrar_cargadores(resultados)

        print(
            f"\nCargadores que cumplen el filtro: "
            f"{len(filtrados)}"
        )

        # Seleccionar cargadores
        seleccionar_cargadores(filtrados)

    except requests.RequestException as error:
        print("\nError de conexión:", error)

    except (ValueError, RuntimeError) as error:
        print("\nError:", error)

    except Exception as error:
        print("\nError inesperado:", error)


# --------------------------------------------------
# FILTRAR CARGADORES
# --------------------------------------------------

def filtrar_cargadores(resultados):

    print("\nFILTRO DE DISPONIBILIDAD")
    print("[1] Todos")
    print("[2] Solo disponibles")
    print("[3] Solo ocupados")

    opcion = input("\nSelecciona una opción: ").strip()

    if opcion == "2":
        return [
            c for c in resultados
            if c["disponible"] is True
        ]

    elif opcion == "3":
        return [
            c for c in resultados
            if c["disponible"] is False
        ]

    return resultados


# --------------------------------------------------
# SELECCIONAR CARGADORES
# --------------------------------------------------

def seleccionar_cargadores(resultados):

    if not resultados:
        print("\nNo hay cargadores para seleccionar.")
        return

    print("\nCARGADORES DISPONIBLES PARA SELECCIONAR")
    print("Introduce los números separados por comas.")
    print("Ejemplo: 1,3,5")
    print("También puedes escribir TODOS.\n")

    for numero, cargador in enumerate(resultados, start=1):

        print(
            f"[{numero}] {cargador['nombre']} | "
            f"{cargador['localidad']} | "
            f"{cargador['estado']} | "
            f"cuprId: {cargador['cuprId']}"
        )

    seleccion = input("\nTu selección: ").strip().upper()

    if seleccion == "TODOS":
        elegidos = resultados

    else:
        try:
            numeros = [
                int(n.strip())
                for n in seleccion.split(",")
            ]

            if any(n < 1 or n > len(resultados) for n in numeros):
                print("Hay números fuera del rango.")
                return

            # Evitar duplicados conservando el orden
            numeros = list(dict.fromkeys(numeros))

            elegidos = [
                resultados[n - 1]
                for n in numeros
            ]

        except ValueError:
            print("Selección incorrecta.")
            return

    guardar_seleccion(elegidos)


# --------------------------------------------------
# GUARDAR SELECCIÓN
# --------------------------------------------------

def guardar_seleccion(cargadores):

    seleccion = []

    for cargador in cargadores:

        seleccion.append({
            "cuprId": cargador["cuprId"],
            "nombre": cargador["nombre"],
            "direccion": cargador["direccion"],
            "localidad": cargador["localidad"],
            "tomas": cargador["tomas"]
        })

    with open(
        "seleccion.json",
        "w",
        encoding="utf-8"
    ) as archivo:

        json.dump(
            seleccion,
            archivo,
            ensure_ascii=False,
            indent=4
        )

    print("\n======================================")
    print(" SELECCIÓN GUARDADA CORRECTAMENTE")
    print("======================================")
    print(f"Cargadores seleccionados: {len(seleccion)}")
    print("Archivo generado: seleccion.json")

if __name__ == "__main__":
    main()

