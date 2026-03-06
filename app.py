import base64
import calendar
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import streamlit as st

DB_PATH = os.getenv("DB_PATH", "citas.db")
SLOTS = ["09:00-10:00", "10:00-11:00", "11:00-12:00", "15:00-16:00", "16:00-17:00"]
MARCH_MONTH = 3
INSTITUTIONAL_DOMAIN = "rree.gob.pe"
ADMIN_ACCESS_CODES = {"adm", "admin"}

DIRECTIONS = {
    "DEE": "Direccion General de Estudios y Estrategias de Politica Exterior",
    "DGA": "Direccion General de America",
    "DDF": "Direccion General de Desarrollo e Integracion Fronteriza",
    "DSL": "Direccion General de Soberania, Limites y Asuntos Antarticos",
    "DGE": "Direccion General de Europa",
    "DAO": "Direccion General de Asia y Oceania",
    "DAM": "Direccion General de Africa, Medio Oriente y Paises del Golfo",
    "DGM": "Direccion General de Asuntos Multilaterales y Globales",
    "DAE": "Direccion General para Asuntos Economicos",
    "DPE": "Direccion General de Promocion Economica",
    "DGC": "Direccion General de Comunidades Peruanas en el Exterior y Asuntos Consulares",
    "DAC": "Direccion General de Diplomacia Cultural",
    "DGT": "Direccion General de Tratados y Derecho Internacional",
    "PRO": "Direccion General de Protocolo y Ceremonial del Estado",
    "SGG": "Secretaria General",
}

def get_conn():
    db_parent = Path(DB_PATH).parent
    db_parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(year: int):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS coordinators (
            code TEXT PRIMARY KEY,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS slots (
            slot_date TEXT NOT NULL,
            time_slot TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            booked_by TEXT,
            booked_at TEXT,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            PRIMARY KEY (slot_date, time_slot)
        )
        """
    )

    existing_cols = {
        row["name"] for row in cur.execute("PRAGMA table_info(slots)").fetchall()
    }
    if "contact_name" not in existing_cols:
        cur.execute("ALTER TABLE slots ADD COLUMN contact_name TEXT")
    if "contact_email" not in existing_cols:
        cur.execute("ALTER TABLE slots ADD COLUMN contact_email TEXT")
    if "contact_phone" not in existing_cols:
        cur.execute("ALTER TABLE slots ADD COLUMN contact_phone TEXT")

    _, last_day = calendar.monthrange(year, MARCH_MONTH)
    for day in range(1, last_day + 1):
        current_day = date(year, MARCH_MONTH, day)
        if current_day.weekday() >= 5:
            continue

        slot_date = current_day.isoformat()
        for time_slot in SLOTS:
            cur.execute(
                """
                INSERT OR IGNORE INTO slots (slot_date, time_slot, status)
                VALUES (?, ?, 'available')
                """,
                (slot_date, time_slot),
            )

    conn.commit()
    conn.close()


@st.cache_resource(show_spinner=False)
def ensure_db_initialized(year: int, db_path: str):
    # Run schema/month bootstrap once per process and year.
    init_db(year)


def fetch_coordinator(code: str):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT contact_name, contact_email, contact_phone
        FROM coordinators
        WHERE code = ?
        """,
        (code,),
    ).fetchone()
    conn.close()
    return row


def normalize_email(email: str):
    return email.strip().lower()


def is_valid_institutional_email(email: str):
    normalized = normalize_email(email)
    return normalized.endswith(f"@{INSTITUTIONAL_DOMAIN}")


