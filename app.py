# A partir de aquí empieza la aplicación Streamlit normal.
import re
import os
from io import BytesIO

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from mplsoccer import VerticalPitch
from supabase import create_client, Client


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
# CARGAR DATOS (SUPABASE / CSV FALLBACK)
# ============================================================

@st.cache_data(ttl=300)
def cargar_datos(archivo=None):
    df = None

    # 1. Intentar cargar desde Supabase si hay credenciales configuradas
    supabase_url = st.secrets.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    supabase_key = st.secrets.get("SUPABASE_KEY") or os.environ.get("SUPABASE_KEY")

    if supabase_url and supabase_key and archivo is None:
        try:
            supabase: Client = create_client(supabase_url, supabase_key)
            # Paginación para obtener todos los registros de la tabla
            all_rows = []
            offset = 0
            limit = 1000
            while True:
                response = supabase.table("jugadores_rfaf").select("*").range(offset, offset + limit - 1).execute()
                rows = response.data
                if not rows:
                    break
                all_rows.extend(rows)
                if len(rows) < limit:
                    break
                offset += limit

            if all_rows:
                df = pd.DataFrame(all_rows)
                # Renombrar columnas de minúsculas de la BD a mayúsculas esperadas por app.py
                mapeo_columnas = {
                    "temporada": "Temporada",
                    "categoria_origen": "Categoria_origen",
                    "competicion_origen": "Competicion_origen",
                    "grupo_origen": "Grupo_origen",
                    "equipo_origen": "Equipo_origen",
                    "jugador": "Jugador",
                    "ano_nacimiento": "Año_nacimiento",
                    "club_en_temporada": "Club_en_temporada",
                    "estado": "Estado",
                    "convocados": "Convocados",
                    "titular": "Titular",
                    "suplente": "Suplente",
                    "jugados": "Jugados",
                    "total_goles": "Total_Goles",
                    "media_goles": "Media_Goles",
                    "amarillas": "Amarillas",
                    "rojas": "Rojas",
                    "doble_amarilla": "Doble_Amarilla",
                    "competiciones_temporada": "Competiciones_temporada"
                }
                df.rename(columns=mapeo_columnas, inplace=True)
        except Exception as e:
            st.warning(f"No se pudo conectar a Supabase, recurriendo a CSV local: {e}")

    # 2. Si no hay Supabase o falló, recurrir al archivo local/subido
    if df is None:
        if archivo is not None:
            if hasattr(archivo, "seek"):
                archivo.seek(0)
            df = pd.read_csv(archivo, encoding="utf-8-sig")
        else:
            df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")

    columnas_numericas = [
        "Año_nacimiento",
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
            df[columna] = pd.to_numeric(df[columna], errors="coerce")

    return df


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def texto_seguro(valor):
    if pd.isna(valor):
        return ""
    return str(valor).strip()


def normalizar_texto(valor):
    return re.sub(r"\s+", " ", texto_seguro(valor).upper()).strip()


def normalizar_grupo(valor):
    texto = normalizar_texto(valor)
    coincidencia = re.fullmatch(r"GRUPO\s+(\d+)", texto)
    if coincidencia:
        return coincidencia.group(1)
    return texto


def objetivo_por_competicion(competicion, categoria=None):
    competicion_normalizada = normalizar_texto(competicion)
    categoria_normalizada = normalizar_texto(categoria)

    for objetivo in OBJETIVOS:
        if categoria is not None and normalizar_texto(objetivo["categoria"]) != categoria_normalizada:
            continue
        aliases = {normalizar_texto(alias) for alias in objetivo["competicion"]}
        if competicion_normalizada in aliases:
            return objetivo
    return None


def grupos_configurados(categoria, competicion):
    objetivo = objetivo_por_competicion(competicion, categoria)
    if objetivo is None:
        return []
    return objetivo["grupos"]


# ============================================================
# PROCESAR COMPETICIONES TEMPORADA
# ============================================================

def separar_competiciones(df):
    registros = []
    patron = re.compile(
        r"^\s*(.*?)\s*,\s*(.*?)\s*·\s*(.*?)\s*,\s*Posicion\s*,\s*([^,]*)\s*,\s*Puntos\s*,\s*([^,]*)",
        re.IGNORECASE
    )

    for _, fila in df.iterrows():
        nuevo_origen = fila.to_dict()
        equipo_origen = texto_seguro(fila.get("Equipo_origen"))
        club_temporada = texto_seguro(fila.get("Club_en_temporada"))

        equipo_base = equipo_origen if equipo_origen else club_temporada
        liga_origen = texto_seguro(fila.get("Competicion_origen"))
        grupo_origen = texto_seguro(fila.get("Grupo_origen"))

        nuevo_origen["Equipo_filtro"] = equipo_base
        nuevo_origen["Liga_filtro"] = liga_origen
        nuevo_origen["Grupo_filtro"] = grupo_origen
        nuevo_origen["Posicion_equipo"] = ""
        nuevo_origen["Puntos_equipo"] = ""

        objetivo_origen = objetivo_por_competicion(liga_origen, fila.get("Categoria_origen"))
        if objetivo_origen is None:
            objetivo_origen = objetivo_por_competicion(liga_origen)

        nuevo_origen["Categoria_filtro"] = (
            objetivo_origen["categoria"] if objetivo_origen is not None else texto_seguro(fila.get("Categoria_origen"))
        )
        nuevo_origen["Es_objetivo"] = objetivo_origen is not None

        raw = fila.get("Competiciones_temporada")
        bloques = []
        if pd.notna(raw):
            texto = str(raw).strip()
            if texto:
                bloques = [bloque.strip() for bloque in texto.split("||") if bloque.strip()]

        registros_adicionales = []
        for bloque in bloques:
            coincidencia = patron.search(bloque)
            if not coincidencia:
                continue

            equipo = coincidencia.group(1).strip()
            liga = coincidencia.group(2).strip()
            grupo = coincidencia.group(3).strip()
            posicion = coincidencia.group(4).strip()
            puntos = coincidencia.group(5).strip()

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

            objetivo = objetivo_por_competicion(liga)
            nuevo["Categoria_filtro"] = objetivo["categoria"] if objetivo is not None else ""
            nuevo["Es_objetivo"] = objetivo is not None
            registros_adicionales.append(nuevo)

        registros.append(nuevo_origen)
        registros.extend(registros_adicionales)

    return pd.DataFrame(registros)


# ============================================================
# FORMACIONES
# ============================================================

FORMACIONES = {
    "4-3-3": [
        ("POR", 8, 40),
        ("LI", 30, 10), ("DFC-I", 27, 30), ("DFC-D", 27, 50), ("LD", 30, 70),
        ("MC-I", 58, 20), ("MCD", 53, 40), ("MC-D", 58, 60),
        ("EI", 90, 13), ("DC", 100, 40), ("ED", 90, 67),
    ],
    "4-2-3-1": [
        ("POR", 8, 40),
        ("LI", 30, 10), ("DFC-I", 27, 30), ("DFC-D", 27, 50), ("LD", 30, 70),
        ("MCD-I", 52, 29), ("MCD-D", 52, 51),
        ("EI", 76, 14), ("MCO", 79, 40), ("ED", 76, 66),
        ("DC", 100, 40),
    ],
    "4-4-2": [
        ("POR", 8, 40),
        ("LI", 30, 10), ("DFC-I", 27, 30), ("DFC-D", 27, 50), ("LD", 30, 70),
        ("MI", 60, 10), ("MC-I", 58, 30), ("MC-D", 58, 50), ("MD", 60, 70),
        ("DC-I", 96, 28), ("DC-D", 96, 52),
    ],
    "3-5-2": [
        ("POR", 8, 40),
        ("DFC-I", 30, 20), ("DFC", 27, 40), ("DFC-D", 30, 60),
        ("CAI", 55, 8),
        ("MC-I", 58, 26), ("MCD", 53, 40), ("MC-D", 58, 54),
        ("CAD", 55, 72),
        ("DC-I", 96, 29), ("DC-D", 96, 51),
    ],
    "3-4-3": [
        ("POR", 8, 40),
        ("DFC-I", 30, 20), ("DFC", 27, 40), ("DFC-D", 30, 60),
        ("MI", 58, 10), ("MC-I", 56, 30), ("MC-D", 56, 50), ("MD", 58, 70),
        ("EI", 88, 15), ("DC", 98, 40), ("ED", 88, 65),
    ],
    "5-3-2": [
        ("POR", 8, 40),
        ("CAI", 34, 7), ("DFC-I", 28, 23), ("DFC", 26, 40), ("DFC-D", 28, 57), ("CAD", 34, 73),
        ("MC-I", 59, 23), ("MC", 55, 40), ("MC-D", 59, 57),
        ("DC-I", 96, 29), ("DC-D", 96, 51),
    ]
}


def nombre_corto(nombre):
    if not isinstance(nombre, str):
        return ""
    nombre = nombre.strip()
    partes = [parte.strip() for parte in nombre.split(",")]

    if len(partes) >= 3 and partes[0].isdigit():
        dorsal = partes[0]
        apellidos = partes[1]
        primer_apellido = apellidos.split()[0].title() if apellidos else ""
        return f"{dorsal}\n{primer_apellido}"

    if len(partes) >= 2:
        apellidos = partes[0]
        primer_apellido = apellidos.split()[0].title() if apellidos else ""
        return primer_apellido

    return nombre.title()


def primer_valor(serie):
    for valor in serie:
        texto = texto_seguro(valor)
        if texto:
            return texto
    return ""


def nombre_archivo_seguro(texto):
    texto = texto_seguro(texto)
    texto = re.sub(r'[<>:"/\\|?*]+', "_", texto)
    return texto.strip(" ._") or "datos"


def dataframe_a_excel(df_exportar, nombre_hoja="Datos"):
    buffer = BytesIO()
    nombre_hoja = str(nombre_hoja)[:31] or "Datos"
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_exportar.to_excel(writer, index=False, sheet_name=nombre_hoja)
    buffer.seek(0)
    return buffer.getvalue()


# ============================================================
# CARGAR ARCHIVO
# ============================================================

archivo_subido = st.session_state.get("archivo_csv", None)

try:
    df_original = cargar_datos(archivo_subido)
except Exception as e:
    st.error(f"Error al cargar los datos: {e}")
    st.info("Coloca jugadores_rfaf.csv o configura tus credenciales de Supabase en Streamlit Secrets.")
    st.file_uploader("Selecciona un CSV de respaldo", type=["csv"], key="archivo_csv")
    st.stop()


# ============================================================
# PROCESAR DATOS
# ============================================================

df = separar_competiciones(df_original)
df_objetivos = df[df["Es_objetivo"].fillna(False)].copy()


# ============================================================
# EXPLORADOR DE TODA LA BASE DE DATOS
# ============================================================

st.subheader("Base de datos de jugadores")

def opciones_texto(serie):
    return sorted({texto_seguro(valor) for valor in serie if texto_seguro(valor)})


def filtrar_por_texto(df_base, columna, valor):
    if valor == "Todos" or columna not in df_base.columns:
        return df_base
    return df_base[
        df_base[columna].fillna("").astype(str).str.strip().eq(str(valor).strip())
    ].copy()


anos_disponibles = sorted(
    pd.to_numeric(df_original["Año_nacimiento"], errors="coerce")
    .dropna().astype(int).unique().tolist()
)

col_anio, col_temporada_bd, col_categoria_bd = st.columns(3)

with col_anio:
    anio_bd = st.selectbox("Año de nacimiento", ["Todos"] + anos_disponibles, key="bd_anio")

df_explorador = df_original.copy()

if anio_bd != "Todos":
    ano_numerico = pd.to_numeric(df_explorador["Año_nacimiento"], errors="coerce")
    df_explorador = df_explorador[ano_numerico.eq(int(anio_bd))].copy()

with col_temporada_bd:
    temporadas_bd = opciones_texto(df_explorador["Temporada"]) if "Temporada" in df_explorador.columns else []
    temporada_bd = st.selectbox("Temporada", ["Todos"] + temporadas_bd, key="bd_temporada")

df_explorador = filtrar_por_texto(df_explorador, "Temporada", temporada_bd)

with col_categoria_bd:
    categorias_bd = opciones_texto(df_explorador["Categoria_origen"]) if "Categoria_origen" in df_explorador.columns else []
    categoria_bd = st.selectbox("Categoría", ["Todos"] + categorias_bd, key="bd_categoria")

df_explorador = filtrar_por_texto(df_explorador, "Categoria_origen", categoria_bd)

col_competicion_bd, col_grupo_bd, col_equipo_bd = st.columns(3)

with col_competicion_bd:
    competiciones_bd = opciones_texto(df_explorador["Competicion_origen"]) if "Competicion_origen" in df_explorador.columns else []
    competicion_bd = st.selectbox("Competición", ["Todos"] + competiciones_bd, key="bd_competicion")

df_explorador = filtrar_por_texto(df_explorador, "Competicion_origen", competicion_bd)

with col_grupo_bd:
    grupos_bd = opciones_texto(df_explorador["Grupo_origen"]) if "Grupo_origen" in df_explorador.columns else []
    grupo_bd = st.selectbox("Grupo", ["Todos"] + grupos_bd, key="bd_grupo")

df_explorador = filtrar_por_texto(df_explorador, "Grupo_origen", grupo_bd)

with col_equipo_bd:
    equipos_bd = opciones_texto(df_explorador["Equipo_origen"]) if "Equipo_origen" in df_explorador.columns else []
    equipo_bd = st.selectbox("Equipo", ["Todos"] + equipos_bd, key="bd_equipo")

df_explorador = filtrar_por_texto(df_explorador, "Equipo_origen", equipo_bd)

busqueda_jugador_bd = st.text_input("Jugador", placeholder="Escribe nombre o apellido...", key="bd_jugador_busqueda")

if busqueda_jugador_bd.strip() and "Jugador" in df_explorador.columns:
    df_explorador = df_explorador[
        df_explorador["Jugador"].fillna("").astype(str).str.contains(
            busqueda_jugador_bd.strip(), case=False, na=False, regex=False
        )
    ].copy()

columnas_bd = [
    "Jugador", "Año_nacimiento", "Temporada", "Categoria_origen", "Competicion_origen",
    "Grupo_origen", "Equipo_origen", "Club_en_temporada", "Estado", "Convocados",
    "Titular", "Suplente", "Jugados", "Total_Goles", "Media_Goles", "Amarillas",
    "Rojas", "Doble_Amarilla"
]
columnas_bd = [columna for columna in columnas_bd if columna in df_explorador.columns]

st.caption(f"{len(df_explorador):,} registros mostrados".replace(",", "."))
tabla_bd = df_explorador[columnas_bd].copy()

st.dataframe(tabla_bd, width="stretch", height=430, hide_index=True)

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
st.subheader("Selección de equipo para la alineación")

if df_objetivos.empty:
    st.warning("El archivo no contiene competiciones incluidas en OBJETIVOS.")
    st.stop()

temporadas = sorted(df_objetivos["Temporada"].dropna().astype(str).unique(), reverse=True)
col_temporada, col_categoria = st.columns([1.2, 2])

with col_temporada:
    temporada = st.selectbox("📅 Temporada", temporadas)

df_temporada = df_objetivos[df_objetivos["Temporada"].astype(str).eq(temporada)].copy()

orden_categorias = []
for objetivo in OBJETIVOS:
    if objetivo["categoria"] not in orden_categorias:
        orden_categorias.append(objetivo["categoria"])

categorias_presentes = {texto_seguro(valor) for valor in df_temporada["Categoria_filtro"].dropna().unique() if texto_seguro(valor)}
categorias = [cat for cat in orden_categorias if cat in categorias_presentes]

with col_categoria:
    categoria = st.selectbox("🏷️ Categoría", categorias)

df_categoria = df_temporada[df_temporada["Categoria_filtro"].eq(categoria)].copy()

ligas_reales = [liga for liga in df_categoria["Liga_filtro"].dropna().astype(str).unique() if liga.strip() and liga.lower() != "nan"]
ligas = []

for objetivo in OBJETIVOS:
    if objetivo["categoria"] != categoria:
        continue
    for alias in objetivo["competicion"]:
        coincidencias = [liga_real for liga_real in ligas_reales if normalizar_texto(liga_real) == normalizar_texto(alias)]
        for coincidencia in coincidencias:
            if coincidencia not in ligas:
                ligas.append(coincidencia)

if not ligas:
    st.warning("No hay competiciones disponibles para esta categoría en la temporada seleccionada.")
    st.stop()

col_liga, col_filtros_dependientes = st.columns([2.2, 3.9])

with col_liga:
    liga = st.selectbox("🏆 Competición", ligas)

df_liga = df_categoria[df_categoria["Liga_filtro"].eq(liga)].copy()

grupos_configuracion = grupos_configurados(categoria, liga)
tiene_selector_grupo = any(grupo_configurado is not None for grupo_configurado in grupos_configuracion)

if tiene_selector_grupo:
    grupos_permitidos = [g for g in grupos_configuracion if g is not None]
    grupos_reales = [g for g in df_liga["Grupo_filtro"].dropna().astype(str).unique() if g.strip() and g.lower() != "nan"]
    grupos_disponibles = [g_conf for g_conf in grupos_permitidos if any(normalizar_grupo(g_real) == normalizar_grupo(g_conf) for g_real in grupos_reales)]
    grupos = ["Todos"] + grupos_disponibles

    with col_filtros_dependientes:
        col_grupo, col_equipo = st.columns([1.7, 2.2])
        with col_grupo:
            grupo = st.selectbox("📍 Grupo", grupos)

    if grupo != "Todos":
        df_grupo = df_liga[df_liga["Grupo_filtro"].apply(normalizar_grupo).eq(normalizar_grupo(grupo))].copy()
    else:
        df_grupo = df_liga.copy()
else:
    grupo = None
    df_grupo = df_liga.copy()
    with col_filtros_dependientes:
        col_equipo = st.container()

equipos = sorted([eq for eq in df_grupo["Equipo_filtro"].dropna().astype(str).unique() if eq.strip() and eq.lower() != "nan"])

if not equipos:
    st.warning("No hay equipos disponibles para estos filtros.")
    st.stop()

with col_equipo:
    equipo = st.selectbox("🛡️ Equipo", equipos)

plantilla = (
    df_grupo[df_grupo["Equipo_filtro"].eq(equipo)]
    .sort_values(["Titular", "Jugados", "Total_Goles"], ascending=[False, False, False], na_position="last")
    .drop_duplicates(subset=["Jugador"])
    .reset_index(drop=True)
)

posicion_equipo = primer_valor(plantilla["Posicion_equipo"])
puntos_equipo = primer_valor(plantilla["Puntos_equipo"])
grupo_equipo = primer_valor(plantilla["Grupo_filtro"])

# ============================================================
# ALINEACIÓN
# ============================================================

st.sidebar.header("Alineación")
formacion = st.sidebar.selectbox("Formación", list(FORMACIONES.keys()))

lista_jugadores = plantilla["Jugador"].dropna().astype(str).drop_duplicates().tolist()
puestos = FORMACIONES[formacion]

claves_puestos = {}
for puesto, x, y in puestos:
    clave = f"alineacion_{temporada}_{liga}_{grupo if grupo is not None else 'SIN_GRUPO'}_{equipo}_{formacion}_{puesto}"
    claves_puestos[puesto] = clave

ya_usados = set()
for puesto, _, _ in puestos:
    clave = claves_puestos[puesto]
    valor = st.session_state.get(clave, SIN_SELECCION)
    if valor != SIN_SELECCION and valor in ya_usados:
        st.session_state[clave] = SIN_SELECCION
    elif valor != SIN_SELECCION:
        ya_usados.add(valor)

if st.sidebar.button("🗑️ Limpiar alineación", width="stretch"):
    for puesto, _, _ in puestos:
        st.session_state[claves_puestos[puesto]] = SIN_SELECCION
    st.rerun()

seleccionados = {}
for puesto, x, y in puestos:
    clave = claves_puestos[puesto]
    actual = st.session_state.get(clave, SIN_SELECCION)
    usados_en_otros = {st.session_state.get(claves_puestos[o_puesto], SIN_SELECCION) for o_puesto, _, _ in puestos if o_puesto != puesto and st.session_state.get(claves_puestos[o_puesto], SIN_SELECCION) != SIN_SELECCION}

    opciones = [SIN_SELECCION] + [j for j in lista_jugadores if j not in usados_en_otros or j == actual]

    if actual not in opciones:
        actual = SIN_SELECCION
        st.session_state[clave] = SIN_SELECCION

    jugador = st.sidebar.selectbox(puesto, opciones, index=opciones.index(actual), key=clave)
    if jugador != SIN_SELECCION:
        seleccionados[puesto] = jugador

st.sidebar.caption(f"{len(seleccionados)}/11 jugadores seleccionados")

# ============================================================
# CAMPO DE FÚTBOL
# ============================================================

pitch = VerticalPitch(
    pitch_type="statsbomb", pitch_color="#2f7d4f", line_color="white",
    linewidth=2, goal_type="box", pad_top=3, pad_bottom=3
)

fig, ax = pitch.draw(figsize=(5, 7))
fig.patch.set_facecolor("#0e1117")
ax.set_title(f"{equipo}\n{formacion}", color="white", fontsize=14, fontweight="bold", pad=10)

for puesto, x, y in puestos:
    jugador = seleccionados.get(puesto)
    pitch.scatter(
        x, y, ax=ax, s=650,
        color="#2066b3" if jugador else "#555555",
        edgecolors="white", linewidth=2, zorder=3
    )
    pitch.annotate(
        puesto, xy=(x, y), ax=ax, ha="center", va="center",
        color="white", fontsize=7.5, fontweight="bold", zorder=4
    )
    if jugador:
        pitch.annotate(
            nombre_corto(jugador), xy=(x, y), xytext=(0, -24), textcoords="offset points",
            ax=ax, ha="center", va="top", color="white", fontsize=7.5, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.22", fc="#111111", ec="none", alpha=0.88), zorder=5
        )

espacio_izq, columna_campo, espacio_der = st.columns([1.15, 1.45, 1.15])
with columna_campo:
    st.pyplot(fig, width="stretch")
plt.close(fig)

# ============================================================
# XI SELECCIONADO Y PLANTILLA COMPLETA
# ============================================================

if seleccionados:
    st.subheader("XI seleccionado")
    filas = []
    for puesto, _, _ in puestos:
        if puesto not in seleccionados:
            continue
        jugador = seleccionados[puesto]
        coincidencias = plantilla[plantilla["Jugador"].eq(jugador)]
        if coincidencias.empty:
            continue
        fila = coincidencias.iloc[0].copy()
        fila["Puesto"] = puesto
        filas.append(fila)

    if filas:
        xi = pd.DataFrame(filas)
        columnas_xi = ["Puesto", "Jugador", "Año_nacimiento", "Jugados", "Titular", "Suplente", "Total_Goles", "Amarillas", "Rojas"]
        columnas_xi = [col for col in columnas_xi if col in xi.columns]
        tabla_xi = xi[columnas_xi].copy()

        st.dataframe(tabla_xi, width="stretch", hide_index=True)
        st.download_button(
            "📥 Descargar Excel (.xlsx)",
            data=dataframe_a_excel(tabla_xi, "XI seleccionado"),
            file_name=f"XI_{nombre_archivo_seguro(equipo)}_{nombre_archivo_seguro(temporada)}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="guardar_xi_excel"
        )

with st.expander(f"📋 Plantilla completa ({len(plantilla)} jugadores)"):
    columnas_tabla = ["Jugador", "Año_nacimiento", "Estado", "Convocados", "Titular", "Suplente", "Jugados", "Total_Goles", "Media_Goles", "Amarillas", "Rojas", "Doble_Amarilla"]
    columnas_tabla = [col for col in columnas_tabla if col in plantilla.columns]
    tabla_plantilla = plantilla[columnas_tabla].copy()

    st.dataframe(tabla_plantilla, width="stretch", hide_index=True)
    st.download_button(
        "📥 Descargar Excel (.xlsx)",
        data=dataframe_a_excel(tabla_plantilla, "Plantilla"),
        file_name=f"plantilla_{nombre_archivo_seguro(equipo)}_{nombre_archivo_seguro(temporada)}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="guardar_plantilla_excel"
    )

st.divider()
with st.expander("📁 Cambiar archivo CSV", expanded=False):
    st.caption("El archivo cargado sustituye temporalmente los datos de la app mientras esté abierta.")
    st.file_uploader("Selecciona otro CSV", type=["csv"], key="archivo_csv")