import csv
import json
import re
import subprocess
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

# ============================================================
# CONFIGURACION
# ============================================================

ADB = r".\adb.exe"
MODO_PRUEBA = False
EXTRAER_COMPETICIONES_TEMPORADA = True

TEMPORADAS = ["26-27", "25-26", "24-25"]
TEMPORADAS_HISTORICAS = ["25-26", "24-25"]
JORNADAS_BUSQUEDA = [1, 2]

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
        "grupos": ["1", "2", "3A", "3B", "4"],
    },
    {
        "categoria": "FÚTBOL JUVENIL",
        "competicion": ["1ª JUVENIL", "PRIMERA JUVENIL"],
        "grupos": ["1", "2"],
    },
    {
        "categoria": "FÚTBOL BASE",
        "competicion": ["DIVISIÓN DE HONOR CADETE", "DIVISION DE HONOR CADETE"],
        "grupos": [None],
    },
]

CSV_OUT = Path("jugadores_rfaf.csv")
CHECKPOINT_OUT = Path("checkpoint_rfaf.json")
DEBUG_DIR = Path("debug_rfaf")
TMP_XML_DEVICE = "/sdcard/rfaf_scraper.xml"

# Esperas cortas. Los dumps de UIAutomator son mucho mas costosos que estos sleeps.
DELAY_TAP = 0.12
DELAY_SCREEN = 0.18
DELAY_SEASON = 0.30
DELAY_SCROLL = 0.16

COLUMNAS = [
    "Temporada",
    "Categoria_origen",
    "Competicion_origen",
    "Grupo_origen",
    "Equipo_origen",
    "Jugador",
    "Ano_nacimiento",
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
    "Doble_Amarilla",
    "Competiciones_temporada",
]

DEVICE = None
CSV_FILE = None
CSV_WRITER = None
CSV_KEYS = set()
CHECKPOINT = None
JUGADORES_OK = set()
EQUIPOS_OK = set()
PARTIDOS_OK = set()
GRUPOS_OK = set()
ANON_ROW_MAP = {}

# ============================================================
# TEXTO / CLAVES
# ============================================================

def strip_icons(value):
    value = value or ""
    return "".join(c for c in value if not (0xE000 <= ord(c) <= 0xF8FF)).strip()


def clean_text(value):
    return re.sub(r"\s+", " ", strip_icons(value)).strip()


def canon(value):
    value = clean_text(value).upper().replace("ª", "A").replace("º", "O")
    value = unicodedata.normalize("NFD", value)
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return "".join(c for c in value if c.isalnum())


def key_text(*parts):
    return "|||".join(canon(x) for x in parts)


def player_key(category, competition, group, team, player):
    return key_text(category, competition, group or "", team, player)


def team_player_prefix(category, competition, group, team):
    return key_text(category, competition, group or "", team) + "|||"


def anon_key(category, competition, group, team, fingerprint):
    return key_text(category, competition, group or "", team, "ANON", fingerprint)


def text_of(node):
    return (node.attrib.get("text", "") or "").strip()


def desc_of(node):
    return (node.attrib.get("content-desc", "") or "").strip()


def valid_player_name(value):
    value = clean_text(value)
    if not value:
        return False
    if re.fullmatch(r"\d{2}-\d{2}", value):
        return False
    if re.fullmatch(r"(19|20)\d{2}", value):
        return False
    if canon(value) in {
        "PARTIDOS", "TARJETAS", "COMPETICIONES", "CONVOCADOS", "TITULAR",
        "SUPLENTE", "JUGADOS", "TOTALGOLES", "MEDIAGOLES", "AMARILLAS",
        "ROJAS", "DOBLEAMARILLA", "POSICION", "PUNTOS",
    }:
        return False
    if "SEGUIDOR" in canon(value):
        return False
    # Los nombres de la app normalmente tienen coma; aceptamos tambien nombres largos sin coma.
    letters = sum(ch.isalpha() for ch in value)
    return letters >= 4

# ============================================================
# ADB
# ============================================================

def adb(*args, timeout=25, check=True):
    cmd = [ADB]
    if DEVICE:
        cmd += ["-s", DEVICE]
    cmd += [str(x) for x in args]

    for attempt in range(3):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout
            if attempt < 2:
                time.sleep(0.20)
                continue
            if check:
                raise RuntimeError(result.stderr.strip() or "ADB fallo")
            return result.stdout
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise RuntimeError(
                    "ADB no responde. Comprueba USB, depuracion USB y que el movil este desbloqueado."
                )
            time.sleep(0.20)