def fetch_coordinator_by_email(email: str):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT code, contact_name, contact_email, contact_phone
        FROM coordinators
        WHERE LOWER(TRIM(contact_email)) = ?
        """,
        (normalize_email(email),),
    ).fetchone()
    conn.close()
    return row


def fetch_taken_direction_codes():
    conn = get_conn()
    taken_rows = conn.execute("SELECT code FROM coordinators").fetchall()
    conn.close()
    return {row["code"] for row in taken_rows}


def register_coordinator(
    code: str, contact_name: str, contact_email: str, contact_phone: str
):
    conn = get_conn()
    cur = conn.cursor()
    existing = cur.execute(
        """
        SELECT code
        FROM coordinators
        WHERE LOWER(TRIM(contact_email)) = ?
        """,
        (normalize_email(contact_email),),
    ).fetchone()
    if existing and existing["code"] != code:
        conn.close()
        return False, "El correo ya esta registrado en otra direccion general."

    taken = cur.execute(
        """
        SELECT 1
        FROM coordinators
        WHERE code = ?
        LIMIT 1
        """,
        (code,),
    ).fetchone()
    if taken:
        conn.close()
        return False, "Esa direccion general ya tiene un coordinador registrado."

    cur.execute(
        """
        INSERT INTO coordinators (code, contact_name, contact_email, contact_phone)
        VALUES (?, ?, ?, ?)
        """,
        (code, contact_name, normalize_email(contact_email), contact_phone),
    )
    conn.commit()
    conn.close()
    return True, None


def upsert_coordinator(code: str, contact_name: str, contact_email: str, contact_phone: str):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO coordinators (code, contact_name, contact_email, contact_phone)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            contact_name = excluded.contact_name,
            contact_email = excluded.contact_email,
            contact_phone = excluded.contact_phone
        """,
        (code, contact_name, normalize_email(contact_email), contact_phone),
    )
    conn.commit()
    conn.close()


def fetch_march_slots(year: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT slot_date, time_slot, status, booked_by FROM slots WHERE slot_date LIKE ?",
        (f"{year}-03-%",),
    ).fetchall()
    conn.close()

    data = {}
    for row in rows:
        parsed_date = datetime.fromisoformat(row["slot_date"]).date()
        if parsed_date.weekday() >= 5:
            continue
        data[(row["slot_date"], row["time_slot"])] = {
            "status": row["status"],
            "booked_by": row["booked_by"],
        }
    return data


def fetch_current_booking_for_code(code: str):
    conn = get_conn()
    row = conn.execute(
        """
        SELECT slot_date, time_slot
        FROM slots
        WHERE status = 'booked' AND booked_by = ?
        ORDER BY slot_date, time_slot
        LIMIT 1
        """,
        (code,),
    ).fetchone()
    conn.close()
    return row


def update_contact_for_code(
    code: str, contact_name: str, contact_email: str, contact_phone: str
):
    upsert_coordinator(code, contact_name, contact_email, contact_phone)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE slots
        SET contact_name = ?, contact_email = ?, contact_phone = ?
        WHERE status = 'booked' AND booked_by = ?
        """,
        (contact_name, contact_email, contact_phone, code),
    )
    conn.commit()
    updated_rows = cur.rowcount
    conn.close()
    return updated_rows


def fetch_admin_booked_slots(year: int):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT slot_date, time_slot, booked_by, contact_name
        FROM slots
        WHERE slot_date LIKE ?
          AND status = 'booked'
        ORDER BY slot_date, time_slot
        """,
        (f"{year}-03-%",),
    ).fetchall()
    conn.close()
    return rows


def fetch_admin_coordinator_summary():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT code, contact_name, contact_email, contact_phone
        FROM coordinators
        """
    ).fetchall()
    conn.close()

    by_code = {row["code"]: row for row in rows}
    summary = []
    for code in sorted(DIRECTIONS.keys()):
        row = by_code.get(code)
        summary.append(
            {
                "Siglas": code,
                "Nombre": row["contact_name"] if row and row["contact_name"] else "FALTA",
                "Correo": row["contact_email"] if row and row["contact_email"] else "FALTA",
                "Telefono": row["contact_phone"] if row and row["contact_phone"] else "FALTA",
            }
        )
    return summary


def admin_clear_coordinator_data(slot_date: str, time_slot: str):
    conn = get_conn()
    cur = conn.cursor()
    row = cur.execute(
        """
        SELECT booked_by
        FROM slots
        WHERE slot_date = ? AND time_slot = ? AND status = 'booked'
        """,
        (slot_date, time_slot),
    ).fetchone()

    cur.execute(
        """
        UPDATE slots
        SET contact_name = NULL, contact_email = NULL, contact_phone = NULL
        WHERE slot_date = ? AND time_slot = ? AND status = 'booked'
        """,
        (slot_date, time_slot),
    )
    if row and row["booked_by"]:
        cur.execute("DELETE FROM coordinators WHERE code = ?", (row["booked_by"],))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected


def admin_delete_selected_booking(slot_date: str, time_slot: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE slots
        SET status = 'available',
            booked_by = NULL,
            booked_at = NULL,
            contact_name = NULL,
            contact_email = NULL,
            contact_phone = NULL
        WHERE slot_date = ? AND time_slot = ? AND status = 'booked'
        """,
        (slot_date, time_slot),
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected


