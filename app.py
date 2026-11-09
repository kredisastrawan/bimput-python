"""
BIMPUT - Sistem Input Nilai (Streamlit, 100% Python)
Jalankan: streamlit run app.py
"""
import hashlib
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
                PRIMARY KEY(user_id, mk_id));
            """
        )
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


# --------------------------------------------------------- halaman: masuk
def page_auth():
    st.title("🎓 BIMPUT - Input Nilai")
    st.caption("Sistem Informasi Akademik")
    tab_masuk, tab_daftar = st.tabs(["Masuk", "Daftar Mahasiswa"])

    with tab_masuk:
        with st.form("form_login"):
            nim = st.text_input("NIM (atau username admin)")
            pw = st.text_input("Password", type="password")
            ok = st.form_submit_button("Masuk", type="primary")
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
            ok = st.form_submit_button("Kirim Pendaftaran", type="primary")
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


# ------------------------------------------------------- halaman: admin
def page_persetujuan():
    st.header("Persetujuan Mahasiswa")
    rows = db_all(
        """SELECT u.*, GROUP_CONCAT(m.nama, ', ') AS mk
           FROM users u
           LEFT JOIN pilihan p ON p.user_id = u.id
           LEFT JOIN mata_kuliah m ON m.id = p.mk_id
           GROUP BY u.id ORDER BY u.created_at DESC"""
    )
    tabs = st.tabs(
        [f"Menunggu ({sum(r['status']=='Menunggu' for r in rows)})",
         f"Disetujui ({sum(r['status']=='Disetujui' for r in rows)})",
         f"Ditolak ({sum(r['status']=='Ditolak' for r in rows)})"]
    )
    for tab, status in zip(tabs, ["Menunggu", "Disetujui", "Ditolak"]):
        with tab:
            data = [r for r in rows if r["status"] == status]
            if not data:
                st.info("Tidak ada data.")
            for r in data:
                with st.container(border=True):
                    st.markdown(f"**{r['nama']}** · NIM `{r['nim']}`")
                    st.caption(f"{r['email'] or '-'} · diajukan {r['created_at']}")
                    st.write("Mata kuliah:", r["mk"] or "-")
                    c1, c2, _ = st.columns([1, 1, 4])
                    if status != "Disetujui" and c1.button("✅ Setujui", key=f"ok{r['id']}"):
                        db_exec("UPDATE users SET status='Disetujui' WHERE id=?", (r["id"],))
                        st.rerun()
                    if status != "Ditolak" and c2.button("❌ Tolak", key=f"no{r['id']}"):
                        db_exec("UPDATE users SET status='Ditolak' WHERE id=?", (r["id"],))
                        st.rerun()


def page_input_nilai():
    st.header("Input Nilai Makul")
    st.caption(
        "Bobot: Presensi 10% · Tugas 20% · UTS 30% · UAS 40%. "
        "Nilai akhir dan grade dihitung otomatis oleh Python."
    )
    mks = db_all("SELECT * FROM mata_kuliah ORDER BY nama")
    if not mks:
        st.info("Belum ada mata kuliah.")
        return
    label = {m["id"]: f'{m["nama"]} ({m["kode"]})' for m in mks}
    mk_id = st.selectbox("Mata Kuliah", list(label), format_func=label.get)
    st.write(f"Semester: **{SEMESTER}**")

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
    for s in studs:
        n = saved.get(s["id"])
        hadir = sessions_to_list(n["sessions"]) if n else [False] * TOTAL_PERTEMUAN
        rec = {"id": s["id"], "NIM": s["nim"], "Nama": s["nama"]}
        for i, h in enumerate(hadir, start=1):
            rec[f"P{i}"] = h
        rec["Tugas"] = float(n["tugas"]) if n and n["tugas"] is not None else 0.0
        rec["UTS"] = float(n["uts"]) if n and n["uts"] is not None else 0.0
        rec["UAS"] = float(n["uas"]) if n and n["uas"] is not None else 0.0
        recs.append(rec)
    df = pd.DataFrame(recs)

    cfg = {"id": None}
    for i in range(1, TOTAL_PERTEMUAN + 1):
        cfg[f"P{i}"] = st.column_config.CheckboxColumn(f"P{i}", help="Centang jika hadir")
    for k in ["Tugas", "UTS", "UAS"]:
        cfg[k] = st.column_config.NumberColumn(k, min_value=0, max_value=100, step=1)

    st.markdown("**Isi presensi (centang = hadir) dan nilai (0-100):**")
    edited = st.data_editor(
        df, key=f"editor_{mk_id}", column_config=cfg,
        disabled=["NIM", "Nama"], hide_index=True, use_container_width=True,
    )

    preview, hasil = [], []
    for _, r in edited.iterrows():
        hadir = sum(bool(r[f"P{i}"]) for i in range(1, TOTAL_PERTEMUAN + 1))
        h = hitung_nilai(hadir, float(r["Tugas"]), float(r["UTS"]), float(r["UAS"]))
        hasil.append((int(r["id"]), r, h))
        preview.append({"NIM": r["NIM"], "Nama": r["Nama"], "Presensi": h["presensi"],
                        "Nilai Akhir": h["nilai_akhir"], "Grade": h["grade"]})
    st.markdown("**Pratinjau hasil (dihitung Python):**")
    st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)

    if st.button("💾 Simpan Nilai", type="primary"):
        for uid, r, h in hasil:
            sessions = ",".join(
                "H" if bool(r[f"P{i}"]) else "-" for i in range(1, TOTAL_PERTEMUAN + 1)
            )
            db_exec(
                """INSERT INTO nilai(user_id,mk_id,semester,sessions,tugas,uts,uas,
                                     nilai_akhir,grade,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,mk_id) DO UPDATE SET
                     semester=excluded.semester, sessions=excluded.sessions,
                     tugas=excluded.tugas, uts=excluded.uts, uas=excluded.uas,
                     nilai_akhir=excluded.nilai_akhir, grade=excluded.grade,
                     updated_at=excluded.updated_at""",
                (uid, mk_id, SEMESTER, sessions, float(r["Tugas"]), float(r["UTS"]),
                 float(r["UAS"]), h["nilai_akhir"], h["grade"], now_str()),
            )
        st.success(f"{len(hasil)} nilai tersimpan. Halaman mahasiswa akan ter-update otomatis.")


def page_data_mahasiswa():
    st.header("Data Mahasiswa")
    rows = db_all(
        """SELECT u.nim AS NIM, u.nama AS Nama, u.email AS Email, u.status AS Status,
                  COUNT(p.mk_id) AS "Jumlah MK", u.created_at AS Terdaftar
           FROM users u LEFT JOIN pilihan p ON p.user_id=u.id
           GROUP BY u.id ORDER BY u.created_at DESC"""
    )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("Belum ada mahasiswa.")


def page_mata_kuliah():
    st.header("Data Mata Kuliah")
    mks = db_all("SELECT kode AS Kode, nama AS Nama, sks AS SKS FROM mata_kuliah ORDER BY nama")
    st.dataframe(pd.DataFrame(mks), hide_index=True, use_container_width=True)

    with st.form("form_mk", clear_on_submit=True):
        st.markdown("**Tambah mata kuliah**")
        c1, c2, c3 = st.columns([1, 3, 1])
        kode = c1.text_input("Kode")
        nama = c2.text_input("Nama")
        sks = c3.number_input("SKS", 1, 6, 3)
        if st.form_submit_button("Tambah"):
            if not kode.strip() or not nama.strip():
                st.error("Kode dan nama wajib diisi.")
            elif db_one("SELECT id FROM mata_kuliah WHERE kode=?", (kode.strip(),)):
                st.error("Kode sudah dipakai.")
            else:
                db_exec("INSERT INTO mata_kuliah(kode,nama,sks) VALUES (?,?,?)",
                        (kode.strip(), nama.strip(), int(sks)))
                st.rerun()


def admin_app():
    menu = st.sidebar.radio(
        "Menu",
        ["Persetujuan Mahasiswa", "Input Nilai", "Data Mahasiswa", "Data Mata Kuliah"],
    )
    {
        "Persetujuan Mahasiswa": page_persetujuan,
        "Input Nilai": page_input_nilai,
        "Data Mahasiswa": page_data_mahasiswa,
        "Data Mata Kuliah": page_mata_kuliah,
    }[menu]()


# ------------------------------------------------------ halaman: mahasiswa
@st.fragment(run_every=5)
def status_live(uid: int, status_awal: str):
    """Cek status tiap 5 detik. Jika berubah, muat ulang seluruh halaman."""
    u = db_one("SELECT status FROM users WHERE id=?", (uid,))
    if u and u["status"] != status_awal:
        st.rerun()
    st.caption(f"Terakhir diperiksa: {datetime.now(WIB).strftime('%H:%M:%S')} (otomatis tiap 5 detik)")


@st.fragment(run_every=5)
def nilai_live(uid: int):
    """Tabel nilai yang menyegarkan diri tiap 5 detik (realtime)."""
    rows = db_all(
        """SELECT m.nama, m.sks, n.sessions, n.tugas, n.uts, n.uas,
                  n.nilai_akhir, n.grade, n.updated_at
           FROM pilihan p JOIN mata_kuliah m ON m.id = p.mk_id
           LEFT JOIN nilai n ON n.user_id = p.user_id AND n.mk_id = p.mk_id
           WHERE p.user_id=? ORDER BY m.nama""",
        (uid,),
    )
    tabel = []
    for r in rows:
        if r["nilai_akhir"] is None:
            tabel.append({"Mata Kuliah": r["nama"], "SKS": r["sks"], "Presensi": "-",
                          "Tugas": "-", "UTS": "-", "UAS": "-",
                          "Nilai Akhir": "-", "Grade": "-"})
        else:
            hadir = sum(sessions_to_list(r["sessions"]))
            tabel.append({"Mata Kuliah": r["nama"], "SKS": r["sks"],
                          "Presensi": hitung_nilai(hadir, 0, 0, 0)["presensi"],
                          "Tugas": r["tugas"], "UTS": r["uts"], "UAS": r["uas"],
                          "Nilai Akhir": r["nilai_akhir"], "Grade": r["grade"]})
    st.dataframe(pd.DataFrame(tabel), hide_index=True, use_container_width=True)
    st.caption(f"🟢 Live · diperbarui {datetime.now(WIB).strftime('%H:%M:%S')}")


def student_app(uid: int):
    u = db_one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        st.session_state.pop("user", None)
        st.rerun()
        return
    st.title(f"Halo, {u['nama']} 👋")
    st.caption(f"NIM {u['nim']} · Semester {SEMESTER}")

    if u["status"] == "Menunggu":
        st.warning("**Pendaftaran sedang ditinjau admin.** Halaman ini akan berubah otomatis setelah disetujui.")
        mks = db_all(
            "SELECT m.nama FROM pilihan p JOIN mata_kuliah m ON m.id=p.mk_id WHERE p.user_id=?",
            (uid,),
        )
        st.write("Mata kuliah diajukan:", ", ".join(m["nama"] for m in mks))
        status_live(uid, u["status"])
    elif u["status"] == "Ditolak":
        st.error("Pendaftaran Anda ditolak. Hubungi admin akademik.")
        status_live(uid, u["status"])
    else:
        st.success("Akun Anda sudah disetujui.")
        st.subheader("Nilai Saya")
        nilai_live(uid)


# --------------------------------------------------------------------- main
def main():
    init_db()
    user = st.session_state.get("user")
    if not user:
        page_auth()
        return
    st.sidebar.markdown(f"**{user['nama']}**  \n_{user['role']}_")
    if st.sidebar.button("Keluar"):
        st.session_state.pop("user", None)
        st.rerun()
    if user["role"] == "admin":
        admin_app()
    else:
        student_app(user["id"])


if __name__ == "__main__":
    main()
