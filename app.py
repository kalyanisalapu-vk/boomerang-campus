import os
import json
import sqlite3
import uuid
import secrets
from functools import wraps
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from ai_matcher import verify, reverse_auto_match

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "static", "uploads")
DB = os.path.join(BASE, "campus.db")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "boomerang-campus-secret-key-2026")
os.makedirs(UPLOADS, exist_ok=True)

CATEGORIES = {
    "ID Card": ["ID number (last 3 digits)", "Name/photo printed on it", "Lanyard / holder colour", "Any sticker or mark"],
    "Wallet": ["Colour", "Brand", "Material (leather/cloth)", "What is inside (cards/cash)", "Any special mark"],
    "Books": ["Title / subject", "Cover colour", "Name written inside?", "Bookmark / notes inside"],
    "Earphones": ["Brand", "Colour", "Type (wired/earbuds/neckband)", "Case colour / scratch"],
    "Phone": ["Brand & model", "Colour", "Cover/case description", "Wallpaper / lock screen", "Scratches/cracks"],
    "Bag": ["Brand", "Colour", "Size", "Items inside", "Special mark / keychain"],
    "Keys": ["Number of keys", "Keychain description", "Brand/tag"],
    "Other": ["Colour", "Shape", "Brand", "Any special mark"],
}

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    d = g.pop("db", None)
    if d:
        d.close()

def q(sql, args=(), one=False, commit=False):
    cur = db().execute(sql, args)
    if commit:
        db().commit()
        return cur.lastrowid
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows

def init_db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        phone TEXT,
        dept TEXT,
        pw TEXT,
        role TEXT DEFAULT 'user',
        karma INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER,
        name TEXT,
        category TEXT,
        date TEXT,
        place TEXT,
        description TEXT,
        photo TEXT,
        features TEXT,
        status TEXT DEFAULT 'lost',
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        finder_id INTEGER,
        photo TEXT,
        description TEXT,
        answers TEXT,
        result TEXT,
        status TEXT,
        handover_otp TEXT,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        msg TEXT,
        seen INTEGER DEFAULT 0,
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS found_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        finder_id INTEGER,
        name TEXT,
        category TEXT,
        place TEXT,
        description TEXT,
        photo TEXT,
        status TEXT DEFAULT 'available',
        matched_lost_id INTEGER DEFAULT NULL,
        match_score REAL DEFAULT 0,
        match_reason TEXT DEFAULT '',
        created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Columns check & migration
    cols_users = [r[1] for r in c.execute("PRAGMA table_info(users)")]
    if "karma" not in cols_users:
        c.execute("ALTER TABLE users ADD COLUMN karma INTEGER DEFAULT 0")

    cols_claims = [r[1] for r in c.execute("PRAGMA table_info(claims)")]
    if "handover_otp" not in cols_claims:
        c.execute("ALTER TABLE claims ADD COLUMN handover_otp TEXT")

    # Admin User
    if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        c.execute("INSERT INTO users(name, email, phone, dept, pw, role, karma) VALUES(?,?,?,?,?,?,?)",
                  ("Campus Security Admin", "admin@campus.com", "9988776655", "Administration", generate_password_hash("admin123"), "admin", 250))
    
    # Demo Users
    if not c.execute("SELECT 1 FROM users WHERE email='demo@campus.com'").fetchone():
        c.execute("INSERT INTO users(name, email, phone, dept, pw, role, karma) VALUES(?,?,?,?,?,?,?)",
                  ("Rahul Sharma", "demo@campus.com", "9876543210", "CSE", generate_password_hash("demo123"), "user", 100))
    
    if not c.execute("SELECT 1 FROM users WHERE email='sneha@campus.com'").fetchone():
        c.execute("INSERT INTO users(name, email, phone, dept, pw, role, karma) VALUES(?,?,?,?,?,?,?)",
                  ("Sneha Reddy", "sneha@campus.com", "9845012345", "ECE", generate_password_hash("sneha123"), "user", 200))

    c.commit()
    c.close()

    import seed
    seed.run(DB, UPLOADS)

def me():
    if "uid" in session:
        return q("SELECT * FROM users WHERE id=?", (session["uid"],), one=True)
    return None

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not me():
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = me()
        if not u or u["role"] != "admin":
            abort(403)
        return f(*a, **k)
    return w