def admin_delete_coordinator_by_code(code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM coordinators WHERE code = ?", (code,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected


def admin_set_status(slot_date: str, time_slot: str, status: str):
    conn = get_conn()
    if status == "available":
        conn.execute(
            """
            UPDATE slots
            SET status = 'available',
                booked_by = NULL,
                booked_at = NULL,
                contact_name = NULL,
                contact_email = NULL,
                contact_phone = NULL
            WHERE slot_date = ? AND time_slot = ?
            """,
            (slot_date, time_slot),
        )
    elif status == "disabled":
        conn.execute(
            """
            UPDATE slots
            SET status = 'disabled',
                booked_by = NULL,
                booked_at = NULL,
                contact_name = NULL,
                contact_email = NULL,
                contact_phone = NULL
            WHERE slot_date = ? AND time_slot = ?
            """,
            (slot_date, time_slot),
        )
    conn.commit()
    conn.close()


def book_slot(
    slot_date: str,
    time_slot: str,
    code: str,
    contact_name: str,
    contact_email: str,
    contact_phone: str,
):
    conn = get_conn()
    cur = conn.cursor()
    current = cur.execute(
        "SELECT status FROM slots WHERE slot_date = ? AND time_slot = ?",
        (slot_date, time_slot),
    ).fetchone()

    if not current or current["status"] != "available":
        conn.close()
        return False, 0

    cur.execute(
        """
        UPDATE slots
        SET status = 'available',
            booked_by = NULL,
            booked_at = NULL,
            contact_name = NULL,
            contact_email = NULL,
            contact_phone = NULL
        WHERE booked_by = ? AND status = 'booked'
        """,
        (code,),
    )
    replaced_count = cur.rowcount

    cur.execute(
        """
        UPDATE slots
        SET status = 'booked',
            booked_by = ?,
            booked_at = ?,
            contact_name = ?,
            contact_email = ?,
            contact_phone = ?
        WHERE slot_date = ? AND time_slot = ?
        """,
        (
            code,
            datetime.now().isoformat(timespec="seconds"),
            contact_name,
            contact_email,
            contact_phone,
            slot_date,
            time_slot,
        ),
    )
    conn.commit()
    conn.close()
    return True, replaced_count


def get_logo_base64():
    logo_path = Path("assets/logo_mre.png")
    fallback_logo = Path("logo_mre.png")
    selected_logo = logo_path if logo_path.exists() else fallback_logo
    return get_image_base64(selected_logo.as_posix())


def get_login_background_base64():
    return get_image_base64("assets/fondoia.png")


@st.cache_data(show_spinner=False)
def get_image_base64(path_str: str):
    image_path = Path(path_str)
    if not image_path.exists():
        return None
    return base64.b64encode(image_path.read_bytes()).decode("utf-8")


def render_persistent_logo():
    encoded = get_logo_base64()
    if not encoded:
        return

    st.markdown(
        f"""
        <div class="mre-logo-fixed">
            <img src="data:image/png;base64,{encoded}" alt="Logo MRE" />
        </div>
        """,
        unsafe_allow_html=True,
    )


def apply_formal_styles(is_login: bool):
    login_bg = get_login_background_base64()
    if login_bg:
        login_bg_css = (
            "background: linear-gradient(180deg, rgba(4, 14, 33, 0.58), rgba(7, 24, 52, 0.48)), "
            f"url('data:image/png;base64,{login_bg}') center center / cover no-repeat;"
        )
    else:
        login_bg_css = (
            "background: radial-gradient(circle at 50% 55%, rgba(255,255,255,0.23), "
            "rgba(255,255,255,0.05) 30%, rgba(2,24,60,0.88) 75%), "
            "linear-gradient(180deg, #0b2342 0%, #112e52 45%, #1a385f 100%);"
        )

    base_css = """
    <style>
    .stApp, html, body, [class*="css"] {
        font-family: "Frutiger", "Frutiger LT Std", "Myriad Pro", "Segoe UI", Arial, sans-serif !important;
    }
    header[data-testid="stHeader"] { display: none !important; }
    div[data-testid="stToolbar"] { display: none !important; }
    #MainMenu { visibility: hidden; }
    h1, h2, h3 {
        font-family: "Frutiger", "Frutiger LT Std", "Myriad Pro", "Segoe UI", Arial, sans-serif !important;
        font-weight: 700 !important;
        text-align: center !important;
    }
    h1 { font-size: 2.0rem !important; }
    h2 { font-size: 1.6rem !important; }
    h3 { font-size: 1.35rem !important; }
    p { text-align: center; }
    div[data-testid="stCaptionContainer"] { text-align: center; }
    .selected-slot {
        width: 90%;
        padding: 0.54rem 0.8rem;
        margin: 0 0 0.45rem 0;
        border-radius: 0.6rem;
        border: 1px solid rgba(51, 153, 255, 0.35);
        background: rgba(102, 179, 255, 0.22);
        color: #17324d;
        text-align: center;
        font-weight: 600;
        box-sizing: border-box;
        margin-left: auto;
        margin-right: auto;
    }
    .pending-slot {
        width: 90%;
        padding: 0.54rem 0.8rem;
        margin: 0 0 0.45rem 0;
        border-radius: 0.6rem;
        border: 1px solid rgba(51, 153, 255, 0.35);
        background: rgba(102, 179, 255, 0.15);
        color: #17324d;
        text-align: center;
        font-weight: 600;
        box-sizing: border-box;
        margin-left: auto;
        margin-right: auto;
    }
    .stButton > button[kind="primary"] {
        background: rgba(102, 179, 255, 0.45) !important;
        border: 1px solid rgba(51, 153, 255, 0.8) !important;
        color: #0f2f4d !important;
        font-weight: 700 !important;
    }
    .stButton > button {
        padding-top: 0.32rem !important;
        padding-bottom: 0.32rem !important;
    }
    .day-slots-col {
        padding-left: 3cm;
    }
    .current-booking-box {
        margin: 1rem auto 0 auto;
        max-width: 760px;
        border: 1px solid rgba(46, 125, 50, 0.55);
        background: rgba(102, 187, 106, 0.18);
        color: #1f4d24;
        border-radius: 10px;
        padding: 0.7rem 1rem;
        text-align: center;
        font-weight: 700;
    }
    </style>
    """

    app_css = """
    <style>
    .block-container {
        max-width: 1200px;
        margin-left: auto;
        margin-right: auto;
        padding-top: 3.2rem;
        padding-bottom: 6rem;
    }
    h1 { margin-top: 2.25rem !important; }
    .mre-logo-fixed {
        position: fixed;
        top: 14px;
        left: 18px;
        width: 25vw;
        max-width: 320px;
        min-width: 170px;
        z-index: 9999;
    }
    .mre-logo-fixed img {
        width: 100%;
        height: auto;
        display: block;
    }
    @media (max-width: 900px) {
        .block-container {
            padding-top: 2.4rem;
        }
    }
    </style>
    """

    login_css = """
    <style>
    .stApp {
        __LOGIN_BG_CSS__
        min-height: 100vh;
    }
    .block-container {
        max-width: 1100px;
        margin: 0 auto;
        padding-top: 0.6rem;
    }
    .login-topbar {
        width: auto;
        height: auto;
        border: none;
        border-radius: 0;
        background: transparent;
        display: flex;
        align-items: center;
        padding: 0;
        box-sizing: border-box;
    }
    .login-topbar img {
        height: 86px;
        width: auto;
        display: block;
    }
    .login-title {
        color: #e8edf6 !important;
        margin-top: 4rem !important;
        margin-bottom: 0.2rem !important;
        text-shadow: 0 2px 5px rgba(0,0,0,0.35);
    }
    .login-subtitle {
        color: #e8edf6 !important;
        font-size: 1.9rem;
        font-weight: 700;
        text-align: center;
        margin-top: 2.4rem;
        margin-bottom: 0.5rem;
        text-shadow: 0 2px 5px rgba(0,0,0,0.35);
    }
    .login-hint {
        color: #e8edf6 !important;
        margin-bottom: 0.9rem !important;
        text-shadow: 0 1px 4px rgba(0,0,0,0.35);
    }
    div[data-testid="stForm"] {
        background: linear-gradient(180deg, rgba(219,226,237,0.32), rgba(196,208,224,0.25));
        border: 1px solid rgba(255,255,255,0.42);
        border-radius: 14px;
        backdrop-filter: blur(3px);
        padding: 0.6rem 0.6rem 1rem 0.6rem;
    }
    div[data-testid="stForm"] label p {
        color: #f0f4fa !important;
        text-align: left !important;
    }
    </style>
    """
    login_css = login_css.replace("__LOGIN_BG_CSS__", login_bg_css)

    st.markdown(base_css, unsafe_allow_html=True)
    if is_login:
        st.markdown(login_css, unsafe_allow_html=True)
    else:
        st.markdown(app_css, unsafe_allow_html=True)


def render_login_topbar():
    logo = get_logo_base64()
    if not logo:
        return
    st.markdown(
        f"""
        <div class="login-topbar">
            <img src="data:image/png;base64,{logo}" alt="Logo MRE" />
        </div>
        """,
        unsafe_allow_html=True,
    )


def clear_session():
    st.session_state.auth_code = None
    st.session_state.auth_email = None
    st.session_state.pending_slot = None
    st.session_state.selected_day_iso = None
    st.session_state.registration_email = None


def render_admin_day_card(day_date: date, slots_data):
    st.markdown(f"**{day_date.strftime('%d')}**")
    for time_slot in SLOTS:
        key = (day_date.isoformat(), time_slot)
        slot = slots_data.get(key, {"status": "available", "booked_by": None})
        status = slot["status"]
        booked_by = slot["booked_by"]

        if status == "booked":
            st.button(
                f"{time_slot} | {booked_by}",
                key=f"admin_booked_{day_date}_{time_slot}",
                disabled=True,
                use_container_width=True,
            )
            if st.button(
                f"Liberar {time_slot}",
                key=f"admin_free_{day_date}_{time_slot}",
                use_container_width=True,
            ):
                admin_set_status(day_date.isoformat(), time_slot, "available")
                st.rerun()
        elif status == "disabled":
            st.button(
                f"{time_slot} | Eliminado",
                key=f"admin_disabled_{day_date}_{time_slot}",
                disabled=True,
                use_container_width=True,
            )
            if st.button(
                f"Reactivar {time_slot}",
                key=f"admin_reactivate_{day_date}_{time_slot}",
                use_container_width=True,
            ):
                admin_set_status(day_date.isoformat(), time_slot, "available")
                st.rerun()
        else:
            st.button(
                f"{time_slot} | Disponible",
                key=f"admin_available_{day_date}_{time_slot}",
                disabled=True,
                use_container_width=True,
            )
            if st.button(
                f"Eliminar {time_slot}",
                key=f"admin_delete_{day_date}_{time_slot}",
                use_container_width=True,
            ):
                admin_set_status(day_date.isoformat(), time_slot, "disabled")
                st.rerun()


def admin_view(year: int):
    action_cols = st.columns([8, 2])
    with action_cols[1]:
        if st.button("Cerrar sesion", use_container_width=True):
            clear_session()
            st.rerun()

    st.subheader("Panel administrador")
    st.info("Puede eliminar slots, reactivar slots y liberar citas reservadas.")

    st.markdown("### Resumen de coordinadores")
    summary = fetch_admin_coordinator_summary()
    st.table(summary)

    st.markdown("### Gestion de coordinadores")
    coordinator_options = []
    for row in summary:
        if (
            row["Nombre"] != "FALTA"
            or row["Correo"] != "FALTA"
            or row["Telefono"] != "FALTA"
        ):
            coordinator_options.append(
                f"{row['Siglas']} | {row['Nombre']} | {row['Correo']}"
            )

    if coordinator_options:
        selected_coord = st.selectbox(
            "Seleccione coordinador para eliminar datos",
            coordinator_options,
            key="admin_selected_coordinator",
        )
        selected_code = selected_coord.split(" | ", 1)[0]
        if st.button(
            "Eliminar datos del coordinador seleccionado",
            use_container_width=True,
            key="admin_delete_coordinator_by_code_btn",
        ):
            affected = admin_delete_coordinator_by_code(selected_code)
            if affected > 0:
                st.success(f"Datos del coordinador {selected_code} eliminados.")
            else:
                st.error("No se encontraron datos para eliminar.")
            st.rerun()
    else:
        st.info("No hay coordinadores registrados para eliminar.")

    st.markdown("### Gestion de citas previas")
    booked_rows = fetch_admin_booked_slots(year)
    if not booked_rows:
        st.info("No hay citas reservadas para gestionar.")
    else:
        options = []
        for row in booked_rows:
            booked_by = row["booked_by"] or "SIN-CODIGO"
            label = f"{row['slot_date']} | {row['time_slot']} | {booked_by}"
            options.append((label, row["slot_date"], row["time_slot"]))

        selected_label = st.selectbox(
            "Seleccione una cita reservada",
            [opt[0] for opt in options],
            key="admin_selected_booking",
        )
        selected_item = next(opt for opt in options if opt[0] == selected_label)
        selected_date, selected_time = selected_item[1], selected_item[2]

        action_col_1, action_col_2 = st.columns(2)
        with action_col_1:
            if st.button(
                "Eliminar datos de coordinador ligado a esta cita", use_container_width=True
            ):
                affected = admin_clear_coordinator_data(selected_date, selected_time)
                if affected > 0:
                    st.success("Datos de coordinador eliminados.")
                else:
                    st.error("No se pudo eliminar datos de coordinador.")
                st.rerun()

        with action_col_2:
            if st.button("Eliminar cita seleccionada", use_container_width=True):
                affected = admin_delete_selected_booking(selected_date, selected_time)
                if affected > 0:
                    st.success("Cita eliminada correctamente.")
                else:
                    st.error("No se pudo eliminar la cita seleccionada.")
                st.rerun()

    st.markdown(f"### Entrevista de Uso de IA - marzo {year}")
    slots_data = fetch_march_slots(year)
    _, last_day = calendar.monthrange(year, MARCH_MONTH)
    business_days = []
    for day in range(1, last_day + 1):
        current_day = date(year, MARCH_MONTH, day)
        if current_day.weekday() < 5:
            business_days.append(current_day)

    weekday_names = ["Lun", "Mar", "Mie", "Jue", "Vie"]
    header_cols = st.columns(5)
    for i, wd in enumerate(weekday_names):
        header_cols[i].markdown(f"**{wd}**")

    for idx in range(0, len(business_days), 5):
        cols = st.columns(5)
        week_slice = business_days[idx : idx + 5]
        for i in range(5):
            with cols[i]:
                if i < len(week_slice):
                    render_admin_day_card(week_slice[i], slots_data)
                else:
                    st.write(" ")


def welcome_view():
    render_login_topbar()
    st.markdown(
        "<h1 class='login-title'>Citas para entrevista de uso de Inteligencia Artificial en el MRE</h1>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<p class='login-hint'>Bienvenido. Ingrese su correo institucional para continuar.</p>",
        unsafe_allow_html=True,
    )

    _, form_col, _ = st.columns([2, 4, 2])
    with form_col:
        with st.form("email_access_form", clear_on_submit=False):
            email = st.text_input(
                "Correo institucional", placeholder=f"usuario@{INSTITUTIONAL_DOMAIN}"
            ).strip()
            submitted = st.form_submit_button("Ingresar", use_container_width=True)

    if submitted:
        access_input = normalize_email(email)
        if access_input in ADMIN_ACCESS_CODES:
            st.session_state.auth_code = "ADMIN"
            st.session_state.auth_email = "admin"
            st.session_state.registration_email = None
            st.rerun()

        if not is_valid_institutional_email(email):
            st.error(f"El correo debe terminar en @{INSTITUTIONAL_DOMAIN}.")
            return

        existing = fetch_coordinator_by_email(email)
        if existing:
            st.session_state.auth_code = existing["code"]
            st.session_state.auth_email = normalize_email(email)
            st.session_state.registration_email = None
            st.rerun()
        st.session_state.registration_email = normalize_email(email)
        st.info("Correo no registrado. Complete su registro de coordinador.")

    pending_email = st.session_state.get("registration_email")
    if not pending_email:
        return

    st.markdown("<h3 class='login-subtitle'>Registro de coordinador</h3>", unsafe_allow_html=True)
    taken_codes = fetch_taken_direction_codes()
    if len(taken_codes) == len(DIRECTIONS):
        st.error("Todas las direcciones generales ya tienen coordinador registrado.")
        return

    _, reg_col, _ = st.columns([1.5, 5, 1.5])
    with reg_col:
        with st.form("register_coordinator_form", clear_on_submit=False):
            direction_labels = []
            for code in sorted(DIRECTIONS):
                if code in taken_codes:
                    direction_labels.append(
                        f"{code} | {DIRECTIONS[code]} (ya tiene coordinador)"
                    )
                else:
                    direction_labels.append(f"{code} | {DIRECTIONS[code]}")
            selected_label = st.selectbox(
                "Direccion general",
                direction_labels,
                key="registration_direction",
            )
            selected_code = selected_label.split(" | ", 1)[0]
            contact_name = st.text_input("Nombre de contacto").strip()
            contact_email = st.text_input(
                "Correo de contacto",
                value=pending_email,
                disabled=True,
            ).strip()
            contact_phone = st.text_input("Telefono de contacto").strip()
            register_clicked = st.form_submit_button(
                "Registrar coordinador", use_container_width=True
            )

        if register_clicked:
            if not contact_name or not contact_phone:
                st.error("Complete nombre y telefono.")
                return
            if selected_code in taken_codes:
                st.error("Esa direccion general ya tiene un coordinador registrado.")
                return

            ok, error_message = register_coordinator(
                selected_code,
                contact_name,
                pending_email,
                contact_phone,
            )
            if not ok:
                st.error(error_message)
                return

            st.session_state.auth_code = selected_code
            st.session_state.auth_email = pending_email
            st.session_state.registration_email = None
            st.success("Registro completado. Ya puede seleccionar su cita.")
            st.rerun()


def app_view(year: int):
    auth_code = st.session_state.auth_code
    auth_email = st.session_state.auth_email

    if auth_code == "ADMIN":
        admin_view(year)
        return

    pending_slot = st.session_state.get("pending_slot")

    coordinator = fetch_coordinator(auth_code)
    if not coordinator:
        st.error("No se encontro un coordinador asociado a este correo.")
        if st.button("Volver al inicio", use_container_width=True):
            clear_session()
            st.rerun()
        return

    save_clicked = False
    top_actions = st.columns([8, 2])
    with top_actions[1]:
        if st.button("Cerrar sesion", use_container_width=True):
            clear_session()
            st.rerun()

    st.subheader(f"Direccion: {auth_code} - {DIRECTIONS[auth_code]}")
    st.markdown(
        f"### Entrevista de Uso de IA - marzo {year} | Coordinacion: {coordinator['contact_name']}"
    )
    st.caption(f"Correo de acceso: {auth_email}")

    with st.expander("Actualizar datos de coordinacion"):
        with st.form("coordinator_update", clear_on_submit=False):
            contact_name = st.text_input(
                "Nombre de contacto",
                value=coordinator["contact_name"] if coordinator else "",
            ).strip()
            contact_email = st.text_input(
                "Correo de contacto",
                value=auth_email,
                disabled=True,
            )
            contact_phone = st.text_input(
                "Telefono de contacto",
                value=coordinator["contact_phone"] if coordinator else "",
            ).strip()
            update_clicked = st.form_submit_button(
                "Actualizar datos de coordinacion", use_container_width=True
            )
            if update_clicked:
                if not contact_name or not contact_phone:
                    st.error("Complete nombre y telefono para actualizar.")
                else:
                    updated_rows = update_contact_for_code(
                        auth_code, contact_name, auth_email, contact_phone
                    )
                    if updated_rows > 0:
                        st.success("Datos de coordinacion actualizados.")
                    else:
                        st.success(
                            "Datos del coordinador actualizados (sin cita activa)."
                        )
                    st.rerun()

    slots_data = fetch_march_slots(year)
    _, last_day = calendar.monthrange(year, MARCH_MONTH)
    business_days = []
    for day in range(1, last_day + 1):
        current_day = date(year, MARCH_MONTH, day)
        if current_day.weekday() < 5:
            business_days.append(current_day)

    available_days = []
    for current_day in business_days:
        if any(
            slots_data.get((current_day.isoformat(), time_slot), {}).get("status")
            == "available"
            for time_slot in SLOTS
        ):
            available_days.append(current_day.isoformat())

    selected_day_iso = st.session_state.get("selected_day_iso")
    if not selected_day_iso or selected_day_iso not in {
        d.isoformat() for d in business_days
    }:
        st.session_state.selected_day_iso = (
            available_days[0] if available_days else business_days[0].isoformat()
        )
        selected_day_iso = st.session_state.selected_day_iso

    left_col, right_col = st.columns([1.0, 1.35])
    with left_col:
        st.markdown("#### Marzo")
        week_headers = st.columns(5)
        for i, wd in enumerate(["Lun", "Mar", "Mie", "Jue", "Vie"]):
            week_headers[i].markdown(f"**{wd}**")

        for week in calendar.monthcalendar(year, MARCH_MONTH):
            cols = st.columns(5)
            for i in range(5):
                with cols[i]:
                    day_num = week[i]
                    if day_num == 0:
                        st.write(" ")
                        continue

                    day_date = date(year, MARCH_MONTH, day_num)
                    day_iso = day_date.isoformat()
                    has_available = day_iso in available_days

                    if has_available:
                        button_type = "primary" if day_iso == selected_day_iso else "secondary"
                        if st.button(
                            f"{day_num:02d}",
                            key=f"day_pick_{day_iso}",
                            use_container_width=True,
                            type=button_type,
                        ):
                            st.session_state.selected_day_iso = day_iso
                            st.rerun()
                    else:
                        st.button(
                            f"{day_num:02d}",
                            key=f"day_disabled_{day_iso}",
                            use_container_width=True,
                            disabled=True,
                        )

    with right_col:
        selected_date = datetime.fromisoformat(selected_day_iso).date()
        weekday_label = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"][
            selected_date.weekday()
        ]
        st.markdown(f"#### {weekday_label} {selected_date.strftime('%d/%m/%Y')}")
        for time_slot in SLOTS:
            slot_info = slots_data.get(
                (selected_day_iso, time_slot),
                {"status": "available", "booked_by": None},
            )
            status = slot_info["status"]
            booked_by = slot_info["booked_by"]
            _, slot_col, _ = st.columns([0.05, 0.9, 0.05])

            with slot_col:
                if status == "booked":
                    st.button(
                        f"{time_slot} | {booked_by}",
                        key=f"taken_right_{selected_day_iso}_{time_slot}",
                        use_container_width=True,
                        disabled=True,
                    )
                elif status == "disabled":
                    st.button(
                        f"{time_slot} | Eliminado",
                        key=f"disabled_right_{selected_day_iso}_{time_slot}",
                        use_container_width=True,
                        disabled=True,
                    )
                else:
                    current_slot = (selected_day_iso, time_slot)
                    if pending_slot == current_slot:
                        st.markdown(
                            f'<div class="pending-slot">{time_slot} | Seleccionada</div>',
                            unsafe_allow_html=True,
                        )
                    elif st.button(
                        f"{time_slot} | Seleccionar",
                        key=f"pick_right_{selected_day_iso}_{time_slot}",
                        use_container_width=True,
                    ):
                        st.session_state.pending_slot = current_slot
                        st.rerun()

        save_clicked = st.button(
            "Guardar cita",
            key="save_appointment_below_slots",
            use_container_width=True,
            type="primary" if pending_slot else "secondary",
            disabled=not pending_slot,
        )

    if save_clicked:
        booked_ok, replaced_count = book_slot(
            pending_slot[0],
            pending_slot[1],
            auth_code,
            coordinator["contact_name"],
            coordinator["contact_email"],
            coordinator["contact_phone"],
        )
        if booked_ok:
            st.session_state.pending_slot = None
            if replaced_count > 0:
                st.success("Cita actualizada correctamente.")
            else:
                st.success("Cita guardada correctamente.")
        else:
            st.error(
                "Ese horario ya no esta disponible. Seleccione otra opcion y guarde de nuevo."
            )
        st.rerun()

    current_booking = fetch_current_booking_for_code(auth_code)
    if current_booking:
        st.markdown(
            f"<div class='current-booking-box'>Cita Actual: {current_booking['slot_date']} | {current_booking['time_slot']}</div>",
            unsafe_allow_html=True,
        )

def main():
    st.set_page_config(
        page_title="Citas para entrevista de uso de Inteligencia Artificial en el MRE",
        layout="wide",
    )

    year = datetime.now().year
    ensure_db_initialized(year, DB_PATH)

    if "auth_code" not in st.session_state:
        st.session_state.auth_code = None
    if "auth_email" not in st.session_state:
        st.session_state.auth_email = None
    if "pending_slot" not in st.session_state:
        st.session_state.pending_slot = None
    if "selected_day_iso" not in st.session_state:
        st.session_state.selected_day_iso = None
    if "registration_email" not in st.session_state:
        st.session_state.registration_email = None

    is_login = not st.session_state.auth_code
    apply_formal_styles(is_login=is_login)

    if is_login:
        welcome_view()
    else:
        render_persistent_logo()
        app_view(year)


if __name__ == "__main__":
    main()






