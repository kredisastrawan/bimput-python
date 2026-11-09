# BIMPUT - Input Nilai

Satu file (`app.py`).

## Fitur
- Mahasiswa: daftar (NIM + password), pilih mata kuliah, lihat status, lihat nilai.
- Admin: setujui/tolak pendaftaran, input presensi + nilai, kelola mata kuliah.
- Nilai akhir dan grade dihitung Python (bobot 10/20/30/40).
- Realtime: halaman mahasiswa menyegarkan diri tiap 5 detik.

## Login admin
Bawaan: username `admin`, password `admin123`.

## Jalankan di komputer
    pip install -r requirements.txt
    streamlit run app.py

## Online gratis (Streamlit Community Cloud)
1. Upload `app.py`, `requirements.txt`, `.gitignore`, `README.md` ke repo GitHub baru.
2. share.streamlit.io > login GitHub > Create app > pilih repo, branch `main`, file `app.py`.
3. Advanced settings > Secrets, isi:
       ADMIN_USER = "admin"
       ADMIN_PASSWORD = "admin123"

## Catatan data
Data disimpan di SQLite (`bimput.db`). Di Streamlit Cloud file ini bisa hilang saat
app restart/redeploy. Cukup untuk tugas/demo. Untuk data permanen, ganti ke
Google Sheets atau Supabase.
