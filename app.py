"""
BIMPUT - Sistem Input Nilai (Streamlit, 100% Python, tanpa HTML/CSS)
Jalankan: streamlit run app.py
"""
import hashlib
import math
import os
import re
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


EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def validasi_profil(uid, nim, nama, email):
    """Kembalikan pesan error (str) atau None jika data valid."""
    if not nim or not nama:
        return "Nama dan NIM wajib diisi."
    if re.search(r"\s", nim):
        return "NIM tidak boleh mengandung spasi."
    if email and not EMAIL_RE.fullmatch(email):
        return "Format email tidak valid."
    if nim == get_secret("ADMIN_USER", "admin"):
        return "NIM ini tidak dapat dipakai."
    if db_one("SELECT id FROM users WHERE nim=? AND id<>?", (nim, uid)):
        return "NIM sudah dipakai mahasiswa lain."
    return None


def update_profil(uid, nim, nama, email):
    """Ubah data diri. Nilai & mata kuliah terhubung lewat id, jadi aman saat NIM berubah."""
    nim, nama, email = nim.strip(), nama.strip(), email.strip()
    err = validasi_profil(uid, nim, nama, email)
    if err:
        return err
    db_exec("UPDATE users SET nim=?, nama=?, email=? WHERE id=?", (nim, nama, email, uid))
    return None


def set_password(uid, pw):
    salt, h = hash_pw(pw)
    db_exec("UPDATE users SET salt=?, pw_hash=? WHERE id=?", (salt, h, uid))


def ganti_password(uid, lama, baru, ulang):
    u = db_one("SELECT salt, pw_hash FROM users WHERE id=?", (uid,))
    if not u or not secrets.compare_digest(hash_pw(lama, u["salt"])[1], u["pw_hash"]):
        return "Password lama salah."
    if len(baru) < 6:
        return "Password baru minimal 6 karakter."
    if baru != ulang:
        return "Konfirmasi password tidak sama."
    set_password(uid, baru)
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
# Semua tampilan memakai komponen bawaan Streamlit (tanpa HTML/CSS/SVG),
# sehingga otomatis mengikuti tema terang maupun gelap.
STATUS_WARNA = {"Menunggu": "orange", "Disetujui": "green", "Ditolak": "red"}
GRADE_WARNA = {"A": "green", "AB": "blue", "B": "blue", "BC": "orange",
               "C": "orange", "D": "red", "E": "red"}


def aman(teks) -> str:
    """Escape karakter markdown pada teks dari pengguna."""
    return re.sub(r"([\\`*_{}\[\]<>#|$~])", r"\\\1", "" if teks is None else str(teks))


def badge(teks, warna="gray") -> str:
    return f":{warna}-badge[{aman(teks)}]"


def badge_status(status) -> str:
    return badge(status, STATUS_WARNA.get(status, "gray"))


def badge_mk(daftar) -> str:
    return " ".join(badge(x, "blue") for x in daftar) if daftar else "_Belum ada mata kuliah._"


def page_header(icon_name, title, subtitle):
    st.title(f":material/{icon_name}: {title}")
    st.caption(subtitle)


def flash(text):
    """Pesan yang tetap tampil setelah st.rerun()."""
    st.session_state["flash"] = text


def show_flash():
    msg = st.session_state.pop("flash", None)
    if msg:
        st.success(msg)


def tabel(df: pd.DataFrame, **kw):
    st.dataframe(df, hide_index=True, width="stretch", **kw)


