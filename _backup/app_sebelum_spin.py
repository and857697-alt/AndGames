import sqlite3, os, uuid, json, random
from functools import wraps
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, g, send_from_directory, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "ganti-dengan-kunci-rahasia-kamu"
DB = "database.db"

# tambahkan int() biar bisa dipakai di template
app.jinja_env.globals.update(int=int, float=float, str=str, len=len)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
ALLOWED_EXT = {"png", "jpg", "jpeg"}
MAX_UPLOAD_MB = 5
QR_EXPIRE_MINUTES = 3
MIN_DEPOSIT = 10000
MIN_WITHDRAW = 50000

os.makedirs(UPLOAD_DIR, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

PROVIDERS = {
    "ewallet": ["DANA", "GoPay", "OVO", "ShopeePay", "LinkAja"],
    "bank": ["BCA", "BRI", "BNI", "Mandiri", "BSI", "CIMB Niaga", "Permata"],
}

BOMB_OPTIONS = [3, 5, 10, 15, 20, 24]
GRID_SIZE = 25

BANNERS = [
    {"title": "Selamat Datang di AndGames!", "sub": "Top up saldo & mainkan game seru", "color": "#2563eb"},
    {"title": "Deposit Mudah", "sub": "Scan QRIS, min Rp 10.000", "color": "#7c3aed"},
    {"title": "Withdraw Cepat", "sub": "Minimal Rp 50.000, diproses admin", "color": "#059669"},
    {"title": "Game Mines", "sub": "Buka kotak, hindari bom, menangkan hadiah!", "color": "#dc2626"},
]

# daftar game (tinggal tambah nanti)
GAME_LIST = [
    {
        "slug": "mines",
        "name": "Mines",
        "desc": "Buka kotak, hindari bom, menang besar!",
        "emoji": "💎",
        "color": "#8b5cf6",
        "grad": "linear-gradient(135deg,#8b5cf6,#3b82f6)",
        "route": "user_game_mines",
        "tag": "POPULER",
    },
    {
        "slug": "spin",
        "name": "Spin Wheel",
        "desc": "Putar roda, dapatkan hadiah!",
        "emoji": "🎡",
        "color": "#f59e0b",
        "grad": "linear-gradient(135deg,#f59e0b,#ef4444)",
        "route": None,
        "tag": "SOON",
    },
    {
        "slug": "scratch",
        "name": "Scratch Card",
        "desc": "Gosok kartu, temukan hadiah tersembunyi!",
        "emoji": "🎫",
        "color": "#10b981",
        "grad": "linear-gradient(135deg,#10b981,#3b82f6)",
        "route": None,
        "tag": "SOON",
    },
    {
        "slug": "dice",
        "name": "Dadu Keberuntungan",
        "desc": "Lempar dadu, menangkan jackpot!",
        "emoji": "🎲",
        "color": "#ec4899",
        "grad": "linear-gradient(135deg,#ec4899,#8b5cf6)",
        "route": None,
        "tag": "SOON",
    },
]


# ---------- HELPER GAME ----------
def _calc_multiplier(bombs, opened, margin):
    total = GRID_SIZE
    safe_total = total - bombs
    if opened <= 0:
        return 1.0
    if opened > safe_total:
        return 0.0
    p = 1.0
    for i in range(opened):
        p *= (safe_total - i) / (total - i)
    if p <= 0:
        return 0.0
    m = (1.0 / p) * (1.0 - margin / 100.0)
    return round(m, 4)


@app.template_global()
def _mult_demo(bombs, opened, margin):
    return _calc_multiplier(bombs, opened, margin)


# ---------- DB ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        no_hp TEXT NOT NULL,
        metode TEXT CHECK(metode IN ('ewallet','bank')),
        norek TEXT,
        role TEXT NOT NULL DEFAULT 'user')""")

    cols = [r[1] for r in db.execute("PRAGMA table_info(users)")]
    if "provider" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN provider TEXT")
    if "saldo" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN saldo REAL NOT NULL DEFAULT 0")

    db.execute("""CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tipe TEXT NOT NULL CHECK(tipe IN ('deposit','withdraw')),
        jumlah REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','approved','rejected','expired')),
        provider TEXT,
        norek TEXT,
        admin_note TEXT,
        bukti TEXT,
        expired_at TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")

    cols = [r[1] for r in db.execute("PRAGMA table_info(transactions)")]
    if "bukti" not in cols:
        db.execute("ALTER TABLE transactions ADD COLUMN bukti TEXT")
    if "expired_at" not in cols:
        db.execute("ALTER TABLE transactions ADD COLUMN expired_at TEXT")

    # GAME TABLES
    db.execute("""CREATE TABLE IF NOT EXISTS game_config(
        id INTEGER PRIMARY KEY CHECK(id=1),
        enabled INTEGER NOT NULL DEFAULT 1,
        profit_margin REAL NOT NULL DEFAULT 5.0,
        min_bet REAL NOT NULL DEFAULT 200,
        max_bet REAL NOT NULL DEFAULT 100000,
        max_win_per_day REAL NOT NULL DEFAULT 100000,
        admin_saldo REAL NOT NULL DEFAULT 0
    )""")
    if not db.execute("SELECT 1 FROM game_config WHERE id=1").fetchone():
        db.execute("INSERT INTO game_config(id) VALUES(1)")

    db.execute("""CREATE TABLE IF NOT EXISTS game_user_override(
        user_id INTEGER PRIMARY KEY,
        win_rate REAL DEFAULT NULL,
        force_next TEXT DEFAULT NULL,
        blocked INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS game_rounds(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        bet REAL NOT NULL,
        bombs INTEGER NOT NULL,
        clicked INTEGER NOT NULL DEFAULT 0,
        cashed_out INTEGER NOT NULL DEFAULT 0,
        multiplier REAL NOT NULL DEFAULT 0,
        payout REAL NOT NULL DEFAULT 0,
        profit REAL NOT NULL DEFAULT 0,
        forced TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        ended_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )""")

    db.execute("""CREATE TABLE IF NOT EXISTS game_clicks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id INTEGER NOT NULL,
        pos INTEGER NOT NULL,
        is_bomb INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY(round_id) REFERENCES game_rounds(id)
    )""")

    if not db.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        db.execute(
            "INSERT INTO users(username,password,email,no_hp,role) VALUES(?,?,?,?,?)",
            ("admin", generate_password_hash("admin123"), "admin@local", "-", "admin"),
        )
    db.commit()
    db.close()