def save_photo(f):
    if not f or not f.filename:
        return None
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(UPLOADS, name))
    return name

def notify(uid, msg):
    q("INSERT INTO notes(user_id, msg) VALUES(?,?)", (uid, msg), commit=True)

def mask(s):
    s = str(s or "").strip()
    if "@" in s:
        parts = s.split("@", 1)
        uname = parts[0]
        masked_uname = uname[:2] + "*****" if len(uname) > 2 else uname + "*****"
        return f"{masked_uname}@{parts[1]}"
    if len(s) >= 7:
        return s[:2] + "*****" + s[-2:]
    return "****"

def add_karma(uid, points=50):
    q("UPDATE users SET karma = karma + ? WHERE id = ?", (points, uid), commit=True)

@app.context_processor
def inject():
    u = me()
    unread_count = 0
    if u:
        row = q("SELECT COUNT(*) c FROM notes WHERE user_id=? AND seen=0", (u["id"],), one=True)
        unread_count = row["c"] if row else 0
    return dict(current_user=u, unread=unread_count, mask=mask, categories=CATEGORIES)

@app.route("/")
def index():
    s = request.args.get("s", "").strip().lower()
    cat = request.args.get("c", "").strip()
    tab = request.args.get("t", "all")

    items = q("SELECT * FROM items ORDER BY id DESC")
    if tab == "lost":
        items = [i for i in items if i["status"] in ("lost", "matched")]
    elif tab == "found":
        items = [i for i in items if i["status"] in ("found", "returned")]

    if cat:
        items = [i for i in items if i["category"] == cat]
    if s:
        items = [i for i in items if s in (i["name"] + " " + i["category"] + " " + i["description"] + " " + (i["place"] or "")).lower()]

    cnt = {r["status"]: r["c"] for r in q("SELECT status, COUNT(*) c FROM items GROUP BY status")}
    return render_template("index.html", items=items, s=s, cat=cat, tab=tab, cnt=cnt)

@app.route("/register", methods=["GET", "POST"])
def register():
    if me():
        return redirect(url_for("index"))
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        email = f.get("email", "").strip().lower()
        phone = f.get("phone", "").strip()
        dept = f.get("dept", "").strip()
        password = f.get("password", "")

        try:
            q("INSERT INTO users(name, email, phone, dept, pw, karma) VALUES(?,?,?,?,?,0)",
              (name, email, phone, dept, generate_password_hash(password)), commit=True)
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email is already registered.", "danger")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if me():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        u = q("SELECT * FROM users WHERE email=?", (email,), one=True)
        if u and check_password_hash(u["pw"], password):
            session["uid"] = u["id"]
            flash(f"Welcome back, {u['name']}!", "success")
            return redirect(url_for("index"))
        flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    u = me()
    mine = q("SELECT * FROM items WHERE owner_id=? ORDER BY id DESC", (u["id"],))
    claims = q("""
        SELECT c.*, i.name iname, i.status istatus 
        FROM claims c 
        JOIN items i ON i.id = c.item_id 
        WHERE c.finder_id = ? ORDER BY c.id DESC
    """, (u["id"],))
    my_found_reports = q("SELECT * FROM found_reports WHERE finder_id=? ORDER BY id DESC", (u["id"],))
    return render_template("dashboard.html", mine=mine, claims=claims, my_found_reports=my_found_reports)

@app.route("/lost/new", methods=["GET", "POST"])
@login_required
def lost_new():
    u = me()
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        cat = f.get("category", "").strip()
        date = f.get("date", "").strip()
        place = f.get("place", "").strip()
        desc = f.get("description", "").strip()
        photo = save_photo(request.files.get("photo"))

        questions = f.getlist("fq")
        answers = f.getlist("fa")
        feats = [{"q": qq.strip(), "a": aa.strip()} for qq, aa in zip(questions, answers) if qq.strip() and aa.strip()]

        if not (name and cat and date and desc):
            flash("Please fill in all required fields.", "danger")
            return render_template("lost_form.html", cats=CATEGORIES)

        iid = q("""
            INSERT INTO items(owner_id, name, category, date, place, description, photo, features, status)
            VALUES(?,?,?,?,?,?,?,?, 'lost')
        """, (u["id"], name, cat, date, place, desc, photo, json.dumps(feats)), commit=True)

        flash("Lost item reported successfully!", "success")
        return redirect(url_for("item", iid=iid))

    return render_template("lost_form.html", cats=CATEGORIES)

