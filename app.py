#python -m streamlit run app.py   pip install streamlit pandas matplotlib mplsoccer

import re
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

CSV_PATH = "jugadores_rfaf.csv"

SIN_SELECCION = "— Sin seleccionar —"


# ============================================================
# CARGAR CSV
# ============================================================

@st.cache_data
def cargar_datos(archivo):

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


# ============================================================
# PROCESAR COMPETICIONES_TEMPORADA
# ============================================================

def separar_competiciones(df):

    """
    Ejemplo:

    ROBRES-C.D. , TERCERA FEDERACIÓN · 17,
    Posicion, 14°, Puntos, 1,

    Se convierte en:

    Equipo_filtro = ROBRES-C.D.
    Liga_filtro = TERCERA FEDERACIÓN
    Grupo_filtro = 17
    Posicion_equipo = 14°
    Puntos_equipo = 1
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

        encontrados = 0

        # ----------------------------------------------------
        # COMPETICIONES EN LA TEMPORADA
        # ----------------------------------------------------

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

            nuevo = fila.to_dict()

            nuevo["Equipo_filtro"] = equipo
            nuevo["Liga_filtro"] = liga
            nuevo["Grupo_filtro"] = grupo
            nuevo["Posicion_equipo"] = posicion
            nuevo["Puntos_equipo"] = puntos

            registros.append(
                nuevo
            )

            encontrados += 1

        # ----------------------------------------------------
        # FALLBACK
        # ----------------------------------------------------

        if encontrados == 0:

            nuevo = fila.to_dict()

            club_temporada = texto_seguro(
                fila.get("Club_en_temporada")
            )

            equipo_origen = texto_seguro(
                fila.get("Equipo_origen")
            )

            if club_temporada:
                equipo = club_temporada
            else:
                equipo = equipo_origen

            nuevo["Equipo_filtro"] = equipo

            nuevo["Liga_filtro"] = texto_seguro(
                fila.get("Competicion_origen")
            )

            nuevo["Grupo_filtro"] = texto_seguro(
                fila.get("Grupo_origen")
            )

            nuevo["Posicion_equipo"] = ""
            nuevo["Puntos_equipo"] = ""

            registros.append(
                nuevo
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
# TÍTULO
# ============================================================

st.title(
    "⚽ Equipos de fútbol"
)

st.caption(
    "Consulta equipos, jugadores, estadísticas y alineaciones "
    "por temporada y competición."
)


# ============================================================
# ARCHIVO CSV
# ============================================================

with st.expander(
    "📁 Cambiar archivo CSV",
    expanded=False
):

    archivo_subido = st.file_uploader(
        "Selecciona otro CSV",
        type=["csv"]
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
        "Coloca jugadores_rfaf.csv "
        "en la misma carpeta que app.py."
    )

    st.stop()


# ============================================================
# PROCESAR DATOS
# ============================================================

df = separar_competiciones(
    df_original
)


# ============================================================
# FILTROS SUPERIORES
# ============================================================

col_temporada, col_liga, col_grupo, col_equipo = st.columns(
    [1, 2.2, 1.5, 2.2]
)


# ============================================================
# TEMPORADA
# ============================================================

temporadas = sorted(
    df["Temporada"]
    .dropna()
    .astype(str)
    .unique(),
    reverse=True
)


with col_temporada:

    temporada = st.selectbox(
        "📅 Temporada",
        temporadas
    )


df_temporada = df[
    df["Temporada"]
    .astype(str)
    .eq(temporada)
].copy()


# ============================================================
# COMPETICIÓN
# ============================================================

ligas = sorted(
    [
        liga

        for liga in (
            df_temporada["Liga_filtro"]
            .dropna()
            .astype(str)
            .unique()
        )

        if liga.strip()
        and liga.lower() != "nan"
    ]
)


if not ligas:

    st.warning(
        "No hay competiciones para esta temporada."
    )

    st.stop()


with col_liga:

    liga = st.selectbox(
        "🏆 Competición",
        ligas
    )


df_liga = df_temporada[
    df_temporada["Liga_filtro"]
    .eq(liga)
].copy()


# ============================================================
# GRUPO
# ============================================================

grupos_reales = sorted(
    [
        grupo

        for grupo in (
            df_liga["Grupo_filtro"]
            .dropna()
            .astype(str)
            .unique()
        )

        if grupo.strip()
        and grupo.lower() != "nan"
    ]
)


grupos = [
    "Todos"
] + grupos_reales


with col_grupo:

    grupo = st.selectbox(
        "📍 Grupo",
        grupos
    )


if grupo != "Todos":

    df_grupo = df_liga[
        df_liga["Grupo_filtro"]
        .eq(grupo)
    ].copy()

else:

    df_grupo = df_liga.copy()


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
# CABECERA DEL EQUIPO
# ============================================================

st.divider()


st.subheader(
    f"{equipo} — {temporada}"
)


datos_competicion = [
    liga
]


if grupo_equipo:

    if grupo_equipo.lower().startswith(
        "grupo"
    ):

        datos_competicion.append(
            grupo_equipo
        )

    else:

        datos_competicion.append(
            f"Grupo {grupo_equipo}"
        )


if posicion_equipo:

    datos_competicion.append(
        f"Posición: {posicion_equipo}"
    )


if puntos_equipo:

    datos_competicion.append(
        f"Puntos: {puntos_equipo}"
    )


st.caption(
    " · ".join(
        datos_competicion
    )
)


# ============================================================
# ESTADÍSTICAS DEL EQUIPO
# ============================================================

col1, col2, col3, col4 = st.columns(
    4
)


numero_jugadores = len(
    plantilla
)


apariciones = int(
    plantilla["Jugados"]
    .fillna(0)
    .sum()
)


titularidades = int(
    plantilla["Titular"]
    .fillna(0)
    .sum()
)


goles = int(
    plantilla["Total_Goles"]
    .fillna(0)
    .sum()
)


col1.metric(
    "Jugadores",
    numero_jugadores
)

col2.metric(
    "Apariciones",
    apariciones
)

col3.metric(
    "Titularidades",
    titularidades
)

col4.metric(
    "Goles",
    goles
)


# ============================================================
# SIDEBAR
# ALINEACIÓN
# ============================================================

st.sidebar.header(
    "⚽ Alineación"
)


formacion = st.sidebar.selectbox(
    "Formación",
    list(
        FORMACIONES.keys()
    )
)


st.sidebar.divider()


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
        f"{grupo}_"
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


        st.dataframe(
            xi[
                columnas_xi
            ],
            width="stretch",
            hide_index=True
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


    st.dataframe(
        plantilla[
            columnas_tabla
        ],
        width="stretch",
        hide_index=True
    )