def adb_input(*args):
    cmd = [ADB]
    if DEVICE:
        cmd += ["-s", DEVICE]
    cmd += ["shell", "input"] + [str(x) for x in args]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def detect_device():
    global DEVICE
    result = subprocess.run(
        [ADB, "devices"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    devices = [
        line.split("\t")[0].strip()
        for line in result.stdout.splitlines()[1:]
        if "\tdevice" in line
    ]
    if not devices:
        raise RuntimeError("No hay ningun movil conectado por ADB.")
    if len(devices) == 1:
        DEVICE = devices[0]
    else:
        print("\nDispositivos:")
        for i, serial in enumerate(devices, 1):
            print(f"{i}. {serial}")
        DEVICE = devices[int(input("Selecciona dispositivo: ")) - 1]
    print(f"✓ Dispositivo: {DEVICE}")

# ============================================================
# XML / UI
# ============================================================

def ui_root():
    output = adb("exec-out", "uiautomator", "dump", "/dev/tty", check=False)
    start = output.find("<?xml")
    end = output.rfind("</hierarchy>")
    if start >= 0 and end >= 0:
        try:
            return ET.fromstring(output[start:end + len("</hierarchy>")])
        except ET.ParseError:
            pass

    adb("shell", "uiautomator", "dump", TMP_XML_DEVICE)
    output = adb("shell", "cat", TMP_XML_DEVICE)
    start = output.find("<?xml")
    end = output.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise RuntimeError("No se pudo obtener el XML de la pantalla.")
    return ET.fromstring(output[start:end + len("</hierarchy>")])


def save_debug(name, root=None):
    try:
        DEBUG_DIR.mkdir(exist_ok=True)
        root = root or ui_root()
        path = DEBUG_DIR / f"{name}_{int(time.time())}.xml"
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
        print(f"DEBUG XML: {path}")
    except Exception:
        pass


def find_res(root, resource_id):
    return next(
        (n for n in root.iter("node") if n.attrib.get("resource-id") == resource_id),
        None,
    )


def find_all_res(root, resource_id):
    return [n for n in root.iter("node") if n.attrib.get("resource-id") == resource_id]


def parent_map(root):
    return {child: parent for parent in root.iter() for child in parent}


def bounds_tuple(node):
    if node is None:
        return None
    nums = [int(x) for x in re.findall(r"-?\d+", node.attrib.get("bounds", "") or "")]
    return tuple(nums) if len(nums) == 4 else None


def center(node):
    b = bounds_tuple(node)
    if not b:
        return None
    return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)


def tap_node(node):
    pos = center(node)
    if not pos:
        return False
    adb_input("tap", pos[0], pos[1])
    return True


def back():
    adb_input("keyevent", "4")
    time.sleep(DELAY_TAP)


def clickable_ancestor(node, parents, limit=15):
    current = node
    for _ in range(limit):
        if current is None:
            return None
        if current.attrib.get("clickable") == "true":
            return current
        current = parents.get(current)
    return None


def is_inside(node, parents, resource_id, limit=25):
    current = node
    for _ in range(limit):
        if current is None:
            return False
        if current.attrib.get("resource-id") == resource_id:
            return True
        current = parents.get(current)
    return False


def find_clickable_text(root, wanted, inside_resource=None):
    target = canon(wanted)
    parents = parent_map(root)
    found = []
    for node in root.iter("node"):
        if canon(text_of(node)) != target and canon(desc_of(node)) != target:
            continue
        if inside_resource and not is_inside(node, parents, inside_resource):
            continue
        clickable = clickable_ancestor(node, parents)
        if clickable is not None and center(clickable):
            found.append(clickable)
    return found[-1] if found else None


def screen_state(root):
    # El orden importa porque la app conserva pantallas inferiores en el XML.
    if find_res(root, "player-screen") is not None:
        return "player"
    if find_res(root, "team-plantilla-modal") is not None:
        return "squad"
    if find_res(root, "team-screen") is not None:
        return "team"
    if find_res(root, "match-screen") is not None:
        return "match"
    if find_res(root, "competition-sections-sheet") is not None:
        return "competition_sheet"
    if find_res(root, "competition-screen") is not None:
        return "competition"
    if find_all_res(root, "competition-category-item"):
        return "competitions"
    return "other"


def after_action(expected, delay=DELAY_SCREEN, timeout=3.2):
    expected = {expected} if isinstance(expected, str) else set(expected)
    time.sleep(delay)
    end = time.time() + timeout
    while True:
        root = ui_root()
        if screen_state(root) in expected:
            return root
        if time.time() >= end:
            return None
        time.sleep(0.08)

# ============================================================
# CSV / CHECKPOINT
# ============================================================

def csv_key(row):
    return (
        canon(row.get("Temporada")),
        canon(row.get("Competicion_origen")),
        canon(row.get("Grupo_origen")),
        canon(row.get("Equipo_origen")),
        canon(row.get("Jugador")),
        str(row.get("Ano_nacimiento", "")).strip(),
    )


def open_csv():
    global CSV_FILE, CSV_WRITER, CSV_KEYS
    exists = CSV_OUT.exists() and CSV_OUT.stat().st_size > 0
    if exists:
        with CSV_OUT.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                CSV_KEYS.add(csv_key(row))

    CSV_FILE = CSV_OUT.open("a", newline="", encoding="utf-8-sig", buffering=1)
    CSV_WRITER = csv.DictWriter(CSV_FILE, fieldnames=COLUMNAS, extrasaction="ignore")
    if not exists:
        CSV_WRITER.writeheader()
        CSV_FILE.flush()
    print(f"✓ CSV: {CSV_OUT.resolve()}")
    print(f"✓ Filas existentes: {len(CSV_KEYS)}")


def append_csv(row):
    key = csv_key(row)
    if key in CSV_KEYS:
        return False
    CSV_WRITER.writerow(row)
    CSV_FILE.flush()
    CSV_KEYS.add(key)
    return True


def load_checkpoint():
    if not CHECKPOINT_OUT.exists():
        return {
            "jugadores": [],
            "equipos": [],
            "partidos": [],
            "grupos": [],
            "anonymous_rows": {},
        }
    try:
        data = json.loads(CHECKPOINT_OUT.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    for key in ("jugadores", "equipos", "partidos", "grupos"):
        data.setdefault(key, [])
    data.setdefault("anonymous_rows", {})
    return data


def save_checkpoint():
    if CHECKPOINT is None:
        return
    CHECKPOINT["jugadores"] = sorted(JUGADORES_OK)
    CHECKPOINT["equipos"] = sorted(EQUIPOS_OK)
    CHECKPOINT["partidos"] = sorted(PARTIDOS_OK)
    CHECKPOINT["grupos"] = sorted(GRUPOS_OK)
    CHECKPOINT["anonymous_rows"] = dict(ANON_ROW_MAP)

    temp = CHECKPOINT_OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(CHECKPOINT, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CHECKPOINT_OUT)


def count_completed_team_players(category, competition, group, team):
    prefix = team_player_prefix(category, competition, group, team)
    return sum(1 for key in JUGADORES_OK if key.startswith(prefix))

# ============================================================
# COMPETICIONES
# ============================================================

def go_to_competitions():
    print("\n→ Abriendo Competiciones...")
    for _ in range(12):
        root = ui_root()
        state = screen_state(root)
        if state == "competitions":
            return root
        if state in {"player", "squad", "team", "match", "competition_sheet", "competition"}:
            back()
            continue
        tab = find_res(root, "tab-competitions-button")
        if tab is not None:
            tap_node(tab)
            result = after_action("competitions")
            if result is not None:
                return result
        else:
            back()
    save_debug("no_competitions")
    raise RuntimeError("No pude llegar a Competiciones.")


def open_category(category):
    root = ui_root()
    target = canon(category)
    for node in find_all_res(root, "competition-category-item"):
        content = canon(desc_of(node) + " " + text_of(node))
        if target in content:
            print(f"→ Categoría: {category}")
            tap_node(node)
            return after_action("competition_sheet") is not None
    return False


def scroll_sheet_down():
    adb_input("swipe", 540, 1900, 540, 1000, 170)
    time.sleep(DELAY_SCROLL)


def open_competition_group(category, aliases, group):
    go_to_competitions()
    if not open_category(category):
        raise RuntimeError(f"No encuentro la categoria {category}")

    competition_name = None
    button = None
    for _ in range(10):
        root = ui_root()
        for alias in aliases:
            candidate = find_clickable_text(root, alias, "competition-sections-sheet")
            if candidate is not None:
                competition_name = alias
                button = candidate
                break
        if button is not None:
            break
        scroll_sheet_down()

    if button is None:
        save_debug("competition_not_found")
        raise RuntimeError("No encuentro la competicion: " + " / ".join(aliases))

    print(f"→ Competición: {competition_name}")
    tap_node(button)
    time.sleep(DELAY_TAP)
    root = ui_root()

    if screen_state(root) == "competition":
        return competition_name, group or ""

    if screen_state(root) != "competition_sheet":
        root = after_action("competition_sheet", delay=0.05)
        if root is None:
            raise RuntimeError("Estado inesperado al abrir competicion.")

    labels = [str(group), f"GRUPO {group}"] if group is not None else ["1", "GRUPO 1", "ÚNICO", "UNICO"]
    for _ in range(8):
        root = ui_root()
        for label in labels:
            node = find_clickable_text(root, label, "competition-sections-sheet")
            if node is not None:
                print(f"→ Grupo: {group if group is not None else label}")
                tap_node(node)
                if after_action("competition") is not None:
                    return competition_name, (str(group) if group is not None else label)
        scroll_sheet_down()

    save_debug("group_not_found")
    raise RuntimeError(f"No pude abrir grupo {group}")

# ============================================================
# JORNADAS / PARTIDOS
# ============================================================

def competition_scroll_top():
    for _ in range(2):
        root = ui_root()
        tab = find_res(root, "competition-view-tab-0")
        b = bounds_tuple(tab)
        if b and b[1] < 900:
            return root
        adb_input("swipe", 540, 750, 540, 1900, 170)
        time.sleep(DELAY_SCROLL)
    return ui_root()


def select_matchday(number):
    print(f"→ Buscando jornada {number}")
    root = competition_scroll_top()
    tab = find_res(root, "competition-view-tab-0")
    if tab is not None:
        tap_node(tab)
        time.sleep(DELAY_TAP)
        root = ui_root()

    wanted = int(number)
    for _ in range(15):
        visible = []
        target = None
        for node in root.iter("node"):
            b = bounds_tuple(node)
            if not b or not (650 <= b[1] <= 1050):
                continue
            value = (desc_of(node) or text_of(node)).strip()
            if not re.fullmatch(r"\d+", value):
                continue
            day = int(value)
            if not 1 <= day <= 60:
                continue
            visible.append(day)
            if day == wanted and node.attrib.get("clickable") == "true":
                target = node

        visible = sorted(set(visible))
        print(f"   Jornadas visibles: {visible}")
        if wanted in visible:
            if target is not None:
                print(f"   ✓ Pulsando jornada {wanted}")
                tap_node(target)
                time.sleep(DELAY_SCREEN)
            else:
                print(f"   ✓ Jornada {wanted} ya seleccionada")
            return True

        prev_button = next(
            (n for n in find_all_res(root, "day-slider-prev-button") if bounds_tuple(n) and bounds_tuple(n)[1] > 650),
            None,
        )
        next_button = next(
            (n for n in find_all_res(root, "day-slider-next-button") if bounds_tuple(n) and bounds_tuple(n)[1] > 650),
            None,
        )

        if visible and wanted < min(visible):
            button = prev_button
        elif visible and wanted > max(visible):
            button = next_button
        else:
            middle = visible[len(visible) // 2] if visible else wanted
            button = prev_button if wanted < middle else next_button

        if button is None or button.attrib.get("enabled") != "true":
            return False
        tap_node(button)
        time.sleep(DELAY_TAP)
        root = ui_root()
    return False


def scroll_results_down():
    adb_input("swipe", 540, 1880, 540, 1050, 170)
    time.sleep(DELAY_SCROLL)


def extract_match_from_block(block):
    clickable = next((n for n in block.iter("node") if n.attrib.get("clickable") == "true"), None)
    if clickable is None:
        return None

    teams = []
    date = ""
    for node in block.iter("node"):
        value = clean_text(text_of(node))
        b = bounds_tuple(node)
        if not value or not b:
            continue
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
            date = value
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}", value) or re.fullmatch(r"\d+", value) or re.fullmatch(r"\d+\s*-\s*\d+", value):
            continue
        if 180 <= b[0] <= 780 and canon(value) not in {"FINALIZADO", "APLAZADO", "SUSPENDIDO", "PENDIENTE", "ENJUEGO"}:
            if not teams or canon(teams[-1]) != canon(value):
                teams.append(value)

    if len(teams) < 2:
        return None
    return {"local": teams[0], "visitante": teams[1], "fecha": date, "node": clickable}


def visible_matches(root):
    container = find_res(root, "competition-calendar-list")
    if container is None:
        return []
    result = []
    for block in list(container):
        item = extract_match_from_block(block)
        if item:
            result.append(item)
    return result


def results_signature(root):
    return tuple((canon(m["local"]), canon(m["visitante"]), m["fecha"]) for m in visible_matches(root))


def match_team_names(root):
    local = find_res(root, "match-header-local-team-button")
    away = find_res(root, "match-header-away-team-button")
    if local is None or away is None:
        return None, None
    return clean_text(desc_of(local)), clean_text(desc_of(away))

# ============================================================
# EQUIPO / PLANTILLA
# ============================================================

def team_name_from_screen(root):
    card = find_res(root, "team-header-card")
    if card is None:
        return ""
    for node in card.iter("node"):
        value = clean_text(text_of(node))
        if value:
            return value
    return ""


def open_team_from_match(side_resource):
    root = ui_root()
    button = find_res(root, side_resource)
    if button is None:
        return None, None
    hint = clean_text(desc_of(button))
    tap_node(button)
    team_root = after_action("team")
    if team_root is None:
        return None, None
    return team_name_from_screen(team_root) or hint, team_root


def open_squad(team_root=None):
    team_root = team_root or ui_root()
    button = find_res(team_root, "team-header-templates-button")
    if button is None:
        return None
    tap_node(button)
    return after_action("squad", delay=0.14, timeout=3.0)


def close_squad_to_team(root=None):
    root = root or ui_root()
    button = find_res(root, "team-plantilla-close-button")
    if button is not None:
        tap_node(button)
        return after_action("team", delay=0.14, timeout=3.0)
    back()
    return after_action("team", delay=0.05, timeout=3.0)


def squad_expected_count(root):
    modal = find_res(root, "team-plantilla-modal")
    if modal is None:
        return None
    label = next((n for n in modal.iter("node") if canon(text_of(n)) == canon("JUGADORES/AS")), None)
    lb = bounds_tuple(label)
    if not lb:
        return None
    for node in modal.iter("node"):
        value = text_of(node)
        b = bounds_tuple(node)
        if b and re.fullmatch(r"\d+", value) and abs(b[1] - lb[1]) <= 80 and b[0] > 800:
            return int(value)
    return None


def row_name_from_node(node):
    # 1) content-desc del row
    name = clean_text(desc_of(node))
    if valid_player_name(name):
        return name

    # 2) texto de sus hijos. Esto tambien corrige casos donde content-desc tarda en poblarse.
    candidates = []
    for child in node.iter("node"):
        value = clean_text(text_of(child))
        if valid_player_name(value):
            candidates.append(value)
    # Los nombres de jugador suelen contener coma. Preferimos esos.
    comma = [x for x in candidates if "," in x]
    if comma:
        return comma[0]
    return candidates[0] if candidates else ""


def is_player_row_shape(node):
    if node.attrib.get("clickable") != "true":
        return False
    if node.attrib.get("resource-id"):
        return False
    b = bounds_tuple(node)
    if not b:
        return False
    x1, y1, x2, y2 = b
    width = x2 - x1
    height = y2 - y1
    # Las filas de jugador de la app ocupan casi todo el ancho (~988px) y ~147px de alto.
    # Permitimos fila recortada en los bordes del ScrollView.
    return x1 <= 120 and x2 >= 900 and width >= 800 and 35 <= height <= 190 and y2 >= 700


def squad_rows(root):
    """
    Devuelve TODAS las filas que parecen jugadores, incluidas las filas SIN NOMBRE.
    Una fila sin nombre es fundamental: puede ser un jugador cuyo nombre esta vacio
    tanto en Plantilla como en 26-27.
    """
    modal = find_res(root, "team-plantilla-modal")
    if modal is None:
        return [], False

    trainer_y = None
    trainer_seen = False
    for node in modal.iter("node"):
        if canon(text_of(node)) == canon("ENTRENADOR/A"):
            b = bounds_tuple(node)
            trainer_y = b[1] if b else None
            trainer_seen = True
            break

    rows = []
    for node in modal.iter("node"):
        if not is_player_row_shape(node):
            continue
        b = bounds_tuple(node)
        if trainer_y is not None and b and b[1] >= trainer_y:
            continue
        rows.append({"node": node, "name": row_name_from_node(node), "bounds": b})

    rows.sort(key=lambda r: (r["bounds"] or (0, 99999, 0, 0))[1])

    # Crear fingerprint para filas anonimas usando los vecinos con nombre.
    for i, row in enumerate(rows):
        if row["name"]:
            row["fingerprint"] = "NAME:" + canon(row["name"])
            continue
        prev_name = ""
        next_name = ""
        for j in range(i - 1, -1, -1):
            if rows[j]["name"]:
                prev_name = rows[j]["name"]
                break
        for j in range(i + 1, len(rows)):
            if rows[j]["name"]:
                next_name = rows[j]["name"]
                break
        # Los vecinos hacen que una fila vacia concreta pueda reconocerse tras reabrir Plantilla.
        row["fingerprint"] = f"BETWEEN:{canon(prev_name)}::{canon(next_name)}"

    return rows, trainer_seen


def visible_row(node):
    b = bounds_tuple(node)
    if not b or b[2] <= b[0] or b[3] <= b[1]:
        return False
    cy = (b[1] + b[3]) // 2
    return 700 <= cy <= 2190


def squad_scroll_down_safe():
    # ~150 px: aproximadamente una fila, con mucho solapamiento.
    adb_input("swipe", 540, 1780, 540, 1630, 280)
    time.sleep(DELAY_SCROLL)


def find_first_pending_player(root, category, competition, group, team_name):
    """
    Recorre Plantilla lentamente y devuelve el primer pendiente.
    Tambien devuelve filas anonimas clicables.
    """
    stagnant = 0
    last_signature = None

    for _ in range(120):
        rows, trainer_seen = squad_rows(root)

        for row in rows:
            node = row["node"]
            if not visible_row(node):
                continue

            name = row["name"]
            if name:
                if player_key(category, competition, group, team_name, name) in JUGADORES_OK:
                    continue
                return row, root, trainer_seen

            # Fila anonima.
            akey = anon_key(category, competition, group, team_name, row["fingerprint"])
            mapped_name = clean_text(ANON_ROW_MAP.get(akey, ""))
            if mapped_name and player_key(category, competition, group, team_name, mapped_name) in JUGADORES_OK:
                continue
            row["anon_key"] = akey
            return row, root, trainer_seen

        if trainer_seen:
            return None, root, True

        signature = tuple((canon(r["name"]), r["fingerprint"]) for r in rows)
        if signature and signature == last_signature:
            stagnant += 1
        else:
            stagnant = 0
        last_signature = signature

        if stagnant >= 7:
            return None, root, False

        squad_scroll_down_safe()
        root = ui_root()

    return None, root, False

# ============================================================
# JUGADOR: IDENTIDAD / ESTADISTICAS
# ============================================================

def player_header(root):
    return find_res(root, "player-header-card")


def player_panel(root):
    header = player_header(root)
    if header is None:
        return None
    return parent_map(root).get(header)


def node_center_y(node):
    b = bounds_tuple(node)
    return ((b[1] + b[3]) // 2) if b else None


def player_identity(root):
    """
    Lee nombre, año y club usando geometria alrededor del año.
    Si 26-27 no muestra nombre, devuelve nombre="" pero conserva año y club.
    """
    header = player_header(root)
    if header is None:
        return "", "", ""

    items = []
    for node in header.iter("node"):
        value = clean_text(text_of(node))
        if not value:
            continue
        b = bounds_tuple(node)
        if not b:
            continue
        if re.fullmatch(r"\d{2}-\d{2}", value):
            continue
        if "SEGUIDOR" in canon(value):
            continue
        items.append((value, node_center_y(node), b))

    year_item = next((item for item in items if re.fullmatch(r"(19|20)\d{2}", item[0])), None)
    if year_item is None:
        return "", "", ""

    year, year_y, _ = year_item

    # Nombre: texto valido inmediatamente por encima del año, no temporada.
    name_candidates = []
    for value, y, b in items:
        if y is None or y >= year_y:
            continue
        if year_y - y > 170:
            continue
        if not valid_player_name(value):
            continue
        name_candidates.append((year_y - y, value))
    name = min(name_candidates, default=(9999, ""), key=lambda x: x[0])[1]

    # Club: texto inmediatamente debajo del año. No exigimos coma.
    club_candidates = []
    for value, y, b in items:
        if y is None or y <= year_y:
            continue
        if y - year_y > 170:
            continue
        if re.fullmatch(r"(19|20)\d{2}", value):
            continue
        if canon(value) in {"PARTIDOS", "TARJETAS", "COMPETICIONES"}:
            continue
        if "SEGUIDOR" in canon(value):
            continue
        club_candidates.append((y - year_y, value))
    club = min(club_candidates, default=(9999, ""), key=lambda x: x[0])[1]

    return clean_text(name), year, clean_text(club)


def wait_player_loaded(root=None, timeout=2.5):
    # El nombre puede estar vacio. Consideramos cargado si hay player-screen + año o PARTIDOS.
    def loaded(r):
        if r is None or screen_state(r) != "player":
            return False
        _, year, _ = player_identity(r)
        if year:
            return True
        panel = player_panel(r)
        if panel is not None:
            return any(text_of(n) == "PARTIDOS" for n in panel.iter("node"))
        return False

    if loaded(root):
        return root
    end = time.time() + timeout
    while time.time() < end:
        root = ui_root()
        if loaded(root):
            return root
        time.sleep(0.08)
    return None


def player_scroll_top_once():
    adb_input("swipe", 540, 800, 540, 1950, 160)
    time.sleep(0.08)


def player_text_elements(root):
    panel = player_panel(root)
    if panel is None:
        return []
    return [
        {"text": clean_text(text_of(n)), "bounds": n.attrib.get("bounds", "")}
        for n in panel.iter("node")
        if clean_text(text_of(n))
    ]


def center_bounds_string(value):
    nums = [int(x) for x in re.findall(r"-?\d+", value or "")]
    if len(nums) != 4:
        return None
    return ((nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2)


def stat_value(elements, label):
    label_item = next((item for item in elements if item["text"] == label), None)
    if label_item is None:
        return None
    p0 = center_bounds_string(label_item["bounds"])
    if p0 is None:
        return None

    candidates = []
    for item in elements:
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", item["text"]):
            continue
        point = center_bounds_string(item["bounds"])
        if point is None or point[1] <= p0[1]:
            continue
        dx = abs(point[0] - p0[0])
        dy = point[1] - p0[1]
        if dx <= 190 and dy <= 220:
            candidates.append((dx + dy, item["text"]))
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def read_player_stats(root):
    name, year, club = player_identity(root)
    elements = player_text_elements(root)
    texts = {item["text"] for item in elements}
    has_stats = "PARTIDOS" in texts and "Convocados" in texts

    data = {
        "Jugador": name,
        "Ano": year,
        "Club": club,
        "Estado": "OK" if has_stats else "SIN_ESTADISTICAS",
        "Convocados": None,
        "Titular": None,
        "Suplente": None,
        "Jugados": None,
        "Total Goles": None,
        "Media Goles": None,
        "Amarillas": None,
        "Rojas": None,
        "Doble Amarilla": None,
    }
    if has_stats:
        for label in (
            "Convocados", "Titular", "Suplente", "Jugados", "Total Goles",
            "Media Goles", "Amarillas", "Rojas", "Doble Amarilla",
        ):
            data[label] = stat_value(elements, label)
    return data

# ============================================================
# COMPETICIONES DE TEMPORADA
# ============================================================

def extract_comp_cards(root):
    panel = player_panel(root)
    if panel is None:
        return set()
    found = set()
    for node in panel.iter("node"):
        description = clean_text(desc_of(node))
        normalized = canon(description)
        if description and "POSICION" in normalized and "PUNTOS" in normalized:
            found.add(re.sub(r"\s+", " ", description))
    return found


def read_full_season(root):
    data = read_player_stats(root)
    if not EXTRAER_COMPETICIONES_TEMPORADA:
        return data, ""

    competitions = extract_comp_cards(root)
    if not competitions:
        adb_input("swipe", 540, 1850, 540, 980, 150)
        time.sleep(DELAY_SCROLL)
        root2 = ui_root()
        competitions |= extract_comp_cards(root2)
    return data, " || ".join(sorted(competitions))

# ============================================================
# TEMPORADAS
# ============================================================

def find_player_season_button(root, season):
    header = player_header(root)
    if header is None:
        return None
    for node in header.iter("node"):
        value = clean_text(desc_of(node) or text_of(node))
        if value == season and node.attrib.get("clickable") == "true":
            return node
    return None


def select_historical_season(season):
    player_scroll_top_once()
    root = ui_root()

    for _ in range(5):
        button = find_player_season_button(root, season)
        if button is not None:
            tap_node(button)
            time.sleep(DELAY_SEASON)
            loaded = ui_root()
            return wait_player_loaded(loaded, 1.8)

        header = player_header(root)
        if header is None:
            return None
        prev_button = next(
            (
                n for n in header.iter("node")
                if n.attrib.get("resource-id") == "day-slider-prev-button"
                and n.attrib.get("enabled") == "true"
            ),
            None,
        )
        if prev_button is None:
            return None
        tap_node(prev_button)
        time.sleep(0.10)
        root = ui_root()
    return None

# ============================================================
# FILAS CSV / JUGADOR
# ============================================================

def build_row(category, competition, group, team, season, data, resolved_name, resolved_year, competitions):
    return {
        "Temporada": season,
        "Categoria_origen": category,
        "Competicion_origen": competition,
        "Grupo_origen": group or "",
        "Equipo_origen": team,
        "Jugador": resolved_name,
        "Ano_nacimiento": data.get("Ano") or resolved_year,
        "Club_en_temporada": data.get("Club") or "",
        "Estado": data.get("Estado") or "",
        "Convocados": data.get("Convocados"),
        "Titular": data.get("Titular"),
        "Suplente": data.get("Suplente"),
        "Jugados": data.get("Jugados"),
        "Total_Goles": data.get("Total Goles"),
        "Media_Goles": data.get("Media Goles"),
        "Amarillas": data.get("Amarillas"),
        "Rojas": data.get("Rojas"),
        "Doble_Amarilla": data.get("Doble Amarilla"),
        "Competiciones_temporada": competitions,
    }


def resolve_player_name(roster_name, season_results):
    roster_name = clean_text(roster_name)
    if valid_player_name(roster_name):
        return roster_name

    # Si 26-27 esta vacio, preferimos 25-26 y luego 24-25.
    for season in ("25-26", "24-25", "26-27"):
        result = season_results.get(season)
        if not result:
            continue
        name = clean_text(result["data"].get("Jugador", ""))
        if valid_player_name(name):
            return name
    return ""


def process_player(category, competition, group, team, roster_name, initial_root, anonymous_map_key=None):
    """
    Caso extremo soportado:
      - nombre vacio en Plantilla
      - nombre vacio en 26-27
      - nombre presente en 25-26 o 24-25

    En ese caso NO escribimos 26-27 inmediatamente. Primero leemos las temporadas,
    resolvemos el nombre real y luego escribimos todas las filas con ese nombre.
    """
    root = wait_player_loaded(initial_root, 2.5)
    if root is None:
        print("      ✗ No cargó la ficha del jugador")
        return False, ""

    season_results = {}

    # 26-27 ya viene cargada por defecto.
    data_2627, comps_2627 = read_full_season(root)
    name_2627_was_missing = not valid_player_name(data_2627.get("Jugador", ""))
    season_results["26-27"] = {"data": data_2627, "competitions": comps_2627}

    # Historicas. Son tambien nuestra fuente de respaldo para el nombre.
    for season in TEMPORADAS_HISTORICAS:
        historical_root = select_historical_season(season)
        if historical_root is None:
            season_results[season] = None
            continue
        data, comps = read_full_season(historical_root)
        season_results[season] = {"data": data, "competitions": comps}

    resolved_name = resolve_player_name(roster_name, season_results)
    if not resolved_name:
        print("      ✗ El jugador no tiene nombre ni en Plantilla ni en 26-27/25-26/24-25")
        save_debug("player_name_unresolved")
        return False, ""

    resolved_year = ""
    for season in TEMPORADAS:
        result = season_results.get(season)
        if result and result["data"].get("Ano"):
            resolved_year = result["data"]["Ano"]
            break

    pkey = player_key(category, competition, group, team, resolved_name)

    # Si era una fila anonima, guardar la relacion fila -> nombre real.
    if anonymous_map_key:
        ANON_ROW_MAP[anonymous_map_key] = resolved_name
        save_checkpoint()

    # Puede ocurrir si ya procesamos este anonimo en una ejecucion anterior y volvemos a pulsarlo.
    if pkey in JUGADORES_OK:
        print(f"      ↪ Fila sin nombre resuelta como {resolved_name}; ya estaba procesado")
        return True, resolved_name

    new_rows = 0
    for season in TEMPORADAS:
        result = season_results.get(season)
        if result is None:
            print(f"         {season} - no disponible")
            continue

        data = result["data"]
        # RELLENO CLAVE: si esta temporada no muestra nombre, usar el nombre resuelto de otra temporada.
        if not valid_player_name(data.get("Jugador", "")):
            data["Jugador"] = resolved_name

        if append_csv(
            build_row(
                category,
                competition,
                group,
                team,
                season,
                data,
                resolved_name,
                resolved_year,
                result["competitions"],
            )
        ):
            new_rows += 1

        marker = "nombre recuperado" if season == "26-27" and name_2627_was_missing else ""
        suffix = f" | {marker}" if marker else ""
        print(
            f"         {season} ✓ J={data.get('Jugados')} "
            f"G={data.get('Total Goles')}{suffix}"
        )

    JUGADORES_OK.add(pkey)
    save_checkpoint()
    print(f"      ✓ {resolved_name} ({new_rows} filas nuevas)")
    return True, resolved_name

# ============================================================
# VOLVER JUGADOR -> EQUIPO -> PLANTILLA
# ============================================================

def return_player_to_squad():
    back()
    root = after_action({"team", "squad"}, delay=0.14, timeout=3.0)
    if root is None:
        return None
    if screen_state(root) == "squad":
        return root
    button = find_res(root, "team-header-templates-button")
    if button is None:
        return None
    tap_node(button)
    return after_action("squad", delay=0.14, timeout=3.0)

# ============================================================
# PROCESAR EQUIPO
# ============================================================

def process_team(category, competition, group, side_resource):
    match_root = ui_root()
    button = find_res(match_root, side_resource)
    if button is None:
        print("   ✗ No encuentro boton del equipo")
        return False

    team_hint = clean_text(desc_of(button))
    hint_key = key_text(category, competition, group or "", team_hint)
    if hint_key in EQUIPOS_OK:
        print(f"   ↪ Equipo ya completado: {team_hint}")
        return True

    team_name, team_root = open_team_from_match(side_resource)
    if not team_name:
        return False

    tkey = key_text(category, competition, group or "", team_name)
    if tkey in EQUIPOS_OK:
        back()
        return after_action("match") is not None

    print(f"\n   EQUIPO: {team_name}")
    squad_root = open_squad(team_root)
    if squad_root is None:
        return False

    expected = squad_expected_count(squad_root)
    if expected is None:
        print("   ✗ No pude leer JUGADORES/AS")
        save_debug("missing_player_count", squad_root)
        return False

    done = count_completed_team_players(category, competition, group, team_name)
    print(f"   La app indica: {expected} jugadores")
    print(f"   Ya procesados: {done}/{expected}")

    passes = 0
    while True:
        done = count_completed_team_players(category, competition, group, team_name)
        if done >= expected:
            print(f"\n   ✓ Plantilla completa: {done}/{expected}")
            break

        row, squad_root, trainer_seen = find_first_pending_player(
            squad_root, category, competition, group, team_name
        )

        if row is not None:
            roster_name = clean_text(row.get("name", ""))
            anonymous = not bool(roster_name)
            anonymous_map_key = row.get("anon_key") if anonymous else None

            if anonymous:
                print("\n      → [JUGADOR SIN NOMBRE EN PLANTILLA]")
                print(f"         Si se resuelve correctamente: {done + 1}/{expected}")
            else:
                print(f"\n      → {roster_name}")
                print(f"         Si termina: {done + 1}/{expected}")

            tap_node(row["node"])
            player_root = after_action("player", delay=0.14, timeout=3.0)
            if player_root is None:
                print("      ⚠ No abrió el jugador")
                squad_root = return_player_to_squad()
                if squad_root is None:
                    return False
                continue

            ok, resolved_name = process_player(
                category,
                competition,
                group,
                team_name,
                roster_name,
                player_root,
                anonymous_map_key=anonymous_map_key,
            )

            squad_root = return_player_to_squad()
            if squad_root is None:
                print("      ✗ No pude volver a Plantilla")
                return False

            if not ok:
                continue

            if anonymous and resolved_name:
                print(f"      ↳ Fila vacía identificada como: {resolved_name}")

            # La app reabre Plantilla arriba. No restauramos scroll.
            continue

        done = count_completed_team_players(category, competition, group, team_name)
        if done >= expected:
            break

        passes += 1
        print(
            f"\n   ↻ Llegué al final con {done}/{expected}. "
            f"La app indica {expected}; vuelvo arriba."
        )

        team_root = close_squad_to_team(squad_root)
        if team_root is None:
            return False
        squad_root = open_squad(team_root)
        if squad_root is None:
            return False

        if passes >= 10:
            print(f"   ✗ Después de {passes} recorridos sigo en {done}/{expected}.")
            save_debug("team_incomplete", squad_root)
            return False

    team_root = close_squad_to_team(squad_root)
    if team_root is None:
        return False

    EQUIPOS_OK.add(tkey)
    EQUIPOS_OK.add(hint_key)
    save_checkpoint()
    print(f"   ✓ EQUIPO COMPLETADO: {team_name} ({expected}/{expected})")

    back()
    return after_action("match", delay=0.14, timeout=3.0) is not None

# ============================================================
# PROCESAR PARTIDO / JORNADA / OBJETIVO
# ============================================================

def process_match(category, competition, group, item):
    mkey = key_text(
        category, competition, group or "", item["local"], item["visitante"], item["fecha"]
    )
    local_key = key_text(category, competition, group or "", item["local"])
    away_key = key_text(category, competition, group or "", item["visitante"])

    if mkey in PARTIDOS_OK or (local_key in EQUIPOS_OK and away_key in EQUIPOS_OK):
        PARTIDOS_OK.add(mkey)
        save_checkpoint()
        print(f"   ↪ Partido ya cubierto: {item['local']} vs {item['visitante']}")
        return True

    tap_node(item["node"])
    root = after_action("match")
    if root is None:
        return False

    local, away = match_team_names(root)
    if not local or not away:
        save_debug("match_teams_missing", root)
        return False

    print(f"\nPARTIDO: {local} vs {away}")

    if not process_team(category, competition, group, "match-header-local-team-button"):
        print(f"   ✗ Error procesando {local}")
        return False

    if not process_team(category, competition, group, "match-header-away-team-button"):
        print(f"   ✗ Error procesando {away}")
        return False

    back()
    if after_action("competition") is None:
        return False

    PARTIDOS_OK.add(mkey)
    save_checkpoint()
    print(f"✓ PARTIDO COMPLETADO: {local} vs {away}")
    return True


def process_matchday(category, competition, group, matchday, max_matches=None):
    if not select_matchday(matchday):
        print(f"⚠ No pude seleccionar jornada {matchday}")
        return False

    seen = set()
    stagnant = 0
    processed = 0

    for _ in range(50):
        root = ui_root()
        candidate = None
        for item in visible_matches(root):
            visible_key = key_text(item["local"], item["visitante"], item["fecha"])
            if visible_key not in seen:
                seen.add(visible_key)
                candidate = item
                break

        if candidate is not None:
            print(f"\n→ Partido encontrado: {candidate['local']} vs {candidate['visitante']}")
            if not process_match(category, competition, group, candidate):
                return False
            processed += 1
            if max_matches is not None and processed >= max_matches:
                break
            continue

        before = results_signature(root)
        scroll_results_down()
        root2 = ui_root()
        after = results_signature(root2)
        stagnant = stagnant + 1 if after == before else 0
        if stagnant >= 2:
            break

    return True


def process_target(target, group):
    category = target["categoria"]
    competition, real_group = open_competition_group(category, target["competicion"], group)
    gkey = key_text(category, competition, real_group)

    if gkey in GRUPOS_OK:
        print(f"✓ Ya completado: {competition} | Grupo {real_group or '-'}")
        return True

    print("\n" + "=" * 70)
    print(f"{category} | {competition} | Grupo {real_group or '-'}")
    print("=" * 70)

    days = [1] if MODO_PRUEBA else JORNADAS_BUSQUEDA
    for day in days:
        if not process_matchday(
            category,
            competition,
            real_group,
            day,
            max_matches=(1 if MODO_PRUEBA else None),
        ):
            return False
        if MODO_PRUEBA:
            break

    if not MODO_PRUEBA:
        GRUPOS_OK.add(gkey)
        save_checkpoint()
    return True

# ============================================================
# MAIN
# ============================================================

def main():
    global CHECKPOINT, JUGADORES_OK, EQUIPOS_OK, PARTIDOS_OK, GRUPOS_OK, ANON_ROW_MAP

    print("\n" + "=" * 70)
    print("SCRAPER RFAF V7")
    print("SOPORTE DE JUGADORES SIN NOMBRE EN PLANTILLA / 26-27")
    print("SCROLL PLANTILLA: APROX. UNA FILA POR VEZ")
    print("CONTADOR APP = FUENTE DE VERDAD")
    print("=" * 70)
    print(f"Modo prueba: {MODO_PRUEBA}")
    print(f"Competiciones por temporada: {EXTRAER_COMPETICIONES_TEMPORADA}")

    detect_device()
    open_csv()
    CHECKPOINT = load_checkpoint()
    JUGADORES_OK = set(CHECKPOINT.get("jugadores", []))
    EQUIPOS_OK = set(CHECKPOINT.get("equipos", []))
    PARTIDOS_OK = set(CHECKPOINT.get("partidos", []))
    GRUPOS_OK = set(CHECKPOINT.get("grupos", []))
    ANON_ROW_MAP = dict(CHECKPOINT.get("anonymous_rows", {}))

    print(
        f"✓ Checkpoint: {len(EQUIPOS_OK)} equipos | "
        f"{len(JUGADORES_OK)} jugadores | {len(PARTIDOS_OK)} partidos"
    )

    targets = [OBJETIVOS[0]] if MODO_PRUEBA else OBJETIVOS

    try:
        for target in targets:
            groups = [None] if MODO_PRUEBA else target["grupos"]
            for group in groups:
                if not process_target(target, group):
                    print("\n⚠ Objetivo incompleto. CSV y checkpoint conservados.")
                    return
    except KeyboardInterrupt:
        print("\n⏹ Detenido manualmente. CSV y checkpoint guardados.")
    finally:
        save_checkpoint()
        if CSV_FILE is not None:
            CSV_FILE.flush()
            CSV_FILE.close()

    print("\n✓ PROCESO FINALIZADO")
    print(f"CSV: {CSV_OUT.resolve()}")
    print(f"Checkpoint: {CHECKPOINT_OUT.resolve()}")


if __name__ == "__main__":
    main()