# ------------------------------------------------------------ halaman: masuk
def page_auth():
    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        st.markdown("## :material/school: Input Nilai")
        st.caption("Akademik System")
        tab_masuk, tab_daftar = st.tabs(["Masuk", "Daftar Mahasiswa"])

        with tab_masuk:
            with st.form("form_login"):
                nim = st.text_input("NIM (atau username admin)")
                pw = st.text_input("Password", type="password")
                ok = st.form_submit_button("Masuk", type="primary", width="stretch")
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
                ok = st.form_submit_button("Kirim Pendaftaran", type="primary", width="stretch")
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
    page_header("how_to_reg", "Persetujuan Mahasiswa",
                "Verifikasi pendaftaran akun mahasiswa baru dan persetujuan pengambilan mata kuliah")
    show_flash()
    if n_wait:
        st.warning(f"{n_wait} pendaftaran menunggu persetujuan.")

    tabs = st.tabs(
        [f"Menunggu ({n_wait})",
         f"Disetujui ({sum(r['status'] == 'Disetujui' for r in rows)})",
         f"Ditolak ({sum(r['status'] == 'Ditolak' for r in rows)})"]
    )
    for tab, status in zip(tabs, ["Menunggu", "Disetujui", "Ditolak"]):
        with tab:
            data = [r for r in rows if r["status"] == status]
            if not data:
                st.info("Tidak ada data.")
            for r in data:
                mks = mk_map.get(r["id"], [])
                with st.container(border=True, key=f"card_{r['id']}_{status}"):
                    st.markdown(f"`{r['nim']}` **{aman(r['nama'])}** {badge_status(status)}")
                    st.caption(f":material/mail: {r['email'] or '-'}  ·  Diajukan: {r['created_at']}")
                    st.markdown(f"**Mata kuliah pilihan ({len(mks)} kelas):**")
                    st.markdown(badge_mk(mks))
                    c1, c2, _ = st.columns([1, 1, 3])
                    if status != "Disetujui" and c1.button("Setujui", key=f"ok{r['id']}", type="primary",
                                                           icon=":material/check:"):
                        db_exec("UPDATE users SET status='Disetujui' WHERE id=?", (r["id"],))
                        st.rerun()
                    if status != "Ditolak" and c2.button("Tolak", key=f"no{r['id']}",
                                                         icon=":material/close:"):
                        db_exec("UPDATE users SET status='Ditolak' WHERE id=?", (r["id"],))
                        st.rerun()