@app.route("/item/<int:iid>")
def item(iid):
    it = q("SELECT * FROM items WHERE id=?", (iid,), one=True)
    if not it:
        abort(404)

    u = me()
    is_owner = u and (u["id"] == it["owner_id"])
    owner = q("SELECT name, dept, email, phone FROM users WHERE id=?", (it["owner_id"],), one=True)

    claims = []
    if is_owner:
        claims = q("""
            SELECT c.*, u.name fname, u.phone fphone, u.email femail, u.dept fdept 
            FROM claims c
            JOIN users u ON u.id = c.finder_id 
            WHERE c.item_id = ? 
            ORDER BY c.id DESC
        """, (iid,))

    my_claim = None
    if u and not is_owner:
        my_claim = q("SELECT * FROM claims WHERE item_id=? AND finder_id=? ORDER BY id DESC LIMIT 1", (iid, u["id"]), one=True)

    return render_template("item.html", it=it, is_owner=is_owner, owner=owner, claims=claims, my_claim=my_claim)

@app.route("/item/<int:iid>/found", methods=["GET", "POST"])
@login_required
def found(iid):
    it = q("SELECT * FROM items WHERE id=?", (iid,), one=True)
    if not it:
        abort(404)

    u = me()
    if it["owner_id"] == u["id"]:
        flash("You cannot claim your own reported lost item.", "warning")
        return redirect(url_for("item", iid=iid))

    failed_attempts = q("SELECT COUNT(*) c FROM claims WHERE item_id=? AND finder_id=? AND status='rejected'",
                        (iid, u["id"]), one=True)["c"]
    if failed_attempts >= 3:
        flash("You have reached the maximum of 3 failed verification attempts for this item.", "danger")
        return redirect(url_for("item", iid=iid))

    feats = json.loads(it["features"] or "[]")
    if request.method == "POST":
        description = request.form.get("description", "").strip()
        answers = request.form.getlist("ans")
        photo = save_photo(request.files.get("photo"))

        if not photo:
            flash("Found item photo is MANDATORY for verification.", "danger")
            return render_template("found_form.html", it=it, feats=feats)

        owner_answers = [f.get("a", "") for f in feats]
        qa = list(zip(owner_answers, answers))
        lost_photo = os.path.join(UPLOADS, it["photo"]) if it["photo"] else None
        found_photo = os.path.join(UPLOADS, photo)

        result = verify(it, lost_photo, found_photo, qa, description)
        is_match = result.get("match", False)
        status = "verified" if is_match else "rejected"

        # Generate 6-Digit Secret Handover OTP if Matched
        generated_otp = str(secrets.randbelow(900000) + 100000) if is_match else None

        cid = q("""
            INSERT INTO claims(item_id, finder_id, photo, description, answers, result, status, handover_otp)
            VALUES(?,?,?,?,?,?,?,?)
        """, (iid, u["id"], photo, description, json.dumps(answers), json.dumps(result), status, generated_otp), commit=True)

        if is_match:
            q("UPDATE items SET status='matched' WHERE id=?", (iid,), commit=True)
            notify(it["owner_id"], f"Someone found your item '{it['name']}'! Your Secret Handover OTP is {generated_otp}. Give this OTP only when you physically receive the item.")
            flash(f"Verification successful! Match verified with AI. Coordinates unlocked.", "success")
        else:
            flash(f"Verification failed: {result.get('reason')}", "danger")

        return redirect(url_for("claim", cid=cid))

    return render_template("found_form.html", it=it, feats=feats)

@app.route("/claim/<int:cid>")
@login_required
def claim(cid):
    c = q("SELECT * FROM claims WHERE id=?", (cid,), one=True)
    if not c:
        abort(404)
    it = q("SELECT * FROM items WHERE id=?", (c["item_id"],), one=True)
    u = me()

    if u["id"] not in (c["finder_id"], it["owner_id"]) and u["role"] != "admin":
        abort(403)

    owner = q("SELECT name, phone, email, dept FROM users WHERE id=?", (it["owner_id"],), one=True)
    finder = q("SELECT name, phone, email, dept FROM users WHERE id=?", (c["finder_id"],), one=True)

    try:
        res = json.loads(c["result"] or "{}")
    except Exception:
        res = {"match": False, "confidence": 0.0, "reason": "Verification processed."}

    can_finder_see_owner = bool(c["finder_id"] == u["id"] and res.get("match") is True)
    can_owner_see_finder = bool(it["owner_id"] == u["id"] and c["status"] == "confirmed")

    return render_template("claim.html", c=c, it=it, owner=owner, finder=finder, res=res,
                           can_finder_see_owner=can_finder_see_owner,
                           can_owner_see_finder=can_owner_see_finder)

