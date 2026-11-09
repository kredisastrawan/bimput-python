"""
BIMPUT - Sistem Input Nilai (Streamlit, 100% Python)
Jalankan: streamlit run app.py
"""
import hashlib
import html as _html
import math
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

# ------------------------------------------------------------- konfigurasi
DB_PATH = os.getenv("DB_PATH", "bimput.db")
SEMESTER = "Ganjil 2025/2026"
TOTAL_PERTEMUAN = 7
BOBOT = {"presensi": 0.10, "tugas": 0.20, "uts": 0.30, "uas": 0.40}
# (batas bawah, huruf). Di bawah 55 dibagi D (40-54.99) dan E (<40).
GRADE_TABLE = [(85, "A"), (78, "AB"), (70, "B"), (63, "BC"), (55, "C"), (40, "D"), (0, "E")]
SEED_MK = [
    ("MAT-101", "Matematika Dasar", 3),
    ("TIF-102", "Algoritma & Pemrograman", 4),
    ("TIF-201", "Basis Data", 3),
    ("TIF-202", "Struktur Data", 3),
    ("TIF-301", "Rekayasa Perangkat Lunak", 3),
    ("TIF-302", "Pemrograman Web", 3),
]
WIB = timezone(timedelta(hours=7))


def get_secret(name: str, default: str) -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(name, default)


def now_str() -> str:
    return datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- database
def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def db_all(sql, params=()):
    with closing(_conn()) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def db_one(sql, params=()):
    rows = db_all(sql, params)
    return rows[0] if rows else None