def page_input_nilai():
    page_header("edit_note", "Input Nilai Makul",
                "Kelola dan input nilai mata kuliah. "
                "Bobot: Presensi 10% · Tugas 20% · UTS 30% · UAS 40%.")
    mks = db_all("SELECT * FROM mata_kuliah ORDER BY nama")
    if not mks:
        st.info("Belum ada mata kuliah.")
        return
    label = {m["id"]: f'{m["nama"]} ({m["kode"]})' for m in mks}
    with st.container(border=True):
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
        "Nilai Akhir (manual)", min_value=0, max_value=100, step=1,
        help="Kosong (None) = dihitung otomatis. Isi angka = pakai angka ini.",
    )
    cfg["Grade"] = st.column_config.SelectboxColumn(
        "Grade (manual)", options=[g for _, g in GRADE_TABLE],
        help="Kosong (None) = otomatis dari nilai akhir. Pilih = pakai grade ini.",
    )

    st.markdown(
        "**Isi presensi (centang = hadir) dan nilai (0-100).** "
        "Kolom **manual** dibiarkan *None* agar nilai dihitung otomatis; "
        "isi hanya jika ingin menimpa (hapus dengan Delete/Backspace untuk mengembalikan)."
    )
    edited = st.data_editor(
        df, key=f"editor_{mk_id}", column_config=cfg,
        disabled=["NIM", "Nama"], hide_index=True, width="stretch",
    )

    preview, hasil = [], []
    for _, r in edited.iterrows():
        hadir = sum(bool(r[f"P{i}"]) for i in range(1, TOTAL_PERTEMUAN + 1))
        nm = None if pd.isna(r["Nilai Akhir"]) else float(r["Nilai Akhir"])
        gm = None if (pd.isna(r["Grade"]) or r["Grade"] == "") else str(r["Grade"])
        res = resolve_nilai(hadir, float(r["Tugas"]), float(r["UTS"]), float(r["UAS"]), nm, gm)
        hasil.append((int(r["id"]), r, res))
        preview.append({"NIM": r["NIM"], "Nama": r["Nama"], "Presensi": res["presensi"],
                        "Nilai Akhir": res["nilai_akhir"], "Grade": res["grade"],
                        "Mode": res["mode"]})
    st.markdown("**Pratinjau hasil akhir**")
    tabel(pd.DataFrame(preview))

    st.write("")
    if st.button("Simpan Nilai", type="primary", icon=":material/save:"):
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
    page_header("groups", "Data Mahasiswa",
                "Daftar mahasiswa dan pengaturan mata kuliah yang diambil")
    show_flash()
    studs = db_all("SELECT * FROM users ORDER BY created_at DESC")
    if not studs:
        st.info("Belum ada mahasiswa.")
        return

    all_mk = db_all("SELECT * FROM mata_kuliah ORDER BY nama")
    mk_label = {m["id"]: f'{m["nama"]} ({m["kode"]}) · {m["sks"]} SKS' for m in all_mk}
    taken = {}
    for r in db_all("SELECT user_id, mk_id FROM pilihan"):
        taken.setdefault(r["user_id"], set()).add(r["mk_id"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Mahasiswa", len(studs))
    c2.metric("Disetujui", sum(s_["status"] == "Disetujui" for s_ in studs))
    c3.metric("Menunggu", sum(s_["status"] == "Menunggu" for s_ in studs))
    c4.metric("Mata Kuliah Tersedia", len(all_mk))

    q = st.text_input("Cari mahasiswa", placeholder="Ketik NIM atau nama…").strip().lower()
    shown = [s_ for s_ in studs if q in s_["nim"].lower() or q in s_["nama"].lower()] if q else studs
    if not shown:
        st.info("Tidak ada mahasiswa yang cocok dengan pencarian.")
        return

    for s_ in shown:
        uid = s_["id"]
        ids = taken.get(uid, set())
        mine = [m for m in all_mk if m["id"] in ids]
        avail = [m["id"] for m in all_mk if m["id"] not in ids]
        total_sks = sum(m["sks"] for m in mine)
        with st.expander(f'{s_["nim"]} · {s_["nama"]}  —  {len(mine)} MK · {total_sks} SKS'):
            st.markdown(f"`{s_['nim']}` **{aman(s_['nama'])}** {badge_status(s_['status'])}")
            st.caption(f":material/mail: {s_['email'] or '-'}  ·  Terdaftar: {s_['created_at']}")
            st.markdown(f"**Mata kuliah diambil ({len(mine)} kelas · {total_sks} SKS):**")
            st.markdown(badge_mk([f'{m["nama"]} · {m["sks"]} SKS' for m in mine]))

            c_add, c_del = st.columns(2)
            with c_add:
                if not all_mk:
                    st.info("Belum ada mata kuliah. Tambahkan dulu di menu Data Mata Kuliah.")
                elif not avail:
                    st.info("Semua mata kuliah sudah diambil.")
                else:
                    k_add = f"add_{uid}"
                    pilih = st.multiselect("Tambah mata kuliah", avail, format_func=mk_label.get,
                                           key=k_add, placeholder="Pilih mata kuliah…")
                    if st.button("Tambahkan", key=f"btn_add_{uid}", type="primary",
                                 icon=":material/add:", disabled=not pilih):
                        for mk in pilih:
                            db_exec("INSERT OR IGNORE INTO pilihan(user_id, mk_id) VALUES (?,?)",
                                    (uid, mk))
                        st.session_state.pop(k_add, None)
                        flash(f'{len(pilih)} mata kuliah ditambahkan ke {s_["nama"]}.')
                        st.rerun()
            with c_del:
                if mine:
                    k_del = f"del_{uid}"
                    hapus = st.multiselect("Hapus mata kuliah", [m["id"] for m in mine],
                                           format_func=mk_label.get, key=k_del,
                                           placeholder="Pilih jika ingin menghapus…")
                    if st.button("Hapus dari mahasiswa", key=f"btn_del_{uid}",
                                 icon=":material/remove:", disabled=not hapus):
                        for mk in hapus:
                            db_exec("DELETE FROM pilihan WHERE user_id=? AND mk_id=?", (uid, mk))
                        st.session_state.pop(k_del, None)
                        flash(f'{len(hapus)} mata kuliah dihapus dari {s_["nama"]}.')
                        st.rerun()

            st.divider()
            st.markdown("**:material/edit: Edit data diri**")
            with st.form(f"form_edit_{uid}"):
                e1, e2 = st.columns(2)
                nama_b = e1.text_input("Nama lengkap", value=s_["nama"], key=f"e_nama_{uid}")
                nim_b = e2.text_input("NIM", value=s_["nim"], key=f"e_nim_{uid}")
                e3, e4 = st.columns(2)
                email_b = e3.text_input("Email", value=s_["email"] or "", key=f"e_email_{uid}")
                pw_b = e4.text_input("Reset password (kosongkan jika tidak diubah)",
                                     type="password", key=f"e_pw_{uid}")
                simpan = st.form_submit_button("Simpan perubahan", type="primary",
                                               icon=":material/save:")
            if simpan:
                err = update_profil(uid, nim_b, nama_b, email_b)
                if not err and pw_b:
                    if len(pw_b) < 6:
                        err = "Password baru minimal 6 karakter."
                    else:
                        set_password(uid, pw_b)
                if err:
                    st.error(err)
                else:
                    flash(f"Data {nama_b.strip()} berhasil diperbarui.")
                    st.rerun()


def page_mata_kuliah():
    page_header("menu_book", "Data Mata Kuliah", "Kelola daftar mata kuliah yang tersedia")
    mks = db_all("SELECT kode, nama, sks FROM mata_kuliah ORDER BY nama")
    tabel(pd.DataFrame(mks).rename(columns={"kode": "Kode", "nama": "Nama", "sks": "SKS"})
          if mks else pd.DataFrame(columns=["Kode", "Nama", "SKS"]))
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


MENU = [
    ("Persetujuan Mahasiswa", ":material/how_to_reg:", page_persetujuan),
    ("Input Nilai", ":material/edit_note:", page_input_nilai),
    ("Data Mahasiswa", ":material/groups:", page_data_mahasiswa),
    ("Data Mata Kuliah", ":material/menu_book:", page_mata_kuliah),
]


def admin_app():
    names = [m[0] for m in MENU]
    cur = st.session_state.get("page")
    if cur not in names:
        cur = names[0]
    with st.sidebar:
        for name, ic, _fn in MENU:
            if st.button(name, icon=ic, key=f"nav_{name}", width="stretch",
                         type="primary" if name == cur else "secondary"):
                st.session_state["page"] = name
                st.rerun()
    for name, _ic, fn in MENU:
        if name == cur:
            fn()


# ----------------------------------------------------------- halaman: mahasiswa
@st.fragment(run_every=5)
def status_live(uid: int, status_awal: str):
    """Cek status tiap 5 detik. Jika berubah, muat ulang seluruh halaman."""
    u = db_one("SELECT status FROM users WHERE id=?", (uid,))
    if u and u["status"] != status_awal:
        st.rerun()
    st.caption(f":material/sync: Sinkronisasi status · terakhir diperiksa "
               f"{datetime.now(WIB).strftime('%H:%M:%S')} (otomatis tiap 5 detik)")


@st.fragment(run_every=5)
def nilai_live(uid: int):
    """Ringkasan + tabel nilai yang menyegarkan diri tiap 5 detik (realtime)."""
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
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mata Kuliah", len(rows))
    c2.metric("Total SKS", sum(r["sks"] for r in rows))
    c3.metric("Sudah Dinilai", f"{len(dinilai)}/{len(rows)}")
    c4.metric("Rata-rata Nilai", rata)

    data = []
    for r in rows:
        if r["nilai_akhir"] is None:
            data.append({"Mata Kuliah": r["nama"], "SKS": r["sks"], "Presensi": "-", "Tugas": "-",
                         "UTS": "-", "UAS": "-", "Nilai Akhir": "-", "Grade": "-"})
        else:
            hadir = sum(sessions_to_list(r["sessions"]))
            data.append({"Mata Kuliah": r["nama"], "SKS": r["sks"],
                         "Presensi": hitung_nilai(hadir, 0, 0, 0)["presensi"],
                         "Tugas": r["tugas"], "UTS": r["uts"], "UAS": r["uas"],
                         "Nilai Akhir": r["nilai_akhir"], "Grade": r["grade"]})
    if data:
        st.dataframe(pd.DataFrame(data).astype(str), hide_index=True, width="stretch")
    else:
        st.info("Belum ada mata kuliah.")
    st.caption(f":material/sync: Live · diperbarui {datetime.now(WIB).strftime('%H:%M:%S')}")


def student_beranda(u):
    uid = u["id"]
    if u["status"] == "Disetujui":
        page_header("school", f"Halo, {u['nama']}", f"NIM {u['nim']} · Semester {SEMESTER}")
        st.success("Akun disetujui")
        nilai_live(uid)
        return

    mks = db_all(
        "SELECT m.nama, m.sks FROM pilihan p JOIN mata_kuliah m ON m.id=p.mk_id WHERE p.user_id=? ORDER BY m.nama",
        (uid,),
    )
    menunggu = u["status"] == "Menunggu"
    _, mid, _ = st.columns([1, 2.4, 1])
    with mid:
        with st.container(border=True):
            if menunggu:
                st.markdown("## :material/schedule: Pendaftaran Sedang Ditinjau")
                st.info("Data Anda telah terkirim ke administrator akademik. "
                        "Halaman ini akan berubah otomatis setelah disetujui. "
                        "Jika ada data yang salah, perbaiki di tab **Profil Saya**.")
            else:
                st.markdown("## :material/error: Pendaftaran Ditolak")
                st.error("Hubungi admin akademik untuk informasi lebih lanjut.")
        status_live(uid, u["status"])
        with st.container(border=True):
            st.markdown(f"**Akun terdaftar:** {aman(u['email'] or '-')}")
            st.markdown(f"**Nama mahasiswa:** {aman(u['nama'])}")
            st.markdown(f"**NIM:** `{u['nim']}`")
            st.markdown(f"**Mata kuliah diajukan ({len(mks)} kelas · {sum(m['sks'] for m in mks)} SKS):**")
            st.markdown(badge_mk([m["nama"] for m in mks]))
            st.caption(f"Diajukan pada: {u['created_at']}")


def student_profil(u):
    uid = u["id"]
    st.subheader(":material/badge: Data Diri")
    st.caption("Perbaiki data jika ada yang salah (misalnya NIM). Perubahan tersimpan langsung.")
    with st.form("form_profil"):
        nama = st.text_input("Nama lengkap", value=u["nama"], key="p_nama")
        nim = st.text_input("NIM", value=u["nim"], key="p_nim")
        email = st.text_input("Email", value=u["email"] or "", key="p_email")
        ok = st.form_submit_button("Simpan Data Diri", type="primary", icon=":material/save:")
    if ok:
        err = update_profil(uid, nim, nama, email)
        if err:
            st.error(err)
        else:
            st.session_state["user"]["nama"] = nama.strip()
            flash("Data diri berhasil diperbarui.")
            st.rerun()

    st.subheader(":material/lock: Ganti Password")
    with st.form("form_pw", clear_on_submit=True):
        lama = st.text_input("Password lama", type="password")
        baru = st.text_input("Password baru (minimal 6 karakter)", type="password")
        ulang = st.text_input("Ulangi password baru", type="password")
        ok_pw = st.form_submit_button("Ganti Password", icon=":material/key:")
    if ok_pw:
        err = ganti_password(uid, lama, baru, ulang)
        if err:
            st.error(err)
        else:
            flash("Password berhasil diganti.")
            st.rerun()


def student_app(uid: int):
    u = db_one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        st.session_state.pop("user", None)
        st.rerun()
        return
    show_flash()
    tab_beranda, tab_profil = st.tabs([":material/home: Beranda", ":material/person: Profil Saya"])
    with tab_beranda:
        student_beranda(u)
    with tab_profil:
        student_profil(u)


# ----------------------------------------------------------------------- main
def main():
    st.set_page_config(page_title="Input Nilai - Akademik System", page_icon=":material/school:",
                       layout="wide")
    init_db()
    user = st.session_state.get("user")
    if not user:
        page_auth()
        return
    with st.sidebar:
        st.markdown("### :material/school: Input Nilai")
        st.caption("Akademik System")
        with st.container(border=True):
            st.markdown(f"**{aman(user['nama'])}**")
            st.caption(user["role"].capitalize())
    if user["role"] == "admin":
        admin_app()
    else:
        student_app(user["id"])
    st.sidebar.write("")
    if st.sidebar.button("Keluar", width="stretch", icon=":material/logout:"):
        st.session_state.pop("user", None)
        st.session_state.pop("page", None)
        st.rerun()


if __name__ == "__main__":
    main()