# ==========================================
# OTP VERIFICATION LOGIC (REPLACED QR)
# ==========================================
@app.route("/claim/<int:cid>/verify_otp", methods=["POST"])
@login_required
def verify_claim_otp(cid):
    c = q("SELECT * FROM claims WHERE id=?", (cid,), one=True)
    if not c:
        abort(404)

    entered_otp = request.form.get("otp", "").strip()
    it = q("SELECT * FROM items WHERE id=?", (c["item_id"],), one=True)

    if c["handover_otp"] and entered_otp == str(c["handover_otp"]).strip():
        q("UPDATE claims SET status='confirmed' WHERE id=?", (cid,), commit=True)
        q("UPDATE items SET status='returned' WHERE id=?", (it["id"],), commit=True)
        
        # Award +50 Karma Points to Finder
        add_karma(c["finder_id"], 50)

        notify(it["owner_id"], f"🎉 OTP Verified! Handover of '{it['name']}' has been officially completed.")
        notify(c["finder_id"], f"🎉 OTP Verified! You successfully returned '{it['name']}' and earned +50 Karma Points!")
        flash("Handover OTP Verified! Item marked as RETURNED and +50 Karma Points awarded to Finder!", "success")
    else:
        flash("Invalid OTP! Please check with the owner for their 6-digit Secret OTP.", "danger")

    return redirect(url_for("claim", cid=cid))

@app.route("/handover/verify", methods=["GET", "POST"])
def otp_verify_page():
    if request.method == "POST":
        claim_id = request.form.get("claim_id", "").strip()
        entered_otp = request.form.get("otp", "").strip()

        c = q("SELECT * FROM claims WHERE id=?", (claim_id,), one=True)
        if c and c["handover_otp"] and str(c["handover_otp"]).strip() == entered_otp:
            it = q("SELECT * FROM items WHERE id=?", (c["item_id"],), one=True)
            q("UPDATE claims SET status='confirmed' WHERE id=?", (claim_id,), commit=True)
            q("UPDATE items SET status='returned' WHERE id=?", (c["item_id"],), commit=True)
            add_karma(c["finder_id"], 50)

            notify(it["owner_id"], f"🎉 Security Desk OTP Verified! Handover of '{it['name']}' is complete.")
            notify(c["finder_id"], f"🎉 Security Desk OTP Verified! +50 Karma Points awarded for returning '{it['name']}'.")
            flash(f"Success! OTP matched for Claim #{claim_id}. '{it['name']}' is now marked as RETURNED! (+50 Karma awarded)", "success")
            return redirect(url_for("otp_verify_page"))
        else:
            flash("Invalid Claim ID or OTP. Please check the details from the owner.", "danger")

    return render_template("otp_verify.html")

@app.route("/item/<int:iid>/got_it", methods=["POST"])
@login_required
def got_it(iid):
    it = q("SELECT * FROM items WHERE id=?", (iid,), one=True)
    if not it or it["owner_id"] != me()["id"]:
        abort(403)

    q("UPDATE items SET status='returned' WHERE id=?", (iid,), commit=True)
    claim_row = q("SELECT finder_id FROM claims WHERE item_id=? AND status='confirmed' LIMIT 1", (iid,), one=True)
    if claim_row:
        add_karma(claim_row["finder_id"], 50)

    flash(f"Awesome! '{it['name']}' has been marked as returned.", "success")
    return redirect(url_for("item", iid=iid))

