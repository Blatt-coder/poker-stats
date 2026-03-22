import os
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from datetime import date, timedelta
import database as db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")


@app.before_request
def ensure_db():
    pass  # DB already initialized at startup


def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if "player_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    if "player_id" in session:
        return redirect(url_for("dashboard"))
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
            return redirect(url_for("dashboard"))
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
                return redirect(url_for("dashboard"))
            flash(err, "error")
    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    leaderboard = db.get_leaderboard()
    players = db.get_all_players()
    return render_template(
        "dashboard.html",
        leaderboard=leaderboard,
        players=players,
        current_player_id=session["player_id"],
    )


# ---------------------------------------------------------------------------
# Log a game result
# ---------------------------------------------------------------------------

@app.route("/log", methods=["GET", "POST"])
@login_required
def log_game():
    if request.method == "POST":
        try:
            amount = float(request.form["amount"])
        except ValueError:
            flash("Amount must be a number.", "error")
            return redirect(url_for("log_game"))

        game_date = request.form.get("game_date") or date.today().isoformat()
        notes = request.form.get("notes", "").strip()
        db.log_result(session["player_id"], amount, game_date, notes)
        flash("Result logged successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("log_game.html", today=date.today().isoformat())


# ---------------------------------------------------------------------------
# Player profile / stats page
# ---------------------------------------------------------------------------

@app.route("/player/<int:player_id>")
@login_required
def player_profile(player_id):
    player = db.get_player_by_id(player_id)
    if not player:
        flash("Player not found.", "error")
        return redirect(url_for("dashboard"))

    stats = db.get_summary_stats(player_id)
    results = db.get_results_for_player(player_id)

    # Build cumulative series for the line chart
    cumulative = []
    running = 0
    for r in results:
        running += r["amount"]
        cumulative.append({"date": r["game_date"], "value": round(running, 2)})

    return render_template(
        "player.html",
        player=player,
        stats=stats,
        results=results,
        cumulative=cumulative,
        is_own_profile=(player_id == session["player_id"]),
    )


# ---------------------------------------------------------------------------
# API endpoints for Chart.js
# ---------------------------------------------------------------------------

@app.route("/api/chart/cumulative")
@login_required
def api_cumulative():
    """
    Returns per-player cumulative series for the line chart.
    {
      labels: [date, ...],
      datasets: [{ label: username, data: [value, ...] }, ...]
    }
    """
    results = db.get_all_results_ordered()

    # Collect all unique dates and per-player running totals
    from collections import defaultdict
    player_series = defaultdict(list)  # username -> [(date, cumulative), ...]
    player_running = defaultdict(float)

    all_dates_set = set()
    for r in results:
        all_dates_set.add(r["game_date"])

    all_dates = sorted(all_dates_set)

    # For each player, build a cumulative value at each date they have a result
    player_data = defaultdict(dict)  # username -> {date: running_total}
    player_running = defaultdict(float)
    for r in results:
        player_running[r["username"]] += r["amount"]
        player_data[r["username"]][r["game_date"]] = round(player_running[r["username"]], 2)

    # Forward-fill so line chart looks continuous
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


@app.route("/api/chart/bar")
@login_required
def api_bar():
    """Bar chart data: net totals per player."""
    leaderboard = db.get_leaderboard()
    labels = [r["username"] for r in leaderboard]
    values = [r["net_total"] for r in leaderboard]
    colors = [
        "#10b981" if v >= 0 else "#ef4444"
        for v in values
    ]
    return jsonify({
        "labels": labels,
        "datasets": [{
            "label": "Net Win/Loss (₪)",
            "data": values,
            "backgroundColor": colors,
            "borderRadius": 6,
        }]
    })


@app.route("/api/chart/player/<int:player_id>")
@login_required
def api_player_cumulative(player_id):
    results = db.get_results_for_player(player_id)
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
        return redirect(url_for("dashboard"))
    try:
        amount = float(request.form["amount"])
    except ValueError:
        flash("Amount must be a number.", "error")
        return redirect(url_for("player_profile", player_id=session["player_id"]))
    game_date = request.form.get("game_date") or result["game_date"]
    notes = request.form.get("notes", "").strip()
    db.update_result(result_id, session["player_id"], amount, game_date, notes)
    flash("Result updated.", "success")
    return redirect(url_for("player_profile", player_id=session["player_id"]))


@app.route("/result/<int:result_id>/delete", methods=["POST"])
@login_required
def delete_result(result_id):
    result = db.get_result_by_id(result_id)
    if not result or result["player_id"] != session["player_id"]:
        flash("Result not found or not yours.", "error")
        return redirect(url_for("dashboard"))
    db.delete_result(result_id, session["player_id"])
    flash("Result deleted.", "success")
    return redirect(url_for("player_profile", player_id=session["player_id"]))


db.init_db()
db.seed_sample_data()

if __name__ == "__main__":
    app.run(debug=True)