def login_required(role):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if session.get("role") != role:
                flash("Silakan login dulu.")
                return redirect(url_for("login"))
            return f(*a, **kw)
        return wrapper
    return deco


def _allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXT


# ---------- ROOT ----------
@app.route("/")
def index():
    if session.get("role") == "admin":
        return redirect(url_for("admin"))
    if session.get("role") == "user":
        return redirect(url_for("user_home"))
    return redirect(url_for("login"))


# ---------- AUTH ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        data = [f.get(k, "").strip() for k in
                ("username", "password", "email", "no_hp", "metode", "norek", "provider")]
        if not all(data) or data[4] not in PROVIDERS or data[6] not in PROVIDERS[data[4]]:
            flash("Semua field wajib diisi.")
            return render_template("register.html", providers=PROVIDERS)
        try:
            db = get_db()
            db.execute(
                "INSERT INTO users(username,password,email,no_hp,metode,norek,provider) "
                "VALUES(?,?,?,?,?,?,?)",
                (data[0], generate_password_hash(data[1]), data[2], data[3],
                 data[4], data[5], data[6]),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("Username atau email sudah terdaftar.")
            return render_template("register.html", providers=PROVIDERS)
        flash("Registrasi berhasil, silakan login.")
        return redirect(url_for("login"))
    return render_template("register.html", providers=PROVIDERS)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = get_db().execute(
            "SELECT * FROM users WHERE username=?",
            (request.form.get("username", "").strip(),),
        ).fetchone()
        if u and check_password_hash(u["password"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = u["id"]
            session["username"] = u["username"]
            session["role"] = u["role"]
            return redirect(url_for("admin" if u["role"] == "admin" else "user_home"))
        flash("Username atau password salah.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- USER ----------
def _get_me():
    return get_db().execute(
        "SELECT * FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()


def _auto_expire(db):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE transactions SET status='expired', updated_at=? "
        "WHERE status='pending' AND tipe='deposit' AND expired_at IS NOT NULL AND expired_at < ?",
        (now, now),
    )
    db.commit()


@app.route("/user")
@login_required("user")
def user_home():
    u = _get_me()
    return render_template("user/home.html", u=u, banners=BANNERS, active="home")


@app.route("/user/profil")
@login_required("user")
def user_profil():
    u = _get_me()
    db = get_db()
    stats = {
        "total_tx": db.execute(
            "SELECT COUNT(*) c FROM transactions WHERE user_id=? AND status='approved'",
            (u["id"],)
        ).fetchone()["c"],
        "total_depo": db.execute(
            "SELECT COALESCE(SUM(jumlah),0) s FROM transactions "
            "WHERE user_id=? AND tipe='deposit' AND status='approved'",
            (u["id"],)
        ).fetchone()["s"],
        "total_wd": db.execute(
            "SELECT COALESCE(SUM(jumlah),0) s FROM transactions "
            "WHERE user_id=? AND tipe='withdraw' AND status='approved'",
            (u["id"],)
        ).fetchone()["s"],
    }
    return render_template("user/profil.html", u=u, stats=stats, active="profil")


@app.route("/user/deposit", methods=["GET", "POST"])
@login_required("user")
def user_deposit():
    u = _get_me()
    db = get_db()
    _auto_expire(db)

    if request.method == "POST":
        try:
            jumlah = float(request.form.get("jumlah", "0").replace(".", "").replace(",", ""))
        except ValueError:
            jumlah = 0
        if jumlah < MIN_DEPOSIT:
            flash("Minimal deposit Rp 10.000")
            return redirect(url_for("user_deposit"))

        expired_at = (datetime.now() + timedelta(minutes=QR_EXPIRE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        cur = db.execute(
            "INSERT INTO transactions(user_id,tipe,jumlah,status,provider,norek,expired_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (u["id"], "deposit", jumlah, "pending", u["provider"], u["norek"], expired_at),
        )
        db.commit()
        return redirect(url_for("user_deposit_qr", tid=cur.lastrowid))

    tx = db.execute(
        "SELECT * FROM transactions WHERE user_id=? AND tipe='deposit' "
        "AND status != 'expired' ORDER BY id DESC LIMIT 30",
        (u["id"],),
    ).fetchall()
    return render_template("user/deposit.html", u=u, tx=tx,
                           min_deposit=MIN_DEPOSIT, active="deposit")


@app.route("/user/deposit/qr/<int:tid>")
@login_required("user")
def user_deposit_qr(tid):
    db = get_db()
    _auto_expire(db)
    t = db.execute(
        "SELECT * FROM transactions WHERE id=? AND user_id=? AND tipe='deposit'",
        (tid, session["user_id"]),
    ).fetchone()
    if not t:
        flash("Transaksi tidak ditemukan.")
        return redirect(url_for("user_deposit"))
    if t["status"] != "pending":
        flash("Transaksi sudah tidak aktif.")
        return redirect(url_for("user_deposit"))
    return render_template("user/deposit_qr.html", t=t, u=_get_me(),
                           active="deposit", expire_minutes=QR_EXPIRE_MINUTES)


@app.route("/user/deposit/upload/<int:tid>", methods=["GET", "POST"])
@login_required("user")
def user_deposit_upload(tid):
    db = get_db()
    _auto_expire(db)
    t = db.execute(
        "SELECT * FROM transactions WHERE id=? AND user_id=? AND tipe='deposit'",
        (tid, session["user_id"]),
    ).fetchone()
    if not t:
        flash("Transaksi tidak ditemukan.")
        return redirect(url_for("user_deposit"))
    if t["status"] != "pending":
        flash("Transaksi sudah tidak aktif.")
        return redirect(url_for("user_deposit"))

    if request.method == "POST":
        file = request.files.get("bukti")
        if not file or not file.filename or not _allowed_file(file.filename):
            flash("File harus JPG/PNG.")
            return redirect(url_for("user_deposit_upload", tid=tid))

        ext = file.filename.rsplit(".", 1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join(UPLOAD_DIR, secure_filename(fname))
        file.save(path)

        db.execute("UPDATE transactions SET bukti=? WHERE id=?", (fname, tid))
        db.commit()
        flash("Bukti berhasil dikirim. Menunggu approve admin.")
        return redirect(url_for("user_deposit"))

    return render_template("user/deposit_upload.html", t=t, u=_get_me(), active="deposit")


@app.route("/user/deposit/batal/<int:tid>", methods=["POST"])
@login_required("user")
def user_deposit_batal(tid):
    db = get_db()
    db.execute(
        "UPDATE transactions SET status='expired', updated_at=datetime('now','localtime') "
        "WHERE id=? AND user_id=? AND status='pending' AND tipe='deposit'",
        (tid, session["user_id"]),
    )
    db.commit()
    flash("Deposit dibatalkan.")
    return redirect(url_for("user_deposit"))


@app.route("/user/withdraw", methods=["GET", "POST"])
@login_required("user")
def user_withdraw():
    u = _get_me()
    db = get_db()

    if request.method == "POST":
        try:
            jumlah = float(request.form.get("jumlah", "0").replace(".", "").replace(",", ""))
        except ValueError:
            jumlah = 0
        if jumlah < MIN_WITHDRAW:
            flash("Minimal withdraw Rp 50.000")
            return redirect(url_for("user_withdraw"))
        if u["saldo"] < jumlah:
            flash("Saldo tidak cukup.")
            return redirect(url_for("user_withdraw"))

        db.execute("UPDATE users SET saldo = saldo - ? WHERE id=?",
                   (jumlah, u["id"]))
        db.execute(
            "INSERT INTO transactions(user_id,tipe,jumlah,status,provider,norek) "
            "VALUES(?,?,?,?,?,?)",
            (u["id"], "withdraw", jumlah, "pending", u["provider"], u["norek"]),
        )
        db.commit()
        flash("Permintaan withdraw dikirim. Saldo dipotong sementara.")
        return redirect(url_for("user_withdraw"))

    tx = db.execute(
        "SELECT * FROM transactions WHERE user_id=? AND tipe='withdraw' "
        "ORDER BY id DESC LIMIT 30",
        (u["id"],),
    ).fetchall()
    return render_template("user/withdraw.html", u=u, tx=tx,
                           min_withdraw=MIN_WITHDRAW, active="withdraw")


@app.route("/user/riwayat")
@login_required("user")
def user_riwayat():
    u = _get_me()
    db = get_db()
    _auto_expire(db)

    tab = request.args.get("tab", "all")
    dari = request.args.get("dari", "").strip()
    sampai = request.args.get("sampai", "").strip()
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    if page < 1:
        page = 1
    PER_PAGE = 10

    where = ["user_id=?"]
    args = [u["id"]]
    if tab in ("deposit", "withdraw"):
        where.append("tipe=?")
        args.append(tab)
    if dari:
        where.append("date(created_at) >= ?")
        args.append(dari)
    if sampai:
        where.append("date(created_at) <= ?")
        args.append(sampai)
    wsql = " AND ".join(where)

    total = db.execute(
        f"SELECT COUNT(*) c FROM transactions WHERE {wsql}", args
    ).fetchone()["c"]
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * PER_PAGE

    tx = db.execute(
        f"SELECT * FROM transactions WHERE {wsql} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        (*args, PER_PAGE, offset),
    ).fetchall()

    summary = {
        "total_in": db.execute(
            f"SELECT COALESCE(SUM(jumlah),0) s FROM transactions "
            f"WHERE {wsql} AND tipe='deposit' AND status='approved'",
            args,
        ).fetchone()["s"],
        "total_out": db.execute(
            f"SELECT COALESCE(SUM(jumlah),0) s FROM transactions "
            f"WHERE {wsql} AND tipe='withdraw' AND status='approved'",
            args,
        ).fetchone()["s"],
    }

    return render_template(
        "user/riwayat.html",
        u=u, tx=tx, tab=tab, dari=dari, sampai=sampai,
        page=page, total_pages=total_pages, summary=summary,
        active="riwayat",
    )


# ---------- ADMIN ----------
@app.route("/admin")
@login_required("admin")
def admin():
    db = get_db()
    _auto_expire(db)
    users = db.execute(
        "SELECT * FROM users WHERE role='user' ORDER BY id DESC"
    ).fetchall()
    stats = {
        "total_user": len(users),
        "pending_depo": db.execute(
            "SELECT COUNT(*) c FROM transactions WHERE tipe='deposit' AND status='pending'"
        ).fetchone()["c"],
        "pending_wd": db.execute(
            "SELECT COUNT(*) c FROM transactions WHERE tipe='withdraw' AND status='pending'"
        ).fetchone()["c"],
        "total_saldo": db.execute(
            "SELECT COALESCE(SUM(saldo),0) s FROM users WHERE role='user'"
        ).fetchone()["s"],
    }
    return render_template("admin/admin.html", users=users, stats=stats)


@app.route("/admin/edit/<int:uid>", methods=["GET", "POST"])
@login_required("admin")
def edit_user(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=? AND role='user'", (uid,)).fetchone()
    if not u:
        flash("User tidak ditemukan.")
        return redirect(url_for("admin"))
    if request.method == "POST":
        f = request.form
        username, email, no_hp, metode, provider, norek = [
            f.get(k, "").strip() for k in
            ("username", "email", "no_hp", "metode", "provider", "norek")]
        password = f.get("password", "").strip()
        if not all([username, email, no_hp, norek]) or metode not in PROVIDERS or provider not in PROVIDERS[metode]:
            flash("Data tidak valid.")
            return render_template("admin/edit_user.html", u=u, providers=PROVIDERS)
        try:
            db.execute(
                "UPDATE users SET username=?, email=?, no_hp=?, metode=?, provider=?, norek=? WHERE id=?",
                (username, email, no_hp, metode, provider, norek, uid),
            )
            if password:
                db.execute("UPDATE users SET password=? WHERE id=?",
                           (generate_password_hash(password), uid))
            db.commit()
        except sqlite3.IntegrityError:
            flash("Username atau email sudah dipakai user lain.")
            return render_template("admin/edit_user.html", u=u, providers=PROVIDERS)
        flash("User berhasil diperbarui.")
        return redirect(url_for("admin"))
    return render_template("admin/edit_user.html", u=u, providers=PROVIDERS)


@app.route("/admin/delete/<int:uid>", methods=["POST"])
@login_required("admin")
def delete_user(uid):
    db = get_db()
    db.execute("DELETE FROM users WHERE id=? AND role='user'", (uid,))
    db.commit()
    flash("User dihapus.")
    return redirect(url_for("admin"))


@app.route("/admin/deposit")
@login_required("admin")
def admin_deposit():
    db = get_db()
    _auto_expire(db)
    rows = db.execute("""
        SELECT t.*, u.username FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.tipe='deposit' AND t.status != 'expired'
        ORDER BY CASE t.status WHEN 'pending' THEN 0 ELSE 1 END, t.id DESC
        LIMIT 200
    """).fetchall()
    return render_template("admin/deposit_list.html", rows=rows)


@app.route("/admin/deposit/<int:tid>/<aksi>", methods=["POST"])
@login_required("admin")
def admin_deposit_action(tid, aksi):
    if aksi not in ("approve", "reject"):
        return redirect(url_for("admin_deposit"))
    db = get_db()
    t = db.execute("SELECT * FROM transactions WHERE id=? AND tipe='deposit'",
                   (tid,)).fetchone()
    if not t or t["status"] != "pending":
        flash("Transaksi tidak valid / sudah diproses.")
        return redirect(url_for("admin_deposit"))

    if aksi == "approve" and not t["bukti"]:
        flash("Belum ada bukti transfer. Tidak bisa approve.")
        return redirect(url_for("admin_deposit"))

    note = request.form.get("note", "").strip() or None
    if aksi == "approve":
        db.execute("UPDATE users SET saldo = saldo + ? WHERE id=?",
                   (t["jumlah"], t["user_id"]))
        db.execute(
            "UPDATE transactions SET status='approved', admin_note=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (note, tid),
        )
        flash("Deposit disetujui.")
    else:
        db.execute(
            "UPDATE transactions SET status='rejected', admin_note=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (note, tid),
        )
        flash("Deposit ditolak.")
    db.commit()
    return redirect(url_for("admin_deposit"))


@app.route("/admin/bukti/<filename>")
@login_required("admin")
def admin_bukti(filename):
    safe = secure_filename(filename)
    if safe != filename:
        abort(400)
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/admin/withdraw")
@login_required("admin")
def admin_withdraw():
    db = get_db()
    rows = db.execute("""
        SELECT t.*, u.username FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.tipe='withdraw' ORDER BY
        CASE t.status WHEN 'pending' THEN 0 ELSE 1 END, t.id DESC
        LIMIT 200
    """).fetchall()
    return render_template("admin/withdraw_list.html", rows=rows)


@app.route("/admin/withdraw/<int:tid>/<aksi>", methods=["POST"])
@login_required("admin")
def admin_withdraw_action(tid, aksi):
    if aksi not in ("approve", "reject"):
        return redirect(url_for("admin_withdraw"))
    db = get_db()
    t = db.execute("SELECT * FROM transactions WHERE id=? AND tipe='withdraw'",
                   (tid,)).fetchone()
    if not t or t["status"] != "pending":
        flash("Transaksi tidak valid / sudah diproses.")
        return redirect(url_for("admin_withdraw"))

    note = request.form.get("note", "").strip() or None
    if aksi == "approve":
        db.execute(
            "UPDATE transactions SET status='approved', admin_note=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (note, tid),
        )
        flash("Withdraw disetujui.")
    else:
        db.execute("UPDATE users SET saldo = saldo + ? WHERE id=?",
                   (t["jumlah"], t["user_id"]))
        db.execute(
            "UPDATE transactions SET status='rejected', admin_note=?, "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (note, tid),
        )
        flash("Withdraw ditolak, saldo dikembalikan.")
    db.commit()
    return redirect(url_for("admin_withdraw"))


# ============ GAME — HELPER ============
def _get_game_config(db):
    return db.execute("SELECT * FROM game_config WHERE id=1").fetchone()


def _get_user_override(db, uid):
    return db.execute(
        "SELECT * FROM game_user_override WHERE user_id=?", (uid,)
    ).fetchone()


def _today_win(db, uid):
    return db.execute(
        "SELECT COALESCE(SUM(profit),0) s FROM game_rounds "
        "WHERE user_id=? AND profit > 0 AND date(created_at)=date('now','localtime')",
        (uid,),
    ).fetchone()["s"]


# ============ GAME — KATALOG ============
@app.route("/user/game")
@login_required("user")
def user_game():
    """Halaman katalog game."""
    u = _get_me()
    db = get_db()
    cfg = _get_game_config(db)
    ov = _get_user_override(db, u["id"])

    my_stats = {
        "total_main": db.execute(
            "SELECT COUNT(*) c FROM game_rounds WHERE user_id=? AND ended_at IS NOT NULL",
            (u["id"],)
        ).fetchone()["c"],
        "total_menang": db.execute(
            "SELECT COALESCE(SUM(profit),0) s FROM game_rounds "
            "WHERE user_id=? AND profit > 0",
            (u["id"],)
        ).fetchone()["s"],
    }

    games = GAME_LIST
    if not cfg["enabled"]:
        games = []

    return render_template(
        "user/game.html", u=u, games=games, cfg=cfg, ov=ov,
        my_stats=my_stats, active="game",
    )


# ============ GAME MINES ============
@app.route("/user/game/mines")
@login_required("user")
def user_game_mines():
    """Halaman permainan Mines."""
    u = _get_me()
    db = get_db()
    cfg = _get_game_config(db)
    ov = _get_user_override(db, u["id"])

    if not cfg["enabled"]:
        flash("Game sedang nonaktif.")
        return redirect(url_for("user_game"))
    if ov and ov["blocked"]:
        flash("Kamu tidak bisa bermain saat ini.")
        return redirect(url_for("user_game"))

    active = db.execute(
        "SELECT * FROM game_rounds WHERE user_id=? AND ended_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (u["id"],),
    ).fetchone()

    history = db.execute(
        "SELECT * FROM game_rounds WHERE user_id=? AND ended_at IS NOT NULL "
        "ORDER BY id DESC LIMIT 15",
        (u["id"],),
    ).fetchall()

    today_win = _today_win(db, u["id"])
    max_win = cfg["max_win_per_day"]

    return render_template(
        "user/game_mines.html", u=u, cfg=cfg, ov=ov, active=active,
        history=history, bombs=BOMB_OPTIONS,
        today_win=today_win, max_win=max_win,
    )


@app.route("/user/game/start", methods=["POST"])
@login_required("user")
def user_game_start():
    u = _get_me()
    db = get_db()
    cfg = _get_game_config(db)
    ov = _get_user_override(db, u["id"])

    if not cfg["enabled"]:
        flash("Game nonaktif.")
        return redirect(url_for("user_game_mines"))
    if ov and ov["blocked"]:
        flash("Kamu tidak bisa bermain.")
        return redirect(url_for("user_game_mines"))

    active = db.execute(
        "SELECT * FROM game_rounds WHERE user_id=? AND ended_at IS NULL",
        (u["id"],),
    ).fetchone()
    if active:
        return redirect(url_for("user_game_mines"))

    try:
        bet = float(request.form.get("bet", "0").replace(".", "").replace(",", ""))
        bombs = int(request.form.get("bombs", "0"))
    except (ValueError, TypeError):
        flash("Data tidak valid.")
        return redirect(url_for("user_game_mines"))

    if bombs not in BOMB_OPTIONS:
        flash("Jumlah bom tidak valid.")
        return redirect(url_for("user_game_mines"))
    if bet < cfg["min_bet"]:
        flash("Minimal bet Rp " + f"{int(cfg['min_bet']):,}".replace(",", "."))
        return redirect(url_for("user_game_mines"))
    if bet > cfg["max_bet"]:
        flash("Maksimal bet Rp " + f"{int(cfg['max_bet']):,}".replace(",", "."))
        return redirect(url_for("user_game_mines"))
    if u["saldo"] < bet:
        flash("Saldo tidak cukup.")
        return redirect(url_for("user_game_mines"))

    today_win = _today_win(db, u["id"])
    if today_win >= cfg["max_win_per_day"]:
        flash("Batas menang harian tercapai. Coba lagi besok.")
        return redirect(url_for("user_game_mines"))

    db.execute("UPDATE users SET saldo = saldo - ? WHERE id=?", (bet, u["id"]))
    db.commit()

    db.execute(
        "INSERT INTO game_rounds(user_id, bet, bombs) VALUES(?,?,?)",
        (u["id"], bet, bombs),
    )
    db.commit()
    return redirect(url_for("user_game_mines"))


@app.route("/user/game/click", methods=["POST"])
@login_required("user")
def user_game_click():
    u = _get_me()
    db = get_db()
    cfg = _get_game_config(db)
    ov = _get_user_override(db, u["id"])

    rnd = db.execute(
        "SELECT * FROM game_rounds WHERE user_id=? AND ended_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (u["id"],),
    ).fetchone()
    if not rnd:
        return {"ok": False, "err": "Tidak ada ronde aktif"}

    try:
        pos = int(request.json.get("pos", -1))
    except (ValueError, TypeError):
        return {"ok": False, "err": "Posisi invalid"}

    if pos < 0 or pos >= GRID_SIZE:
        return {"ok": False, "err": "Posisi di luar grid"}

    already = db.execute(
        "SELECT 1 FROM game_clicks WHERE round_id=? AND pos=?",
        (rnd["id"], pos),
    ).fetchone()
    if already:
        return {"ok": False, "err": "Sudah diklik"}

    rng = random.Random(rnd["id"] * 1000 + rnd["user_id"])
    positions = list(range(GRID_SIZE))
    rng.shuffle(positions)
    bomb_set = set(positions[:rnd["bombs"]])

    forced = None
    if ov and ov["force_next"]:
        forced = ov["force_next"]
        db.execute("UPDATE game_user_override SET force_next=NULL WHERE user_id=?",
                   (u["id"],))
        db.commit()

    is_bomb = pos in bomb_set

    if forced == "lose":
        is_bomb = True
    elif forced == "win":
        is_bomb = False

    today_win = _today_win(db, u["id"])
    if not is_bomb and today_win >= cfg["max_win_per_day"]:
        is_bomb = True

    db.execute(
        "INSERT INTO game_clicks(round_id, pos, is_bomb) VALUES(?,?,?)",
        (rnd["id"], pos, 1 if is_bomb else 0),
    )
    db.commit()

    if is_bomb:
        db.execute(
            "UPDATE game_rounds SET clicked=?, multiplier=0, payout=0, "
            "profit=-?, ended_at=datetime('now','localtime'), forced=? WHERE id=?",
            (rnd["clicked"] + 1, rnd["bet"], forced, rnd["id"]),
        )
        db.execute("UPDATE game_config SET admin_saldo = admin_saldo + ? WHERE id=1",
                   (rnd["bet"],))
        db.commit()
        return {"ok": True, "bomb": True, "pos": pos, "bet": rnd["bet"], "payout": 0}
    else:
        new_clicked = rnd["clicked"] + 1
        new_mult = _calc_multiplier(rnd["bombs"], new_clicked, cfg["profit_margin"])
        new_payout = round(rnd["bet"] * new_mult, 2)
        db.execute(
            "UPDATE game_rounds SET clicked=?, multiplier=?, payout=? WHERE id=?",
            (new_clicked, new_mult, new_payout, rnd["id"]),
        )
        db.commit()
        return {
            "ok": True, "bomb": False, "pos": pos,
            "clicked": new_clicked,
            "multiplier": new_mult,
            "payout": new_payout,
        }


@app.route("/user/game/cashout", methods=["POST"])
@login_required("user")
def user_game_cashout():
    u = _get_me()
    db = get_db()

    rnd = db.execute(
        "SELECT * FROM game_rounds WHERE user_id=? AND ended_at IS NULL "
        "ORDER BY id DESC LIMIT 1",
        (u["id"],),
    ).fetchone()
    if not rnd:
        return {"ok": False, "err": "Tidak ada ronde aktif"}
    if rnd["clicked"] < 1:
        return {"ok": False, "err": "Buka minimal 1 kotak dulu"}

    payout = rnd["payout"]
    profit = payout - rnd["bet"]

    db.execute("UPDATE users SET saldo = saldo + ? WHERE id=?", (payout, u["id"]))
    db.execute(
        "UPDATE game_rounds SET cashed_out=1, profit=?, "
        "ended_at=datetime('now','localtime') WHERE id=?",
        (profit, rnd["id"]),
    )

    if profit < 0:
        db.execute("UPDATE game_config SET admin_saldo = admin_saldo + ? WHERE id=1",
                   (abs(profit),))
    elif profit > 0:
        db.execute("UPDATE game_config SET admin_saldo = admin_saldo - ? WHERE id=1",
                   (profit,))

    db.commit()
    return {"ok": True, "payout": payout, "profit": profit}


# ============ ADMIN GAME ============
@app.route("/admin/game")
@login_required("admin")
def admin_game():
    db = get_db()
    cfg = _get_game_config(db)
    stats = {
        "total_rounds": db.execute(
            "SELECT COUNT(*) c FROM game_rounds WHERE ended_at IS NOT NULL"
        ).fetchone()["c"],
        "total_bet": db.execute(
            "SELECT COALESCE(SUM(bet),0) s FROM game_rounds WHERE ended_at IS NOT NULL"
        ).fetchone()["s"],
        "total_payout": db.execute(
            "SELECT COALESCE(SUM(payout),0) s FROM game_rounds WHERE ended_at IS NOT NULL"
        ).fetchone()["s"],
    }
    stats["profit"] = stats["total_bet"] - stats["total_payout"]
    return render_template("admin/game_config.html", cfg=cfg, stats=stats)


@app.route("/admin/game/config", methods=["POST"])
@login_required("admin")
def admin_game_config_save():
    db = get_db()
    try:
        enabled = 1 if request.form.get("enabled") == "on" else 0
        margin = float(request.form.get("profit_margin", "5"))
        min_bet = float(request.form.get("min_bet", "200"))
        max_bet = float(request.form.get("max_bet", "100000"))
        max_win = float(request.form.get("max_win_per_day", "100000"))
    except ValueError:
        flash("Angka tidak valid.")
        return redirect(url_for("admin_game"))

    db.execute("""UPDATE game_config SET
        enabled=?, profit_margin=?, min_bet=?, max_bet=?, max_win_per_day=?
        WHERE id=1""", (enabled, margin, min_bet, max_bet, max_win))
    db.commit()
    flash("Setelan game disimpan.")
    return redirect(url_for("admin_game"))


@app.route("/admin/game/users")
@login_required("admin")
def admin_game_users():
    db = get_db()
    users = db.execute("""
        SELECT u.*, o.win_rate, o.force_next, o.blocked, o.note
        FROM users u
        LEFT JOIN game_user_override o ON o.user_id = u.id
        WHERE u.role='user'
        ORDER BY u.id DESC
    """).fetchall()
    return render_template("admin/game_user.html", users=users)


@app.route("/admin/game/users/<int:uid>", methods=["POST"])
@login_required("admin")
def admin_game_user_save(uid):
    db = get_db()
    blocked = 1 if request.form.get("blocked") == "on" else 0
    force_next = request.form.get("force_next", "").strip() or None
    if force_next not in (None, "win", "lose"):
        force_next = None
    try:
        wr_raw = request.form.get("win_rate", "").strip()
        win_rate = float(wr_raw) if wr_raw else None
    except ValueError:
        win_rate = None
    note = request.form.get("note", "").strip() or None

    exists = db.execute(
        "SELECT 1 FROM game_user_override WHERE user_id=?", (uid,)
    ).fetchone()
    if exists:
        db.execute("""UPDATE game_user_override SET
            win_rate=?, force_next=?, blocked=?, note=? WHERE user_id=?""",
            (win_rate, force_next, blocked, note, uid))
    else:
        db.execute("""INSERT INTO game_user_override
            (user_id, win_rate, force_next, blocked, note)
            VALUES(?,?,?,?,?)""",
            (uid, win_rate, force_next, blocked, note))
    db.commit()
    flash("Override disimpan.")
    return redirect(url_for("admin_game_users"))


@app.route("/admin/game/users/<int:uid>/reset", methods=["POST"])
@login_required("admin")
def admin_game_user_reset(uid):
    db = get_db()
    db.execute("DELETE FROM game_user_override WHERE user_id=?", (uid,))
    db.commit()
    flash("Override dihapus.")
    return redirect(url_for("admin_game_users"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