# ==========================================
# REVERSE AI AUTO-MATCHER
# ==========================================
@app.route("/found/new", methods=["GET", "POST"])
@login_required
def found_new():
    u = me()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        place = request.form.get("place", "").strip()
        desc = request.form.get("description", "").strip()
        photo = save_photo(request.files.get("photo"))

        if not (name and category and desc and photo):
            flash("Please provide item name, category, description and found photo.", "danger")
            return render_template("found_report_form.html", cats=CATEGORIES)

        fid = q("""
            INSERT INTO found_reports(finder_id, name, category, place, description, photo)
            VALUES(?,?,?,?,?,?)
        """, (u["id"], name, category, place, desc, photo), commit=True)

        found_dict = {"name": name, "category": category, "place": place, "description": desc, "photo": photo}
        candidate_lost_items = q("SELECT * FROM items WHERE status IN ('lost', 'matched') ORDER BY id DESC")
        best_id, match_score, match_reason = reverse_auto_match(found_dict, candidate_lost_items, UPLOADS)

        if best_id and match_score >= 0.60:
            q("UPDATE found_reports SET matched_lost_id=?, match_score=?, match_reason=? WHERE id=?",
              (best_id, match_score, match_reason, fid), commit=True)

            lost_owner = q("SELECT owner_id, name FROM items WHERE id=?", (best_id,), one=True)
            if lost_owner:
                notify(lost_owner["owner_id"],
                       f"🤖 AI Auto-Match: A found '{name}' matches your lost item with {int(match_score*100)}% similarity! Check it now.")

            flash(f"AI Auto-Matcher found a {int(match_score*100)}% match with an existing lost item!", "success")
            return redirect(url_for("found_match_view", fid=fid))

        flash("Item reported as found! Our background AI will notify you if anyone reports losing it.", "info")
        return redirect(url_for("dashboard"))

    return render_template("found_report_form.html", cats=CATEGORIES)

@app.route("/found/match/<int:fid>")
@login_required
def found_match_view(fid):
    f_rep = q("SELECT * FROM found_reports WHERE id=?", (fid,), one=True)
    if not f_rep or not f_rep["matched_lost_id"]:
        abort(404)

    lost_it = q("SELECT * FROM items WHERE id=?", (f_rep["matched_lost_id"],), one=True)
    lost_owner = q("SELECT name, dept, email, phone FROM users WHERE id=?", (lost_it["owner_id"],), one=True)
    finder = q("SELECT name, dept, email, phone FROM users WHERE id=?", (f_rep["finder_id"],), one=True)

    return render_template("found_match_view.html", f_rep=f_rep, lost_it=lost_it, lost_owner=lost_owner, finder=finder)

@app.route("/leaderboard")
def leaderboard():
    heroes = q("SELECT name, dept, karma FROM users WHERE karma > 0 ORDER BY karma DESC, name ASC LIMIT 20")
    total_returned = q("SELECT COUNT(*) c FROM items WHERE status='returned'", one=True)["c"]
    return render_template("leaderboard.html", heroes=heroes, total_returned=total_returned)

@app.route("/notifications")
@login_required
def notifications():
    u = me()
    notes = q("SELECT * FROM notes WHERE user_id=? ORDER BY id DESC", (u["id"],))
    q("UPDATE notes SET seen=1 WHERE user_id=?", (u["id"],), commit=True)
    return render_template("notes.html", notes=notes)

@app.route("/admin")
@admin_required
def admin():
    items = q("SELECT i.*, u.name oname, u.phone ophone, u.email oemail FROM items i JOIN users u ON u.id = i.owner_id ORDER BY i.id DESC")
    claims = q("SELECT c.*, i.name iname, u.name fname, u.phone fphone, u.email femail FROM claims c JOIN items i ON i.id = c.item_id JOIN users u ON u.id = c.finder_id ORDER BY c.id DESC")
    stats = {s: sum(1 for i in items if i["status"] == s) for s in ("lost", "matched", "found", "returned")}
    return render_template("admin.html", items=items, claims=claims, stats=stats)

@app.route("/admin/item/<int:iid>/delete", methods=["POST"])
@admin_required
def admin_delete(iid):
    q("DELETE FROM items WHERE id=?", (iid,), commit=True)
    q("DELETE FROM claims WHERE item_id=?", (iid,), commit=True)
    flash("Item and related claims removed by Admin.", "info")
    return redirect(url_for("admin"))

@app.route("/admin/item/<int:iid>/returned", methods=["POST"])
@admin_required
def admin_returned(iid):
    q("UPDATE items SET status='returned' WHERE id=?", (iid,), commit=True)
    flash("Item marked as returned by Admin.", "success")
    return redirect(url_for("admin"))

init_db()
if __name__ == "__main__":
    app.run(debug=True, port=5000)