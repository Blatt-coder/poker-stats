import os
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from datetime import date
import database as db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "player_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def _check_table_access(table_id):
    """Returns table if user is a member, else None."""
    table = db.get_table_by_id(table_id)
    if not table or not db.is_table_member(table_id, session["player_id"]):
        return None
    return table


@app.context_processor
def inject_current_table():
    table = None
    if "current_table_id" in session:
        table = db.get_table_by_id(session["current_table_id"])
    return {"current_table": table}


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if "player_id" in session:
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        player = db.verify_player(username, password)
        if player:
            session["player_id"] = player["id"]
            session["username"] = player["username"]
            if "pending_invite" in session:
                code = session.pop("pending_invite")
                return redirect(url_for("join_table", code=code))
            return redirect(url_for("home"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("Username and password are required.", "error")
        elif len(password) < 4:
            flash("Password must be at least 4 characters.", "error")
        else:
            ok, err = db.create_player(username, password)
            if ok:
                player = db.get_player_by_username(username)
                session["player_id"] = player["id"]
                session["username"] = player["username"]
                flash(f"Welcome, {username}!", "success")
                if "pending_invite" in session:
                    code = session.pop("pending_invite")
                    return redirect(url_for("join_table", code=code))
                return redirect(url_for("home"))
            flash(err, "error")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Home — table list
# ---------------------------------------------------------------------------

@app.route("/home")
@login_required
def home():
    session.pop("current_table_id", None)
    tables = db.get_tables_for_player(session["player_id"])
    member_counts = {t["id"]: db.get_table_member_count(t["id"]) for t in tables}
    return render_template("home.html", tables=tables, member_counts=member_counts)


# ---------------------------------------------------------------------------
# Create table
# ---------------------------------------------------------------------------

@app.route("/table/create", methods=["GET", "POST"])
@login_required
def create_table():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Table name is required.", "error")
        else:
            table_id, _code = db.create_table(name, session["player_id"])
            flash(f'Table "{name}" created!', "success")
            return redirect(url_for("dashboard", table_id=table_id))
    return render_template("create_table.html")


# ---------------------------------------------------------------------------
# Join via invite link
# ---------------------------------------------------------------------------

@app.route("/join/<code>")
def join_table(code):
    if "player_id" not in session:
        session["pending_invite"] = code
        return redirect(url_for("login"))
    table = db.get_table_by_invite_code(code)
    if not table:
        flash("Invalid invite link.", "error")
        return redirect(url_for("home"))
    if db.is_table_member(table["id"], session["player_id"]):
        flash(f'You are already a member of "{table["name"]}".', "info")
    else:
        db.join_table(table["id"], session["player_id"])
        flash(f'You joined "{table["name"]}"!', "success")
    return redirect(url_for("dashboard", table_id=table["id"]))


# ---------------------------------------------------------------------------
# Table dashboard
# ---------------------------------------------------------------------------

@app.route("/table/<int:table_id>/dashboard")
@login_required
def dashboard(table_id):
    table = _check_table_access(table_id)
    if not table:
        flash("Table not found or you are not a member.", "error")
        return redirect(url_for("home"))
    session["current_table_id"] = table_id
    leaderboard = db.get_leaderboard(table_id)
    return render_template(
        "dashboard.html",
        table=table,
        leaderboard=leaderboard,
        current_player_id=session["player_id"],
    )


# ---------------------------------------------------------------------------
# Log a game result
# ---------------------------------------------------------------------------

@app.route("/table/<int:table_id>/log", methods=["GET", "POST"])
@login_required
def log_game(table_id):
    table = _check_table_access(table_id)
    if not table:
        flash("Table not found or you are not a member.", "error")
        return redirect(url_for("home"))
    session["current_table_id"] = table_id
    if request.method == "POST":
        try:
            amount = float(request.form["amount"])
        except ValueError:
            flash("Amount must be a number.", "error")
            return redirect(url_for("log_game", table_id=table_id))
        game_date = request.form.get("game_date") or date.today().isoformat()
        notes = request.form.get("notes", "").strip()
        db.log_result(session["player_id"], amount, game_date, notes, table_id)
        flash("Result logged successfully!", "success")
        return redirect(url_for("dashboard", table_id=table_id))
    return render_template("log_game.html", table=table, today=date.today().isoformat())


# ---------------------------------------------------------------------------
# Player profile (table-scoped)
# ---------------------------------------------------------------------------

@app.route("/table/<int:table_id>/player/<int:player_id>")
@login_required
def player_profile(table_id, player_id):
    table = _check_table_access(table_id)
    if not table:
        flash("Table not found or you are not a member.", "error")
        return redirect(url_for("home"))
    session["current_table_id"] = table_id
    player = db.get_player_by_id(player_id)
    if not player or not db.is_table_member(table_id, player_id):
        flash("Player not found in this table.", "error")
        return redirect(url_for("dashboard", table_id=table_id))

    stats = db.get_summary_stats(player_id, table_id)
    results = db.get_results_for_player(player_id, table_id)

    cumulative = []
    running = 0
    for r in results:
        running += r["amount"]
        cumulative.append({"date": r["game_date"], "value": round(running, 2)})

    return render_template(
        "player.html",
        table=table,
        player=player,
        stats=stats,
        results=results,
        cumulative=cumulative,
        is_own_profile=(player_id == session["player_id"]),
    )


# ---------------------------------------------------------------------------
# API endpoints for Chart.js
# ---------------------------------------------------------------------------

@app.route("/table/<int:table_id>/api/chart/cumulative")
@login_required
def api_cumulative(table_id):
    if not db.is_table_member(table_id, session["player_id"]):
        return jsonify({"error": "Forbidden"}), 403

    results = db.get_all_results_ordered(table_id)

    from collections import defaultdict
    all_dates_set = set()
    for r in results:
        all_dates_set.add(r["game_date"])
    all_dates = sorted(all_dates_set)

    player_data = defaultdict(dict)
    player_running = defaultdict(float)
    for r in results:
        player_running[r["username"]] += r["amount"]
        player_data[r["username"]][r["game_date"]] = round(player_running[r["username"]], 2)

    datasets = []
    colors = ["#6366f1", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6"]
    for i, (username, date_map) in enumerate(sorted(player_data.items())):
        data = []
        last = 0
        for d in all_dates:
            if d in date_map:
                last = date_map[d]
            data.append(last)
        datasets.append({
            "label": username,
            "data": data,
            "borderColor": colors[i % len(colors)],
            "backgroundColor": colors[i % len(colors)] + "22",
            "tension": 0.3,
            "fill": False,
        })

    return jsonify({"labels": all_dates, "datasets": datasets})


@app.route("/table/<int:table_id>/api/chart/bar")
@login_required
def api_bar(table_id):
    if not db.is_table_member(table_id, session["player_id"]):
        return jsonify({"error": "Forbidden"}), 403
    leaderboard = db.get_leaderboard(table_id)
    labels = [r["username"] for r in leaderboard]
    values = [r["net_total"] for r in leaderboard]
    colors = ["#10b981" if v >= 0 else "#ef4444" for v in values]
    return jsonify({
        "labels": labels,
        "datasets": [{
            "label": "Net Win/Loss (₪)",
            "data": values,
            "backgroundColor": colors,
            "borderRadius": 6,
        }]
    })


@app.route("/table/<int:table_id>/api/chart/player/<int:player_id>")
@login_required
def api_player_cumulative(table_id, player_id):
    if not db.is_table_member(table_id, session["player_id"]):
        return jsonify({"error": "Forbidden"}), 403
    results = db.get_results_for_player(player_id, table_id)
    labels, data = [], []
    running = 0
    for r in results:
        running += r["amount"]
        labels.append(r["game_date"])
        data.append(round(running, 2))
    return jsonify({"labels": labels, "data": data})


# ---------------------------------------------------------------------------
# Edit / delete a result (only the owner can do this)
# ---------------------------------------------------------------------------

@app.route("/result/<int:result_id>/edit", methods=["POST"])
@login_required
def edit_result(result_id):
    result = db.get_result_by_id(result_id)
    if not result or result["player_id"] != session["player_id"]:
        flash("Result not found or not yours.", "error")
        return redirect(url_for("home"))
    table_id = result["table_id"]
    try:
        amount = float(request.form["amount"])
    except ValueError:
        flash("Amount must be a number.", "error")
        return redirect(url_for("player_profile", table_id=table_id, player_id=session["player_id"]))
    game_date = request.form.get("game_date") or result["game_date"]
    notes = request.form.get("notes", "").strip()
    db.update_result(result_id, session["player_id"], amount, game_date, notes)
    flash("Result updated.", "success")
    return redirect(url_for("player_profile", table_id=table_id, player_id=session["player_id"]))


@app.route("/result/<int:result_id>/delete", methods=["POST"])
@login_required
def delete_result(result_id):
    result = db.get_result_by_id(result_id)
    if not result or result["player_id"] != session["player_id"]:
        flash("Result not found or not yours.", "error")
        return redirect(url_for("home"))
    table_id = result["table_id"]
    db.delete_result(result_id, session["player_id"])
    flash("Result deleted.", "success")
    return redirect(url_for("player_profile", table_id=table_id, player_id=session["player_id"]))


db.init_db()
db.delete_demo_data()

if __name__ == "__main__":
    app.run(debug=True)
