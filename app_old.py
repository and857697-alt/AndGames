import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "ganti-dengan-kunci-rahasia-kamu"
DB = "database.db"

PROVIDERS = {
    "ewallet": ["DANA", "GoPay", "OVO", "ShopeePay", "LinkAja"],
    "bank": ["BCA", "BRI", "BNI", "Mandiri", "BSI", "CIMB Niaga", "Permata"],
}

MIN_DEPOSIT = 10000
MIN_WITHDRAW = 50000

BANNERS = [
    {"title": "Selamat Datang di AndGames!", "sub": "Top up saldo & mainkan game seru", "color": "#2563eb"},
    {"title": "Deposit Mudah", "sub": "Minimal Rp 10.000 via e-wallet & bank", "color": "#7c3aed"},
    {"title": "Withdraw Cepat", "sub": "Minimal Rp 50.000, diproses admin", "color": "#059669"},
    {"title": "Segera Hadir", "sub": "Game seru akan segera rilis di sini", "color": "#dc2626"},
]


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
            CHECK(status IN ('pending','approved','rejected')),
        provider TEXT,
        norek TEXT,
        admin_note TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
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


@app.route("/user")
@login_required("user")
def user_home():
    u = _get_me()
    return render_template("user/home.html", u=u, banners=BANNERS, active="home")


@app.route("/user/profil")
@login_required("user")
def user_profil():
    u = _get_me()
    return render_template("user/profil.html", u=u, active="profil")


@app.route("/user/deposit", methods=["GET", "POST"])
@login_required("user")
def user_deposit():
    u = _get_me()
    db = get_db()

    if request.method == "POST":
        try:
            jumlah = float(request.form.get("jumlah", "0").replace(".", "").replace(",", ""))
        except ValueError:
            jumlah = 0
        if jumlah < MIN_DEPOSIT:
            flash("Minimal deposit Rp 10.000")
            return redirect(url_for("user_deposit"))
        db.execute(
            "INSERT INTO transactions(user_id,tipe,jumlah,status,provider,norek) "
            "VALUES(?,?,?,?,?,?)",
            (u["id"], "deposit", jumlah, "pending", u["provider"], u["norek"]),
        )
        db.commit()
        flash("Permintaan deposit dikirim. Tunggu approve admin.")
        return redirect(url_for("user_deposit"))

    tx = db.execute(
        "SELECT * FROM transactions WHERE user_id=? AND tipe='deposit' "
        "ORDER BY id DESC LIMIT 30",
        (u["id"],),
    ).fetchall()
    return render_template("user/deposit.html", u=u, tx=tx,
                           min_deposit=MIN_DEPOSIT, active="deposit")


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
    tab = request.args.get("tab", "all")
    q = "SELECT * FROM transactions WHERE user_id=?"
    args = [u["id"]]
    if tab in ("deposit", "withdraw"):
        q += " AND tipe=?"
        args.append(tab)
    q += " ORDER BY id DESC LIMIT 100"
    tx = get_db().execute(q, args).fetchall()
    return render_template("user/riwayat.html", u=u, tx=tx, tab=tab, active="riwayat")


# ---------- ADMIN ----------
@app.route("/admin")
@login_required("admin")
def admin():
    db = get_db()
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
    rows = db.execute("""
        SELECT t.*, u.username FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.tipe='deposit' ORDER BY
        CASE t.status WHEN 'pending' THEN 0 ELSE 1 END, t.id DESC
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


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