def db_exec(sql, params=()):
    with closing(_conn()) as c:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def init_db():
    with closing(_conn()) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nim TEXT UNIQUE NOT NULL,
                nama TEXT NOT NULL,
                email TEXT,
                salt TEXT NOT NULL,
                pw_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Menunggu',
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS mata_kuliah(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kode TEXT UNIQUE NOT NULL,
                nama TEXT NOT NULL,
                sks INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pilihan(
                user_id INTEGER NOT NULL,
                mk_id INTEGER NOT NULL,
                PRIMARY KEY(user_id, mk_id));
            CREATE TABLE IF NOT EXISTS nilai(
                user_id INTEGER NOT NULL,
                mk_id INTEGER NOT NULL,
                semester TEXT,
                sessions TEXT,
                tugas REAL, uts REAL, uas REAL,
                nilai_akhir INTEGER, grade TEXT, updated_at TEXT,
                nilai_manual INTEGER, grade_manual TEXT,
                PRIMARY KEY(user_id, mk_id));
            """
        )
        # migrasi untuk database lama yang belum punya kolom override
        cols = {r[1] for r in c.execute("PRAGMA table_info(nilai)").fetchall()}
        if "nilai_manual" not in cols:
            c.execute("ALTER TABLE nilai ADD COLUMN nilai_manual INTEGER")
        if "grade_manual" not in cols:
            c.execute("ALTER TABLE nilai ADD COLUMN grade_manual TEXT")
        if c.execute("SELECT COUNT(*) FROM mata_kuliah").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO mata_kuliah(kode, nama, sks) VALUES (?,?,?)", SEED_MK
            )
        c.commit()


# ------------------------------------------------------- logika penilaian
def round_half_up(x: float) -> int:
    """Pembulatan 0.5 naik (bukan pembulatan bankir bawaan Python)."""
    return int(math.floor(x + 0.5))


def hitung_grade(nilai_akhir: float) -> str:
    for batas, huruf in GRADE_TABLE:
        if nilai_akhir >= batas:
            return huruf
    return "E"


def hitung_nilai(hadir: int, tugas: float, uts: float, uas: float) -> dict:
    presensi_pct = round_half_up(hadir / TOTAL_PERTEMUAN * 100)
    nilai = round_half_up(
        presensi_pct * BOBOT["presensi"]
        + tugas * BOBOT["tugas"]
        + uts * BOBOT["uts"]
        + uas * BOBOT["uas"]
    )
    return {
        "presensi_pct": presensi_pct,
        "presensi": f"{hadir}/{TOTAL_PERTEMUAN} ({presensi_pct}%)",
        "nilai_akhir": nilai,
        "grade": hitung_grade(nilai),
    }


def resolve_nilai(hadir, tugas, uts, uas, nilai_manual=None, grade_manual=None) -> dict:
    """Gabungkan hitungan otomatis dengan pengaturan manual admin.
    - nilai_manual terisi  -> dipakai sebagai nilai akhir (dibatasi 0-100)
    - grade_manual terisi  -> dipakai sebagai grade
    - kosong               -> otomatis dari rumus"""
    h = hitung_nilai(hadir, tugas, uts, uas)
    if nilai_manual is None:
        nilai = h["nilai_akhir"]
    else:
        nilai = max(0, min(100, round_half_up(float(nilai_manual))))
    grade = grade_manual if grade_manual else hitung_grade(nilai)
    manual = nilai_manual is not None or bool(grade_manual)
    return {**h, "nilai_akhir": nilai, "grade": grade,
            "nilai_manual": None if nilai_manual is None else nilai,
            "grade_manual": grade_manual or None,
            "mode": "Manual" if manual else "Otomatis"}


def sessions_to_list(s) -> list:
    parts = (s or "").split(",") if s else []
    parts = (parts + ["-"] * TOTAL_PERTEMUAN)[:TOTAL_PERTEMUAN]
    return [p == "H" for p in parts]


# ------------------------------------------------------------------- akun
def hash_pw(pw: str, salt_hex: str = None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 100_000)
    return salt.hex(), h.hex()


def register_user(nim, nama, email, pw, mk_ids):
    if db_one("SELECT id FROM users WHERE nim=?", (nim,)):
        return "NIM sudah terdaftar. Silakan masuk."
    salt, h = hash_pw(pw)
    uid = db_exec(
        "INSERT INTO users(nim,nama,email,salt,pw_hash,status,created_at) "
        "VALUES (?,?,?,?,?,'Menunggu',?)",
        (nim, nama, email, salt, h, now_str()),
    )
    for mk in mk_ids:
        db_exec("INSERT OR IGNORE INTO pilihan(user_id, mk_id) VALUES (?,?)", (uid, mk))
    return None


def login(nim: str, pw: str):
    admin_user = get_secret("ADMIN_USER", "admin")
    admin_pw = get_secret("ADMIN_PASSWORD", "admin123")
    if secrets.compare_digest(nim, admin_user) and secrets.compare_digest(pw, admin_pw):
        return {"role": "admin", "nama": "Admin"}
    u = db_one("SELECT * FROM users WHERE nim=?", (nim,))
    if not u:
        return None
    _, h = hash_pw(pw, u["salt"])
    if secrets.compare_digest(h, u["pw_hash"]):
        return {"role": "mahasiswa", "id": u["id"], "nama": u["nama"]}
    return None


# ================================================================= TAMPILAN
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');
:root{--blue:#0084C7;--blue-d:#0074B0;--ink:#0F172A;--muted:#64748B;--line:#E2E8F0;}
.stApp, .stMarkdown, p, label, h1, h2, h3, h4, input, button, textarea,
[data-testid="stMarkdownContainer"]{font-family:'Plus Jakarta Sans',-apple-system,'Segoe UI',sans-serif;}
.stApp{background:linear-gradient(180deg,#F8FAFC 0%,#EAF3FB 100%);color:var(--ink);}
header[data-testid="stHeader"]{background:transparent;}
footer{visibility:hidden;}
.block-container{max-width:1120px;padding-top:2.4rem;padding-bottom:4rem;}
h1,h2,h3{font-weight:800 !important;letter-spacing:-0.02em;color:var(--ink);}

/* sidebar */
[data-testid="stSidebar"]{background:#fff;border-right:1px solid #EDF2F7;box-shadow:8px 0 32px -16px rgba(0,132,199,.12);}
[data-testid="stSidebar"] [role="radiogroup"]{gap:6px;}
[data-testid="stSidebar"] [role="radiogroup"] label{padding:12px 14px;border-radius:14px;width:100%;cursor:pointer;transition:all .15s;}
[data-testid="stSidebar"] [role="radiogroup"] label:hover{background:#F1F7FC;}
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child{display:none;}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:var(--blue);box-shadow:0 12px 24px -8px rgba(0,132,199,.5);}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) p{color:#fff !important;font-weight:700;}
[data-testid="stSidebar"] [role="radiogroup"] label p{font-weight:600;color:#334155;}

/* tombol */
.stButton>button,.stFormSubmitButton>button{border-radius:14px;font-weight:700;padding:.55rem 1.3rem;border:1px solid var(--line);background:#fff;color:var(--ink);transition:all .15s;}
.stButton>button:hover,.stFormSubmitButton>button:hover{border-color:var(--blue);color:var(--blue);}
[data-testid^="stBaseButton-primary"],button[kind^="primary"]{background:var(--blue) !important;border:none !important;color:#fff !important;box-shadow:0 12px 28px -6px rgba(0,132,199,.38);}
[data-testid^="stBaseButton-primary"]:hover,button[kind^="primary"]:hover{background:var(--blue-d) !important;color:#fff !important;transform:translateY(-1px);}

/* form, input, kartu */
[data-testid="stForm"]{background:#fff;border:1px solid #EEF2F7;border-radius:22px;padding:1.6rem;box-shadow:0 10px 30px -5px rgba(0,132,199,.07),0 4px 12px -2px rgba(15,23,42,.03);}
[class*="st-key-card"]{background:#fff;border:1px solid #EEF2F7 !important;border-radius:22px;padding:1.2rem 1.4rem;box-shadow:0 10px 30px -5px rgba(0,132,199,.07),0 4px 12px -2px rgba(15,23,42,.03);}
[data-baseweb="input"],[data-baseweb="base-input"],[data-baseweb="select"]>div{border-radius:14px !important;}
[data-testid="stAlert"]{border-radius:16px;}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{border-radius:16px;overflow:hidden;border:1px solid #EEF2F7;}
button[data-baseweb="tab"] p{font-weight:700;}

/* komponen HTML */
.hero{display:flex;align-items:center;gap:18px;background:#fff;border-radius:24px;padding:22px 26px;margin-bottom:22px;box-shadow:0 10px 30px -5px rgba(0,132,199,.07),0 4px 12px -2px rgba(15,23,42,.03);}
.hero-ico{width:58px;height:58px;border-radius:18px;background:#E6F3FB;display:flex;align-items:center;justify-content:center;font-size:28px;flex:none;}
.hero h1{margin:0;font-size:1.65rem;line-height:1.2;padding:0;}
.hero .sub{color:var(--muted);font-size:.95rem;margin-top:4px;}
.pill{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.74rem;font-weight:700;white-space:nowrap;}
.pill-wait{background:#FEF3C7;color:#B45309;}.pill-ok{background:#D1FAE5;color:#047857;}
.pill-no{background:#FEE2E2;color:#B91C1C;}.pill-blue{background:#E0F2FE;color:#0369A1;}
.pill-gray{background:#F1F5F9;color:#475569;}
.g-A{background:#D1FAE5;color:#047857;}.g-AB{background:#E0F2FE;color:#0369A1;}
.g-B{background:#DBEAFE;color:#1D4ED8;}.g-BC{background:#FEF3C7;color:#B45309;}
.g-C{background:#FFEDD5;color:#C2410C;}.g-D{background:#FEE2E2;color:#B91C1C;}.g-E{background:#FECACA;color:#991B1B;}
.nim{font-family:'JetBrains Mono',monospace;font-size:.82rem;font-weight:600;color:var(--blue);background:#EBF4FE;padding:3px 10px;border-radius:8px;}
.mk-chip{display:inline-block;border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 13px;font-size:.82rem;font-weight:500;margin:0 6px 6px 0;color:#334155;}
.meta{color:var(--muted);font-size:.85rem;margin:6px 0 10px;}
.lbl{font-family:'JetBrains Mono',monospace;font-size:.7rem;letter-spacing:.06em;color:#94A3B8;margin:6px 0 8px;}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.nama{font-weight:800;font-size:1.08rem;}
.tbl-wrap{background:#fff;border-radius:20px;overflow-x:auto;box-shadow:0 10px 30px -5px rgba(0,132,199,.07),0 4px 12px -2px rgba(15,23,42,.03);}
.tbl{width:100%;border-collapse:collapse;font-size:.9rem;}
.tbl th{text-align:left;padding:14px 16px;color:#475569;font-weight:700;background:#F8FAFC;border-bottom:1px solid #EEF2F7;white-space:nowrap;}
.tbl td{padding:13px 16px;border-bottom:1px solid #F1F5F9;color:#1E293B;}
.tbl tr:last-child td{border-bottom:none;}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:18px;}
.stat{background:#fff;border-radius:20px;padding:16px 18px;box-shadow:0 10px 30px -5px rgba(0,132,199,.07);}
.stat .v{font-size:1.7rem;font-weight:800;color:var(--blue);line-height:1.1;}
.stat .k{color:var(--muted);font-size:.8rem;font-weight:600;margin-top:4px;}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:8px;}
.brand .logo{width:46px;height:46px;border-radius:15px;background:linear-gradient(135deg,#38bdf8,#0084C7);display:flex;align-items:center;justify-content:center;font-size:22px;box-shadow:0 12px 28px -6px rgba(0,132,199,.45);}
.brand .t{font-weight:800;font-size:1.1rem;line-height:1.15;}.brand .s{color:var(--muted);font-size:.8rem;}
.userchip{background:#F1F7FC;border-radius:14px;padding:10px 14px;margin:6px 0 16px;font-size:.85rem;}
.userchip b{display:block;font-size:.95rem;}.userchip span{color:var(--muted);}
.center{text-align:center;}
.status-card{background:#fff;border-radius:28px;padding:34px 30px;box-shadow:0 20px 50px -10px rgba(0,132,199,.12),0 4px 12px -2px rgba(15,23,42,.04);text-align:center;}
.status-ico{width:78px;height:78px;border-radius:24px;background:#E6F3FB;margin:0 auto 14px;display:flex;align-items:center;justify-content:center;font-size:36px;}
.status-card h2{margin:10px 0 6px;font-size:1.6rem;}.status-card .desc{color:var(--muted);margin-bottom:18px;}
.sync{display:flex;align-items:center;gap:10px;background:#F1F7FC;border:1px solid #DCEBF7;border-radius:16px;padding:12px 16px;text-align:left;font-size:.85rem;margin-bottom:16px;}
.dot{width:10px;height:10px;border-radius:50%;background:#10B981;flex:none;box-shadow:0 0 0 4px rgba(16,185,129,.18);}
.detail{background:#F8FAFC;border:1px solid #EEF2F7;border-radius:18px;padding:6px 18px 14px;text-align:left;}
.detail .r{display:flex;justify-content:space-between;gap:12px;padding:11px 0;border-bottom:1px solid #EEF2F7;font-size:.9rem;}
.detail .r span{color:var(--muted);}.detail .r b{font-weight:700;}
.live{color:var(--muted);font-size:.8rem;margin-top:10px;}
</style>
"""


class Safe(str):
    """Penanda: string ini sudah aman (HTML buatan kita sendiri)."""


def esc(x) -> str:
    if isinstance(x, Safe):
        return str(x)
    return _html.escape("" if x is None else str(x))


def md(html_str: str):
    st.markdown(html_str, unsafe_allow_html=True)


def pill(text, kind="gray") -> Safe:
    return Safe(f'<span class="pill pill-{kind}">{esc(text)}</span>')


def status_pill(status) -> Safe:
    kind = {"Menunggu": "wait", "Disetujui": "ok", "Ditolak": "no"}.get(status, "gray")
    return pill(status, kind)


def grade_pill(g) -> Safe:
    if not g or g == "-":
        return Safe("-")
    return Safe(f'<span class="pill g-{esc(g)}">{esc(g)}</span>')


def chips(items) -> Safe:
    return Safe("".join(f'<span class="mk-chip">{esc(i)}</span>' for i in items))


def html_table(headers, rows) -> Safe:
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return Safe(f'<div class="tbl-wrap"><table class="tbl"><tr>{head}</tr>{body}</table></div>')


def page_header(icon, title, subtitle, badge=None):
    b = f" {badge}" if badge else ""
    md(f'<div class="hero"><div class="hero-ico">{icon}</div>'
       f'<div><h1>{esc(title)}{b}</h1><div class="sub">{esc(subtitle)}</div></div></div>')


# ------------------------------------------------------------ halaman: masuk
def page_auth():
    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        md('<div class="center brand" style="justify-content:center;margin-bottom:18px">'
           '<div class="logo">🎓</div><div style="text-align:left"><div class="t">Input Nilai</div>'
           '<div class="s">Akademik System</div></div></div>')
        tab_masuk, tab_daftar = st.tabs(["Masuk", "Daftar Mahasiswa"])

        with tab_masuk:
            with st.form("form_login"):
                nim = st.text_input("NIM (atau username admin)")
                pw = st.text_input("Password", type="password")
                ok = st.form_submit_button("Masuk", type="primary", use_container_width=True)
            if ok:
                user = login(nim.strip(), pw)
                if user:
                    st.session_state["user"] = user
                    st.rerun()
                else:
                    st.error("NIM/username atau password salah.")

        with tab_daftar:
            mks = db_all("SELECT * FROM mata_kuliah ORDER BY nama")
            label = {m["id"]: f'{m["nama"]} ({m["sks"]} SKS)' for m in mks}
            with st.form("form_daftar"):
                nama = st.text_input("Nama lengkap")
                nim = st.text_input("NIM")
                email = st.text_input("Email (opsional)")
                pw = st.text_input("Password (minimal 6 karakter)", type="password")
                pilih = st.multiselect(
                    "Mata kuliah yang diambil", options=list(label), format_func=label.get
                )
                ok = st.form_submit_button("Kirim Pendaftaran", type="primary", use_container_width=True)
            if ok:
                if not nama.strip() or not nim.strip():
                    st.error("Nama dan NIM wajib diisi.")
                elif len(pw) < 6:
                    st.error("Password minimal 6 karakter.")
                elif not pilih:
                    st.error("Pilih minimal satu mata kuliah.")
                else:
                    err = register_user(nim.strip(), nama.strip(), email.strip(), pw, pilih)
                    if err:
                        st.error(err)
                    else:
                        st.success("Pendaftaran terkirim. Silakan masuk dan tunggu persetujuan admin.")


# -------------------------------------------------------------- halaman: admin
def page_persetujuan():
    rows = db_all("SELECT * FROM users ORDER BY created_at DESC")
    mk_map = {}
    for r in db_all(
        "SELECT p.user_id, m.nama FROM pilihan p JOIN mata_kuliah m ON m.id=p.mk_id ORDER BY m.nama"
    ):
        mk_map.setdefault(r["user_id"], []).append(r["nama"])
    n_wait = sum(r["status"] == "Menunggu" for r in rows)
    badge = pill(f"{n_wait} Menunggu ACC", "wait") if n_wait else None
    page_header("👥", "Persetujuan Mahasiswa",
                "Verifikasi pendaftaran akun mahasiswa baru dan persetujuan pengambilan mata kuliah",
                badge)

    tabs = st.tabs(
        [f"Menunggu ({n_wait})",
         f"Disetujui ({sum(r['status']=='Disetujui' for r in rows)})",
         f"Ditolak ({sum(r['status']=='Ditolak' for r in rows)})"]
    )
    for tab, status in zip(tabs, ["Menunggu", "Disetujui", "Ditolak"]):
        with tab:
            data = [r for r in rows if r["status"] == status]
            if not data:
                st.info("Tidak ada data.")
            for r in data:
                mks = mk_map.get(r["id"], [])
                with st.container(key=f"card_{r['id']}_{status}"):
                    md(f'<div class="row"><span class="nim">{esc(r["nim"])}</span>'
                       f'<span class="nama">{esc(r["nama"])}</span>{status_pill(status)}</div>'
                       f'<div class="meta">✉ {esc(r["email"] or "-")} · Diajukan: {esc(r["created_at"])}</div>'
                       f'<div class="lbl">MATA KULIAH PILIHAN ({len(mks)} KELAS):</div>{chips(mks)}')
                    c1, c2, _ = st.columns([1, 1, 3])
                    if status != "Disetujui" and c1.button("✅ Setujui", key=f"ok{r['id']}", type="primary"):
                        db_exec("UPDATE users SET status='Disetujui' WHERE id=?", (r["id"],))
                        st.rerun()
                    if status != "Ditolak" and c2.button("✖ Tolak", key=f"no{r['id']}"):
                        db_exec("UPDATE users SET status='Ditolak' WHERE id=?", (r["id"],))
                        st.rerun()


def page_input_nilai():
    page_header("📝", "Input Nilai Makul",
                "Kelola dan input nilai mata kuliah dengan mudah dan cepat. "
                "Bobot: Presensi 10% · Tugas 20% · UTS 30% · UAS 40%.")
    mks = db_all("SELECT * FROM mata_kuliah ORDER BY nama")
    if not mks:
        st.info("Belum ada mata kuliah.")
        return
    label = {m["id"]: f'{m["nama"]} ({m["kode"]})' for m in mks}
    with st.container(key="card_pilih"):
        c1, c2 = st.columns([3, 1])
        mk_id = c1.selectbox("Mata Kuliah", list(label), format_func=label.get)
        c2.text_input("Semester", value=SEMESTER, disabled=True)

    studs = db_all(
        """SELECT u.id, u.nim, u.nama FROM users u
           JOIN pilihan p ON p.user_id = u.id
           WHERE p.mk_id=? AND u.status='Disetujui' ORDER BY u.nim""",
        (mk_id,),
    )
    if not studs:
        st.info("Belum ada mahasiswa yang disetujui untuk mata kuliah ini.")
        return

    saved = {r["user_id"]: r for r in db_all("SELECT * FROM nilai WHERE mk_id=?", (mk_id,))}
    recs = []
    for s_ in studs:
        n = saved.get(s_["id"])
        hadir = sessions_to_list(n["sessions"]) if n else [False] * TOTAL_PERTEMUAN
        rec = {"id": s_["id"], "NIM": s_["nim"], "Nama": s_["nama"]}
        for i, h in enumerate(hadir, start=1):
            rec[f"P{i}"] = h
        rec["Tugas"] = float(n["tugas"]) if n and n["tugas"] is not None else 0.0
        rec["UTS"] = float(n["uts"]) if n and n["uts"] is not None else 0.0
        rec["UAS"] = float(n["uas"]) if n and n["uas"] is not None else 0.0
        rec["Nilai Akhir"] = n["nilai_manual"] if n and n["nilai_manual"] is not None else None
        rec["Grade"] = n["grade_manual"] if n and n["grade_manual"] else None
        recs.append(rec)
    df = pd.DataFrame(recs)
    df["Nilai Akhir"] = pd.to_numeric(df["Nilai Akhir"], errors="coerce")
    df["Grade"] = df["Grade"].astype(object)

    cfg = {"id": None}
    for i in range(1, TOTAL_PERTEMUAN + 1):
        cfg[f"P{i}"] = st.column_config.CheckboxColumn(f"P{i}", help="Centang jika hadir")
    for k in ["Tugas", "UTS", "UAS"]:
        cfg[k] = st.column_config.NumberColumn(k, min_value=0, max_value=100, step=1)
    cfg["Nilai Akhir"] = st.column_config.NumberColumn(
        "Nilai Akhir", min_value=0, max_value=100, step=1,
        help="Kosongkan = dihitung otomatis. Isi = pakai angka ini.",
    )
    cfg["Grade"] = st.column_config.SelectboxColumn(
        "Grade", options=[g for _, g in GRADE_TABLE],
        help="Kosongkan = otomatis dari nilai akhir. Pilih = pakai grade ini.",
    )

    st.markdown(
        "**Isi presensi (centang = hadir) dan nilai (0-100).** "
        "Kolom **Nilai Akhir** dan **Grade** boleh dikosongkan "
        "(tombol Delete/Backspace) agar kembali otomatis."
    )
    edited = st.data_editor(
        df, key=f"editor_{mk_id}", column_config=cfg,
        disabled=["NIM", "Nama"], hide_index=True, use_container_width=True,
    )

    preview, hasil = [], []
    for _, r in edited.iterrows():
        hadir = sum(bool(r[f"P{i}"]) for i in range(1, TOTAL_PERTEMUAN + 1))
        nm = None if pd.isna(r["Nilai Akhir"]) else float(r["Nilai Akhir"])
        gm = None if (pd.isna(r["Grade"]) or r["Grade"] == "") else str(r["Grade"])
        res = resolve_nilai(hadir, float(r["Tugas"]), float(r["UTS"]), float(r["UAS"]), nm, gm)
        hasil.append((int(r["id"]), r, res))
        preview.append([r["NIM"], r["Nama"], res["presensi"], res["nilai_akhir"],
                        grade_pill(res["grade"]),
                        pill(res["mode"], "blue" if res["mode"] == "Manual" else "gray")])
    st.markdown("**Pratinjau hasil akhir**")
    md(html_table(["NIM", "Nama", "Presensi", "Nilai Akhir", "Grade", "Mode"], preview))

    st.write("")
    if st.button("💾 Simpan Nilai", type="primary"):
        for uid, r, res in hasil:
            sessions = ",".join(
                "H" if bool(r[f"P{i}"]) else "-" for i in range(1, TOTAL_PERTEMUAN + 1)
            )
            db_exec(
                """INSERT INTO nilai(user_id,mk_id,semester,sessions,tugas,uts,uas,
                                     nilai_akhir,grade,updated_at,nilai_manual,grade_manual)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,mk_id) DO UPDATE SET
                     semester=excluded.semester, sessions=excluded.sessions,
                     tugas=excluded.tugas, uts=excluded.uts, uas=excluded.uas,
                     nilai_akhir=excluded.nilai_akhir, grade=excluded.grade,
                     updated_at=excluded.updated_at,
                     nilai_manual=excluded.nilai_manual, grade_manual=excluded.grade_manual""",
                (uid, mk_id, SEMESTER, sessions, float(r["Tugas"]), float(r["UTS"]),
                 float(r["UAS"]), res["nilai_akhir"], res["grade"], now_str(),
                 res["nilai_manual"], res["grade_manual"]),
            )
        st.success(f"{len(hasil)} nilai tersimpan. Halaman mahasiswa akan ter-update otomatis.")


def page_data_mahasiswa():
    page_header("🎓", "Data Mahasiswa", "Daftar seluruh mahasiswa yang pernah mendaftar")
    rows = db_all(
        """SELECT u.nim, u.nama, u.email, u.status, COUNT(p.mk_id) AS jml, u.created_at
           FROM users u LEFT JOIN pilihan p ON p.user_id=u.id
           GROUP BY u.id ORDER BY u.created_at DESC"""
    )
    if not rows:
        st.info("Belum ada mahasiswa.")
        return
    md(html_table(
        ["NIM", "Nama", "Email", "Status", "Jumlah MK", "Terdaftar"],
        [[Safe(f'<span class="nim">{esc(r["nim"])}</span>'), r["nama"], r["email"] or "-",
          status_pill(r["status"]), r["jml"], r["created_at"]] for r in rows],
    ))


def page_mata_kuliah():
    page_header("📚", "Data Mata Kuliah", "Kelola daftar mata kuliah yang tersedia")
    mks = db_all("SELECT kode, nama, sks FROM mata_kuliah ORDER BY nama")
    md(html_table(["Kode", "Nama", "SKS"],
                  [[Safe(f'<span class="nim">{esc(m["kode"])}</span>'), m["nama"], m["sks"]] for m in mks]))
    st.write("")
    with st.form("form_mk", clear_on_submit=True):
        st.markdown("**Tambah mata kuliah**")
        c1, c2, c3 = st.columns([1, 3, 1])
        kode = c1.text_input("Kode")
        nama = c2.text_input("Nama")
        sks = c3.number_input("SKS", 1, 6, 3)
        if st.form_submit_button("Tambah", type="primary"):
            if not kode.strip() or not nama.strip():
                st.error("Kode dan nama wajib diisi.")
            elif db_one("SELECT id FROM mata_kuliah WHERE kode=?", (kode.strip(),)):
                st.error("Kode sudah dipakai.")
            else:
                db_exec("INSERT INTO mata_kuliah(kode,nama,sks) VALUES (?,?,?)",
                        (kode.strip(), nama.strip(), int(sks)))
                st.rerun()


MENU = {
    "👥  Persetujuan Mahasiswa": page_persetujuan,
    "📝  Input Nilai": page_input_nilai,
    "🎓  Data Mahasiswa": page_data_mahasiswa,
    "📚  Data Mata Kuliah": page_mata_kuliah,
}


def admin_app():
    menu = st.sidebar.radio("Menu", list(MENU), label_visibility="collapsed")
    MENU[menu]()


# ----------------------------------------------------------- halaman: mahasiswa
@st.fragment(run_every=5)
def status_live(uid: int, status_awal: str):
    """Cek status tiap 5 detik. Jika berubah, muat ulang seluruh halaman."""
    u = db_one("SELECT status FROM users WHERE id=?", (uid,))
    if u and u["status"] != status_awal:
        st.rerun()
    md(f'<div class="sync"><span class="dot"></span><div><b>Sinkronisasi status</b><br>'
       f'Terakhir diperiksa: {datetime.now(WIB).strftime("%H:%M:%S")} (otomatis tiap 5 detik)</div></div>')


@st.fragment(run_every=5)
def nilai_live(uid: int):
    """Kartu ringkasan + tabel nilai yang menyegarkan diri tiap 5 detik (realtime)."""
    rows = db_all(
        """SELECT m.nama, m.sks, n.sessions, n.tugas, n.uts, n.uas,
                  n.nilai_akhir, n.grade
           FROM pilihan p JOIN mata_kuliah m ON m.id = p.mk_id
           LEFT JOIN nilai n ON n.user_id = p.user_id AND n.mk_id = p.mk_id
           WHERE p.user_id=? ORDER BY m.nama""",
        (uid,),
    )
    dinilai = [r for r in rows if r["nilai_akhir"] is not None]
    rata = round(sum(r["nilai_akhir"] for r in dinilai) / len(dinilai), 1) if dinilai else "-"
    md('<div class="stats">'
       f'<div class="stat"><div class="v">{len(rows)}</div><div class="k">Mata Kuliah</div></div>'
       f'<div class="stat"><div class="v">{sum(r["sks"] for r in rows)}</div><div class="k">Total SKS</div></div>'
       f'<div class="stat"><div class="v">{len(dinilai)}/{len(rows)}</div><div class="k">Sudah Dinilai</div></div>'
       f'<div class="stat"><div class="v">{rata}</div><div class="k">Rata-rata Nilai</div></div></div>')

    tabel = []
    for r in rows:
        if r["nilai_akhir"] is None:
            tabel.append([r["nama"], r["sks"], "-", "-", "-", "-", "-", "-"])
        else:
            hadir = sum(sessions_to_list(r["sessions"]))
            tabel.append([r["nama"], r["sks"], hitung_nilai(hadir, 0, 0, 0)["presensi"],
                          r["tugas"], r["uts"], r["uas"], r["nilai_akhir"], grade_pill(r["grade"])])
    md(html_table(["Mata Kuliah", "SKS", "Presensi", "Tugas", "UTS", "UAS", "Nilai Akhir", "Grade"], tabel))
    md(f'<div class="live"><span class="dot" style="display:inline-block;margin-right:8px"></span>'
       f'Live · diperbarui {datetime.now(WIB).strftime("%H:%M:%S")}</div>')


def student_app(uid: int):
    u = db_one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        st.session_state.pop("user", None)
        st.rerun()
        return

    if u["status"] == "Disetujui":
        page_header("🎓", f"Halo, {u['nama']}", f"NIM {u['nim']} · Semester {SEMESTER}",
                    pill("Akun disetujui", "ok"))
        nilai_live(uid)
        return

    mks = db_all(
        "SELECT m.nama, m.sks FROM pilihan p JOIN mata_kuliah m ON m.id=p.mk_id WHERE p.user_id=? ORDER BY m.nama",
        (uid,),
    )
    menunggu = u["status"] == "Menunggu"
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        md('<div class="status-card">'
           f'<div class="status-ico">{"⏳" if menunggu else "⚠️"}</div>'
           f'{pill("STATUS: MENUNGGU PERSETUJUAN ADMIN", "blue") if menunggu else pill("STATUS: DITOLAK", "no")}'
           f'<h2>{"Pendaftaran Sedang Ditinjau" if menunggu else "Pendaftaran Ditolak"}</h2>'
           f'<div class="desc">{"Data Anda telah terkirim ke administrator akademik. Halaman ini akan berubah otomatis setelah disetujui." if menunggu else "Hubungi admin akademik untuk informasi lebih lanjut."}</div>'
           '</div>')
        st.write("")
        status_live(uid, u["status"])
        md('<div class="detail">'
           f'<div class="r"><span>Akun Terdaftar</span><b>{esc(u["email"] or "-")}</b></div>'
           f'<div class="r"><span>Nama Mahasiswa</span><b>{esc(u["nama"])}</b></div>'
           f'<div class="r"><span>NIM</span><b class="nim">{esc(u["nim"])}</b></div>'
           f'<div class="lbl" style="margin-top:12px">MATA KULIAH DIAJUKAN ({len(mks)} KELAS · {sum(m["sks"] for m in mks)} SKS):</div>'
           f'{chips([m["nama"] for m in mks])}'
           f'<div class="live" style="text-align:right">Diajukan pada: {esc(u["created_at"])}</div></div>')


# ----------------------------------------------------------------------- main
def main():
    init_db()
    md(CSS)
    user = st.session_state.get("user")
    if not user:
        page_auth()
        return
    with st.sidebar:
        md('<div class="brand"><div class="logo">🎓</div><div><div class="t">Input Nilai</div>'
           '<div class="s">Akademik System</div></div></div>'
           f'<div class="userchip"><b>{esc(user["nama"])}</b><span>{esc(user["role"].capitalize())}</span></div>')
    if user["role"] == "admin":
        admin_app()
    else:
        student_app(user["id"])
    if st.sidebar.button("Keluar", use_container_width=True):
        st.session_state.pop("user", None)
        st.rerun()


if __name__ == "__main__":
    main()
