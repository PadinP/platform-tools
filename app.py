# A partir de aquí empieza la aplicación Streamlit normal.
import re
from io import BytesIO

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from mplsoccer import VerticalPitch


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

st.set_page_config(
    page_title="Equipos de fútbol",
    page_icon="⚽",
    layout="wide"
)

# ============================================================
# OCULTAR DESCARGA CSV NATIVA DE ST.DATAFRAME
# ============================================================
# Streamlit puede cambiar internamente el HTML de la barra de herramientas,
# por eso se cubren varias variantes del botón de descarga CSV.
st.markdown(
    """
    <style>
    button[aria-label="Download as CSV"],
    button[title="Download as CSV"],
    button[aria-label*="CSV" i],
    button[title*="CSV" i] {
        display: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

CSV_PATH = "jugadores_rfaf.csv"

SIN_SELECCION = "— Sin seleccionar —"


# ============================================================
# COMPETICIONES OBJETIVO
# Cada categoría define exactamente qué competiciones y grupos
# deben aparecer en los filtros del campo de fútbol.
# ============================================================

OBJETIVOS = [
    {
        "categoria": "FÚTBOL NACIONAL",
        "competicion": ["TERCERA FEDERACIÓN", "TERCERA DIVISIÓN"],
        "grupos": [None],
    },
    {
        "categoria": "FÚTBOL NACIONAL",
        "competicion": ["LIGA NACIONAL JUVENIL", "LIGA NACIONAL"],
        "grupos": [None],
    },
    {
        "categoria": "FÚTBOL REGIONAL",
        "competicion": ["REGIONAL PREFERENTE"],
        "grupos": ["1", "2"],
    },
    {
        "categoria": "FÚTBOL JUVENIL",
        "competicion": ["JUVENIL PREFERENTE", "PREFERENTE JUVENIL"],
        "grupos": [
            "Grupo 1 - Zaragoza",
            "Grupo 2 - Huesca",
            "Grupo 4 - Teruel",
            "Grupo 3-A - Provincia Zaragoza",
            "Grupo 3-B - Provincia Zaragoza",
        ],
    },
    {
        "categoria": "FÚTBOL JUVENIL",
        "competicion": ["1ª JUVENIL", "PRIMERA JUVENIL"],
        "grupos": ["Grupo 1 - Zaragoza", "Grupo 2 - Zaragoza"],
    },
    {
        "categoria": "FÚTBOL BASE",
        "competicion": [
            "DIVISIÓN DE HONOR CADETE",
            "DIVISION DE HONOR CADETE",
        ],
        "grupos": [None],
    },
]


# ============================================================
# CARGAR CSV
# ============================================================

@st.cache_data
def cargar_datos(archivo):

    if hasattr(
        archivo,
        "seek"
    ):
        archivo.seek(0)

    df = pd.read_csv(
        archivo,
        encoding="utf-8-sig"
    )

    columnas_numericas = [
        "Ano_nacimiento",
        "Convocados",
        "Titular",
        "Suplente",
        "Jugados",
        "Total_Goles",
        "Media_Goles",
        "Amarillas",
        "Rojas",
        "Doble_Amarilla"
    ]

    for columna in columnas_numericas:

        if columna in df.columns:

            df[columna] = pd.to_numeric(
                df[columna],
                errors="coerce"
            )

    return df


# ============================================================
# TEXTO SEGURO
# ============================================================

def texto_seguro(valor):

    if pd.isna(valor):
        return ""

    return str(valor).strip()


def normalizar_texto(valor):
    """Normaliza texto para comparar filtros sin depender de mayúsculas."""
    return re.sub(
        r"\s+",
        " ",
        texto_seguro(valor).upper()
    ).strip()


def normalizar_grupo(valor):
    """
    Normaliza grupos para que, por ejemplo, "1", "Grupo 1"
    y "GRUPO 1" se consideren el mismo grupo.
    """

    texto = normalizar_texto(
        valor
    )

    coincidencia = re.fullmatch(
        r"GRUPO\s+(\d+)",
        texto
    )

    if coincidencia:
        return coincidencia.group(1)

    return texto


def objetivo_por_competicion(competicion, categoria=None):
    """
    Devuelve el bloque de OBJETIVOS que corresponde a una competición.
    Si se pasa categoría, también exige que coincida.
    """

    competicion_normalizada = normalizar_texto(
        competicion
    )

    categoria_normalizada = normalizar_texto(
        categoria
    )

    for objetivo in OBJETIVOS:

        if (
            categoria is not None
            and normalizar_texto(
                objetivo["categoria"]
            ) != categoria_normalizada
        ):
            continue

        aliases = {
            normalizar_texto(alias)
            for alias in objetivo["competicion"]
        }

        if competicion_normalizada in aliases:
            return objetivo

    return None


def categoria_de_competicion(competicion, categoria_fallback=""):
    """Obtiene la categoría correcta a partir de OBJETIVOS."""

    objetivo = objetivo_por_competicion(
        competicion
    )

    if objetivo is not None:
        return objetivo["categoria"]

    return texto_seguro(
        categoria_fallback
    )


def grupos_configurados(categoria, competicion):
    """
    Devuelve los grupos permitidos para la pareja
    categoría + competición definida en OBJETIVOS.
    """

    objetivo = objetivo_por_competicion(
        competicion,
        categoria
    )

    if objetivo is None:
        return []

    return objetivo["grupos"]


# ============================================================
# PROCESAR COMPETICIONES_TEMPORADA
# ============================================================

def separar_competiciones(df):

    """
    Genera los registros que utiliza la app para filtrar equipos.

    IMPORTANTE:
    - La competición de origen SIEMPRE se conserva.
    - ``Competiciones_temporada`` se considera información adicional,
      no un sustituto de ``Competicion_origen``.

    Esto evita que un jugador desaparezca de su plantilla de origen
    simplemente porque también tenga otra competición registrada
    durante la temporada.
    """

    registros = []

    patron = re.compile(
        r"^\s*"
        r"(.*?)\s*,\s*"          # Equipo
        r"(.*?)\s*·\s*"          # Competición
        r"(.*?)\s*,\s*"          # Grupo
        r"Posicion\s*,\s*"
        r"([^,]*)\s*,\s*"        # Posición
        r"Puntos\s*,\s*"
        r"([^,]*)",               # Puntos
        re.IGNORECASE
    )

    for _, fila in df.iterrows():

        # ----------------------------------------------------
        # 1. REGISTRO DE ORIGEN: SIEMPRE SE AÑADE
        # ----------------------------------------------------

        nuevo_origen = fila.to_dict()

        equipo_origen = texto_seguro(
            fila.get("Equipo_origen")
        )

        club_temporada = texto_seguro(
            fila.get("Club_en_temporada")
        )

        # Las estadísticas de la fila proceden de la competición
        # y equipo de origen, por eso se prioriza Equipo_origen.
        equipo_base = (
            equipo_origen
            if equipo_origen
            else club_temporada
        )

        liga_origen = texto_seguro(
            fila.get("Competicion_origen")
        )

        grupo_origen = texto_seguro(
            fila.get("Grupo_origen")
        )

        nuevo_origen["Equipo_filtro"] = equipo_base
        nuevo_origen["Liga_filtro"] = liga_origen
        nuevo_origen["Grupo_filtro"] = grupo_origen
        nuevo_origen["Posicion_equipo"] = ""
        nuevo_origen["Puntos_equipo"] = ""

        objetivo_origen = objetivo_por_competicion(
            liga_origen,
            fila.get("Categoria_origen")
        )

        if objetivo_origen is None:
            objetivo_origen = objetivo_por_competicion(
                liga_origen
            )

        nuevo_origen["Categoria_filtro"] = (
            objetivo_origen["categoria"]
            if objetivo_origen is not None
            else texto_seguro(
                fila.get("Categoria_origen")
            )
        )

        nuevo_origen["Es_objetivo"] = (
            objetivo_origen is not None
        )

        # ----------------------------------------------------
        # 2. COMPETICIONES ADICIONALES DE LA TEMPORADA
        # ----------------------------------------------------

        raw = fila.get(
            "Competiciones_temporada"
        )

        bloques = []

        if pd.notna(raw):

            texto = str(raw).strip()

            if texto:

                bloques = [
                    bloque.strip()
                    for bloque in texto.split("||")
                    if bloque.strip()
                ]

        registros_adicionales = []

        for bloque in bloques:

            coincidencia = patron.search(
                bloque
            )

            if not coincidencia:
                continue

            equipo = coincidencia.group(1).strip()
            liga = coincidencia.group(2).strip()
            grupo = coincidencia.group(3).strip()
            posicion = coincidencia.group(4).strip()
            puntos = coincidencia.group(5).strip()

            # Si el bloque describe exactamente la misma competición
            # de origen, aprovechamos posición/puntos y no duplicamos.
            misma_competicion_origen = (
                normalizar_texto(equipo) == normalizar_texto(equipo_base)
                and normalizar_texto(liga) == normalizar_texto(liga_origen)
                and normalizar_grupo(grupo) == normalizar_grupo(grupo_origen)
            )

            if misma_competicion_origen:
                nuevo_origen["Posicion_equipo"] = posicion
                nuevo_origen["Puntos_equipo"] = puntos
                continue

            nuevo = fila.to_dict()

            nuevo["Equipo_filtro"] = equipo
            nuevo["Liga_filtro"] = liga
            nuevo["Grupo_filtro"] = grupo
            nuevo["Posicion_equipo"] = posicion
            nuevo["Puntos_equipo"] = puntos

            objetivo = objetivo_por_competicion(
                liga
            )

            nuevo["Categoria_filtro"] = (
                objetivo["categoria"]
                if objetivo is not None
                else ""
            )

            nuevo["Es_objetivo"] = (
                objetivo is not None
            )

            registros_adicionales.append(
                nuevo
            )

        # Primero conservamos siempre la fila de la competición de
        # origen y después añadimos las otras competiciones encontradas.
        registros.append(
            nuevo_origen
        )

        registros.extend(
            registros_adicionales
        )

    return pd.DataFrame(
        registros
    )


# ============================================================
# FORMACIONES
# ============================================================

FORMACIONES = {

    "4-3-3": [

        ("POR", 8, 40),

        ("LI", 30, 10),
        ("DFC-I", 27, 30),
        ("DFC-D", 27, 50),
        ("LD", 30, 70),

        ("MC-I", 58, 20),
        ("MCD", 53, 40),
        ("MC-D", 58, 60),

        ("EI", 90, 13),
        ("DC", 100, 40),
        ("ED", 90, 67),
    ],

    "4-2-3-1": [

        ("POR", 8, 40),

        ("LI", 30, 10),
        ("DFC-I", 27, 30),
        ("DFC-D", 27, 50),
        ("LD", 30, 70),

        ("MCD-I", 52, 29),
        ("MCD-D", 52, 51),

        ("EI", 76, 14),
        ("MCO", 79, 40),
        ("ED", 76, 66),

        ("DC", 100, 40),
    ],

    "4-4-2": [

        ("POR", 8, 40),

        ("LI", 30, 10),
        ("DFC-I", 27, 30),
        ("DFC-D", 27, 50),
        ("LD", 30, 70),

        ("MI", 60, 10),
        ("MC-I", 58, 30),
        ("MC-D", 58, 50),
        ("MD", 60, 70),

        ("DC-I", 96, 28),
        ("DC-D", 96, 52),
    ],

    "3-5-2": [

        ("POR", 8, 40),

        ("DFC-I", 30, 20),
        ("DFC", 27, 40),
        ("DFC-D", 30, 60),

        ("CAI", 55, 8),

        ("MC-I", 58, 26),
        ("MCD", 53, 40),
        ("MC-D", 58, 54),

        ("CAD", 55, 72),

        ("DC-I", 96, 29),
        ("DC-D", 96, 51),
    ],

    "3-4-3": [

        ("POR", 8, 40),

        ("DFC-I", 30, 20),
        ("DFC", 27, 40),
        ("DFC-D", 30, 60),

        ("MI", 58, 10),
        ("MC-I", 56, 30),
        ("MC-D", 56, 50),
        ("MD", 58, 70),

        ("EI", 88, 15),
        ("DC", 98, 40),
        ("ED", 88, 65),
    ],

    "5-3-2": [

        ("POR", 8, 40),

        ("CAI", 34, 7),
        ("DFC-I", 28, 23),
        ("DFC", 26, 40),
        ("DFC-D", 28, 57),
        ("CAD", 34, 73),

        ("MC-I", 59, 23),
        ("MC", 55, 40),
        ("MC-D", 59, 57),

        ("DC-I", 96, 29),
        ("DC-D", 96, 51),
    ]
}


# ============================================================
# NOMBRE CORTO PARA EL CAMPO
# ============================================================

def nombre_corto(nombre):

    if not isinstance(
        nombre,
        str
    ):
        return ""

    nombre = nombre.strip()

    # Ejemplo:
    # 4, ARÉVALO IBÁÑEZ, DAVID

    partes = [
        parte.strip()
        for parte in nombre.split(",")
    ]

    # --------------------------------------------------------
    # Si tiene dorsal + apellidos + nombre
    # --------------------------------------------------------

    if (
        len(partes) >= 3
        and partes[0].isdigit()
    ):

        dorsal = partes[0]
        apellidos = partes[1]
        nombres = partes[2]

        primer_apellido = (
            apellidos.split()[0].title()
            if apellidos
            else ""
        )

        return (
            f"{dorsal}\n"
            f"{primer_apellido}"
        )

    # --------------------------------------------------------
    # Apellidos, Nombre
    # --------------------------------------------------------

    if len(partes) >= 2:

        apellidos = partes[0]
        nombres = partes[1]

        primer_apellido = (
            apellidos.split()[0].title()
            if apellidos
            else ""
        )

        return primer_apellido

    return nombre.title()


# ============================================================
# PRIMER VALOR VÁLIDO
# ============================================================

def primer_valor(serie):

    for valor in serie:

        texto = texto_seguro(
            valor
        )

        if texto:
            return texto

    return ""


# ============================================================
# EXPORTAR TABLAS A EXCEL
# ============================================================

def nombre_archivo_seguro(texto):
    """Convierte un texto en un nombre válido para un archivo de Windows."""

    texto = texto_seguro(texto)
    texto = re.sub(r'[<>:"/\\|?*]+', "_", texto)

    return texto.strip(" ._") or "datos"


def dataframe_a_excel(df_exportar, nombre_hoja="Datos"):
    """Convierte un DataFrame en un archivo Excel .xlsx en memoria."""

    buffer = BytesIO()
    nombre_hoja = str(nombre_hoja)[:31] or "Datos"

    with pd.ExcelWriter(
        buffer,
        engine="openpyxl"
    ) as writer:
        df_exportar.to_excel(
            writer,
            index=False,
            sheet_name=nombre_hoja
        )

    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# TÍTULOS PRINCIPALES
# La interfaz se divide en dos secciones principales:
# 1) Base de datos de jugadores
# 2) Selección de equipo para la alineación
# ============================================================

# ============================================================
# CARGAR ARCHIVO
# El uploader se dibuja al FINAL de la app, pero su valor se
# puede recuperar aquí desde session_state en cada rerun.
# ============================================================

archivo_subido = st.session_state.get(
    "archivo_csv",
    None
)

try:

    if archivo_subido is not None:

        df_original = cargar_datos(
            archivo_subido
        )

    else:

        df_original = cargar_datos(
            CSV_PATH
        )

except FileNotFoundError:

    st.error(
        f"No se encontró el archivo {CSV_PATH}."
    )

    st.info(
        "Coloca jugadores_rfaf.csv en la misma carpeta que app.py "
        "o carga un CSV aquí."
    )

    st.file_uploader(
        "Selecciona un CSV",
        type=["csv"],
        key="archivo_csv"
    )

    st.stop()


# ============================================================
# PROCESAR DATOS
# ============================================================

df = separar_competiciones(
    df_original
)

df_objetivos = df[
    df["Es_objetivo"]
    .fillna(False)
].copy()


# ============================================================
# EXPLORADOR DE TODA LA BASE DE DATOS
# ============================================================

st.subheader(
    "Base de datos de jugadores"
)

def opciones_texto(serie):
    """Devuelve valores de texto únicos, limpios y ordenados."""

    return sorted(
        {
            texto_seguro(valor)
            for valor in serie
            if texto_seguro(valor)
        }
    )


def filtrar_por_texto(df_base, columna, valor):
    """Aplica un filtro exacto de texto si no se eligió 'Todos'."""

    if valor == "Todos" or columna not in df_base.columns:
        return df_base

    return df_base[
        df_base[columna]
        .fillna("")
        .astype(str)
        .str.strip()
        .eq(str(valor).strip())
    ].copy()


# ------------------------------------------------------------
# FILTROS DESPLEGABLES
# ------------------------------------------------------------

# 1) Año de nacimiento: filtro principal
anos_disponibles = sorted(
    pd.to_numeric(
        df_original["Ano_nacimiento"],
        errors="coerce"
    )
    .dropna()
    .astype(int)
    .unique()
    .tolist()
)

col_anio, col_temporada_bd, col_categoria_bd = st.columns(3)

with col_anio:
    anio_bd = st.selectbox(
        "Año de nacimiento",
        ["Todos"] + anos_disponibles,
        key="bd_anio"
    )

# Comenzamos a encadenar los filtros
# para que los siguientes desplegables solo enseñen valores posibles.
df_explorador = df_original.copy()

if anio_bd != "Todos":
    ano_numerico = pd.to_numeric(
        df_explorador["Ano_nacimiento"],
        errors="coerce"
    )

    df_explorador = df_explorador[
        ano_numerico.eq(int(anio_bd))
    ].copy()


with col_temporada_bd:
    temporadas_bd = opciones_texto(
        df_explorador["Temporada"]
    ) if "Temporada" in df_explorador.columns else []

    temporada_bd = st.selectbox(
        "Temporada",
        ["Todos"] + temporadas_bd,
        key="bd_temporada"
    )


df_explorador = filtrar_por_texto(
    df_explorador,
    "Temporada",
    temporada_bd
)


with col_categoria_bd:
    categorias_bd = opciones_texto(
        df_explorador["Categoria_origen"]
    ) if "Categoria_origen" in df_explorador.columns else []

    categoria_bd = st.selectbox(
        "Categoría",
        ["Todos"] + categorias_bd,
        key="bd_categoria"
    )


df_explorador = filtrar_por_texto(
    df_explorador,
    "Categoria_origen",
    categoria_bd
)


col_competicion_bd, col_grupo_bd, col_equipo_bd = st.columns(3)

with col_competicion_bd:
    competiciones_bd = opciones_texto(
        df_explorador["Competicion_origen"]
    ) if "Competicion_origen" in df_explorador.columns else []

    competicion_bd = st.selectbox(
        "Competición",
        ["Todos"] + competiciones_bd,
        key="bd_competicion"
    )


df_explorador = filtrar_por_texto(
    df_explorador,
    "Competicion_origen",
    competicion_bd
)


with col_grupo_bd:
    grupos_bd = opciones_texto(
        df_explorador["Grupo_origen"]
    ) if "Grupo_origen" in df_explorador.columns else []

    grupo_bd = st.selectbox(
        "Grupo",
        ["Todos"] + grupos_bd,
        key="bd_grupo"
    )


df_explorador = filtrar_por_texto(
    df_explorador,
    "Grupo_origen",
    grupo_bd
)


with col_equipo_bd:
    equipos_bd = opciones_texto(
        df_explorador["Equipo_origen"]
    ) if "Equipo_origen" in df_explorador.columns else []

    equipo_bd = st.selectbox(
        "Equipo",
        ["Todos"] + equipos_bd,
        key="bd_equipo"
    )


df_explorador = filtrar_por_texto(
    df_explorador,
    "Equipo_origen",
    equipo_bd
)


# Jugador se busca escribiendo nombre o apellido.
# No usamos un desplegable porque la lista puede contener miles de nombres.
busqueda_jugador_bd = st.text_input(
    "Jugador",
    placeholder="Escribe nombre o apellido...",
    key="bd_jugador_busqueda"
)

if busqueda_jugador_bd.strip() and "Jugador" in df_explorador.columns:

    df_explorador = df_explorador[
        df_explorador["Jugador"]
        .fillna("")
        .astype(str)
        .str.contains(
            busqueda_jugador_bd.strip(),
            case=False,
            na=False,
            regex=False
        )
    ].copy()


columnas_bd = [
    "Jugador",
    "Ano_nacimiento",
    "Temporada",
    "Categoria_origen",
    "Competicion_origen",
    "Grupo_origen",
    "Equipo_origen",
    "Club_en_temporada",
    "Estado",
    "Convocados",
    "Titular",
    "Suplente",
    "Jugados",
    "Total_Goles",
    "Media_Goles",
    "Amarillas",
    "Rojas",
    "Doble_Amarilla"
]

columnas_bd = [
    columna
    for columna in columnas_bd
    if columna in df_explorador.columns
]

st.caption(
    f"{len(df_explorador):,} registros mostrados"
    .replace(",", ".")
)

tabla_bd = df_explorador[
    columnas_bd
].copy()

st.dataframe(
    tabla_bd,
    width="stretch",
    height=430,
    hide_index=True
)

st.download_button(
    "📥 Descargar Excel (.xlsx)",
    data=dataframe_a_excel(tabla_bd, "Base de jugadores"),
    file_name="base_jugadores.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    key="guardar_bd_excel"
)

# ============================================================
# FILTROS PARA EL CAMPO DE FÚTBOL
# ============================================================

st.divider()

st.subheader(
    "Selección de equipo para la alineación"
)

if df_objetivos.empty:

    st.warning(
        "El archivo no contiene competiciones incluidas en OBJETIVOS."
    )

    st.stop()


# ============================================================
# TEMPORADA
# ============================================================

temporadas = sorted(
    df_objetivos["Temporada"]
    .dropna()
    .astype(str)
    .unique(),
    reverse=True
)

col_temporada, col_categoria = st.columns(
    [1.2, 2]
)

with col_temporada:

    temporada = st.selectbox(
        "📅 Temporada",
        temporadas
    )


df_temporada = df_objetivos[
    df_objetivos["Temporada"]
    .astype(str)
    .eq(temporada)
].copy()


# ============================================================
# CATEGORÍA
# ============================================================

orden_categorias = []

for objetivo in OBJETIVOS:

    categoria_objetivo = objetivo[
        "categoria"
    ]

    if categoria_objetivo not in orden_categorias:

        orden_categorias.append(
            categoria_objetivo
        )


categorias_presentes = {
    texto_seguro(valor)
    for valor in (
        df_temporada["Categoria_filtro"]
        .dropna()
        .unique()
    )
    if texto_seguro(valor)
}


categorias = [
    categoria_objetivo
    for categoria_objetivo in orden_categorias
    if categoria_objetivo in categorias_presentes
]


with col_categoria:

    categoria = st.selectbox(
        "🏷️ Categoría",
        categorias
    )


df_categoria = df_temporada[
    df_temporada["Categoria_filtro"]
    .eq(categoria)
].copy()


# ============================================================
# COMPETICIÓN
# ============================================================

ligas_reales = [
    liga
    for liga in (
        df_categoria["Liga_filtro"]
        .dropna()
        .astype(str)
        .unique()
    )
    if liga.strip()
    and liga.lower() != "nan"
]


ligas = []

for objetivo in OBJETIVOS:

    if objetivo["categoria"] != categoria:
        continue

    for alias in objetivo["competicion"]:

        coincidencias = [
            liga_real
            for liga_real in ligas_reales
            if normalizar_texto(
                liga_real
            ) == normalizar_texto(
                alias
            )
        ]

        for coincidencia in coincidencias:

            if coincidencia not in ligas:

                ligas.append(
                    coincidencia
                )


if not ligas:

    st.warning(
        "No hay competiciones disponibles para esta categoría "
        "en la temporada seleccionada."
    )

    st.stop()


# La competición se muestra primero. A partir de ella decidimos
# si esta categoría necesita selector de grupo o no.
col_liga, col_filtros_dependientes = st.columns(
    [2.2, 3.9]
)

with col_liga:

    liga = st.selectbox(
        "🏆 Competición",
        ligas
    )


df_liga = df_categoria[
    df_categoria["Liga_filtro"]
    .eq(liga)
].copy()


# ============================================================
# GRUPO
# Si OBJETIVOS contiene [None], la competición no tiene grupo y
# el selector desaparece por completo.
# ============================================================

grupos_configuracion = grupos_configurados(
    categoria,
    liga
)

tiene_selector_grupo = any(
    grupo_configurado is not None
    for grupo_configurado in grupos_configuracion
)


if tiene_selector_grupo:

    grupos_permitidos = [
        grupo_configurado
        for grupo_configurado in grupos_configuracion
        if grupo_configurado is not None
    ]

    grupos_reales = [
        grupo_real
        for grupo_real in (
            df_liga["Grupo_filtro"]
            .dropna()
            .astype(str)
            .unique()
        )
        if grupo_real.strip()
        and grupo_real.lower() != "nan"
    ]

    grupos_disponibles = []

    for grupo_configurado in grupos_permitidos:

        existe_en_datos = any(
            normalizar_grupo(
                grupo_real
            ) == normalizar_grupo(
                grupo_configurado
            )
            for grupo_real in grupos_reales
        )

        if existe_en_datos:

            grupos_disponibles.append(
                grupo_configurado
            )

    grupos = [
        "Todos"
    ] + grupos_disponibles

    with col_filtros_dependientes:

        col_grupo, col_equipo = st.columns(
            [1.7, 2.2]
        )

        with col_grupo:

            grupo = st.selectbox(
                "📍 Grupo",
                grupos
            )

    if grupo != "Todos":

        grupo_normalizado = normalizar_grupo(
            grupo
        )

        df_grupo = df_liga[
            df_liga["Grupo_filtro"]
            .apply(
                normalizar_grupo
            )
            .eq(
                grupo_normalizado
            )
        ].copy()

    else:

        df_grupo = df_liga.copy()

else:

    # La competición no usa grupos: no mostramos ningún selector.
    grupo = None
    df_grupo = df_liga.copy()

    # En este caso el equipo ocupa todo el espacio derecho.
    with col_filtros_dependientes:
        col_equipo = st.container()


# ============================================================
# EQUIPO
# ============================================================

equipos = sorted(
    [
        equipo
        for equipo in (
            df_grupo["Equipo_filtro"]
            .dropna()
            .astype(str)
            .unique()
        )
        if equipo.strip()
        and equipo.lower() != "nan"
    ]
)


if not equipos:

    st.warning(
        "No hay equipos disponibles para estos filtros."
    )

    st.stop()


with col_equipo:

    equipo = st.selectbox(
        "🛡️ Equipo",
        equipos
    )


# ============================================================
# OBTENER PLANTILLA
# ============================================================

plantilla = (
    df_grupo[
        df_grupo["Equipo_filtro"]
        .eq(equipo)
    ]

    .sort_values(
        [
            "Titular",
            "Jugados",
            "Total_Goles"
        ],

        ascending=[
            False,
            False,
            False
        ],

        na_position="last"
    )

    .drop_duplicates(
        subset=["Jugador"]
    )

    .reset_index(
        drop=True
    )
)


# ============================================================
# DATOS DE LA COMPETICIÓN
# ============================================================

posicion_equipo = primer_valor(
    plantilla["Posicion_equipo"]
)

puntos_equipo = primer_valor(
    plantilla["Puntos_equipo"]
)

grupo_equipo = primer_valor(
    plantilla["Grupo_filtro"]
)


# ============================================================
# El equipo y la formación se muestran directamente en el campo
# ============================================================


# ============================================================
# SIDEBAR
# ALINEACIÓN
# ============================================================

st.sidebar.header(
    "Alineación"
)


formacion = st.sidebar.selectbox(
    "Formación",
    list(
        FORMACIONES.keys()
    )
)


# ============================================================
# JUGADORES DISPONIBLES
# ============================================================

lista_jugadores = (
    plantilla["Jugador"]
    .dropna()
    .astype(str)
    .drop_duplicates()
    .tolist()
)


puestos = FORMACIONES[
    formacion
]


# ============================================================
# CLAVES DE CADA PUESTO
# ============================================================

claves_puestos = {}


for puesto, x, y in puestos:

    clave = (
        f"alineacion_"
        f"{temporada}_"
        f"{liga}_"
        f"{grupo if grupo is not None else 'SIN_GRUPO'}_"
        f"{equipo}_"
        f"{formacion}_"
        f"{puesto}"
    )

    claves_puestos[
        puesto
    ] = clave


# ============================================================
# LIMPIAR DUPLICADOS QUE PUDIERAN HABER QUEDADO
# DE UNA VERSIÓN ANTERIOR
# ============================================================

ya_usados = set()


for puesto, _, _ in puestos:

    clave = claves_puestos[
        puesto
    ]

    valor = st.session_state.get(
        clave,
        SIN_SELECCION
    )

    if (
        valor != SIN_SELECCION
        and valor in ya_usados
    ):

        st.session_state[
            clave
        ] = SIN_SELECCION

    elif valor != SIN_SELECCION:

        ya_usados.add(
            valor
        )


# ============================================================
# BOTÓN LIMPIAR ALINEACIÓN
# ============================================================

if st.sidebar.button(
    "🗑️ Limpiar alineación",
    width="stretch"
):

    for puesto, _, _ in puestos:

        clave = claves_puestos[
            puesto
        ]

        st.session_state[
            clave
        ] = SIN_SELECCION

    st.rerun()


# ============================================================
# SELECTORES
# SIN JUGADORES REPETIDOS
# ============================================================

seleccionados = {}


for puesto, x, y in puestos:

    clave = claves_puestos[
        puesto
    ]


    # --------------------------------------------------------
    # JUGADOR ACTUAL EN ESTE PUESTO
    # --------------------------------------------------------

    actual = st.session_state.get(
        clave,
        SIN_SELECCION
    )


    # --------------------------------------------------------
    # JUGADORES YA USADOS EN OTROS PUESTOS
    # --------------------------------------------------------

    usados_en_otros = set()


    for otro_puesto, _, _ in puestos:

        if otro_puesto == puesto:
            continue


        otra_clave = claves_puestos[
            otro_puesto
        ]


        otro_jugador = st.session_state.get(
            otra_clave,
            SIN_SELECCION
        )


        if otro_jugador != SIN_SELECCION:

            usados_en_otros.add(
                otro_jugador
            )


    # --------------------------------------------------------
    # OPCIONES DISPONIBLES
    # --------------------------------------------------------

    opciones = [
        SIN_SELECCION
    ]


    for jugador in lista_jugadores:

        if (
            jugador not in usados_en_otros
            or jugador == actual
        ):

            opciones.append(
                jugador
            )


    # --------------------------------------------------------
    # SI EL VALOR ACTUAL YA NO ES VÁLIDO
    # --------------------------------------------------------

    if actual not in opciones:

        actual = SIN_SELECCION

        st.session_state[
            clave
        ] = SIN_SELECCION


    # --------------------------------------------------------
    # SELECTBOX
    # --------------------------------------------------------

    indice = opciones.index(
        actual
    )


    jugador = st.sidebar.selectbox(
        puesto,
        opciones,
        index=indice,
        key=clave
    )


    if jugador != SIN_SELECCION:

        seleccionados[
            puesto
        ] = jugador


# ============================================================
# CONTADOR
# ============================================================

st.sidebar.caption(
    f"{len(seleccionados)}/11 jugadores seleccionados"
)


# ============================================================
# CAMPO DE FÚTBOL
# ============================================================

pitch = VerticalPitch(
    pitch_type="statsbomb",
    pitch_color="#2f7d4f",
    line_color="white",
    linewidth=2,
    goal_type="box",
    pad_top=3,
    pad_bottom=3
)


fig, ax = pitch.draw(
    figsize=(5, 7)
)


fig.patch.set_facecolor(
    "#0e1117"
)


ax.set_title(
    f"{equipo}\n{formacion}",
    color="white",
    fontsize=14,
    fontweight="bold",
    pad=10
)


# ============================================================
# DIBUJAR POSICIONES Y JUGADORES
# ============================================================

for puesto, x, y in puestos:

    jugador = seleccionados.get(
        puesto
    )


    # --------------------------------------------------------
    # CÍRCULO
    # --------------------------------------------------------

    pitch.scatter(
        x,
        y,
        ax=ax,
        s=650,
        color=(
            "#2066b3"
            if jugador
            else "#555555"
        ),
        edgecolors="white",
        linewidth=2,
        zorder=3
    )


    # --------------------------------------------------------
    # POSICIÓN
    # --------------------------------------------------------

    pitch.annotate(
        puesto,
        xy=(x, y),
        ax=ax,
        ha="center",
        va="center",
        color="white",
        fontsize=7.5,
        fontweight="bold",
        zorder=4
    )


    # --------------------------------------------------------
    # NOMBRE DEL JUGADOR
    # --------------------------------------------------------

    if jugador:

        pitch.annotate(
            nombre_corto(
                jugador
            ),
            xy=(x, y),
            xytext=(0, -24),
            textcoords="offset points",
            ax=ax,
            ha="center",
            va="top",
            color="white",
            fontsize=7.5,
            fontweight="bold",
            bbox=dict(
                boxstyle="round,pad=0.22",
                fc="#111111",
                ec="none",
                alpha=0.88
            ),
            zorder=5
        )


# ============================================================
# MOSTRAR CAMPO
# ============================================================

espacio_izq, columna_campo, espacio_der = st.columns(
    [1.15, 1.45, 1.15]
)


with columna_campo:

    st.pyplot(
        fig,
        width="stretch"
    )


plt.close(
    fig
)


# ============================================================
# XI SELECCIONADO
# ============================================================

if seleccionados:

    st.subheader(
        "XI seleccionado"
    )


    filas = []


    for puesto, _, _ in puestos:

        if puesto not in seleccionados:
            continue


        jugador = seleccionados[
            puesto
        ]


        coincidencias = plantilla[
            plantilla["Jugador"]
            .eq(jugador)
        ]


        if coincidencias.empty:
            continue


        fila = coincidencias.iloc[
            0
        ].copy()


        fila[
            "Puesto"
        ] = puesto


        filas.append(
            fila
        )


    if filas:

        xi = pd.DataFrame(
            filas
        )


        columnas_xi = [
            "Puesto",
            "Jugador",
            "Ano_nacimiento",
            "Jugados",
            "Titular",
            "Suplente",
            "Total_Goles",
            "Amarillas",
            "Rojas"
        ]


        columnas_xi = [
            columna

            for columna in columnas_xi

            if columna in xi.columns
        ]


        tabla_xi = xi[
            columnas_xi
        ].copy()

        st.dataframe(
            tabla_xi,
            width="stretch",
            hide_index=True
        )

        st.download_button(
            "📥 Descargar Excel (.xlsx)",
            data=dataframe_a_excel(tabla_xi, "XI seleccionado"),
            file_name=f"XI_{nombre_archivo_seguro(equipo)}_{nombre_archivo_seguro(temporada)}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="guardar_xi_excel"
        )

# ============================================================
# PLANTILLA COMPLETA
# ============================================================

with st.expander(
    f"📋 Plantilla completa ({len(plantilla)} jugadores)"
):

    columnas_tabla = [
        "Jugador",
        "Ano_nacimiento",
        "Estado",
        "Convocados",
        "Titular",
        "Suplente",
        "Jugados",
        "Total_Goles",
        "Media_Goles",
        "Amarillas",
        "Rojas",
        "Doble_Amarilla"
    ]


    columnas_tabla = [
        columna

        for columna in columnas_tabla

        if columna in plantilla.columns
    ]


    tabla_plantilla = plantilla[
        columnas_tabla
    ].copy()

    st.dataframe(
        tabla_plantilla,
        width="stretch",
        hide_index=True
    )

    st.download_button(
        "📥 Descargar Excel (.xlsx)",
        data=dataframe_a_excel(tabla_plantilla, "Plantilla"),
        file_name=f"plantilla_{nombre_archivo_seguro(equipo)}_{nombre_archivo_seguro(temporada)}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="guardar_plantilla_excel"
    )

# ============================================================
# CAMBIAR ARCHIVO CSV
# Se deja al final, como se solicitó.
# ============================================================

st.divider()

with st.expander(
    "📁 Cambiar archivo CSV",
    expanded=False
):

    st.caption(
        "El archivo cargado sustituye temporalmente a "
        "jugadores_rfaf.csv mientras la app esté abierta."
    )

    st.file_uploader(
        "Selecciona otro CSV",
        type=["csv"],
        key="archivo_csv"
    )
