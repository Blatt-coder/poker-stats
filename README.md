# PokerStats

A web app for tracking poker wins and losses among a friend group.

## Features

- Player registration and login
- Log game results (amount won/lost, date, optional notes)
- Dashboard with leaderboard and two charts:
  - Line chart: each player's cumulative P&L over time
  - Bar chart: net results comparison across all players
- Per-player stats page with 7-day, 30-day, and all-time summaries
- Dark, clean UI

## Deploy to Render (recommended)

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → **Blueprint**
3. Connect your GitHub repo — Render will detect `render.yaml` and automatically create:
   - A free PostgreSQL database
   - A web service wired to it
4. Click **Apply** — the app will be live at `https://poker-stats.onrender.com` (or similar) in ~2 minutes

That's it. Share the URL with your friends.

## Local development

Requires a PostgreSQL database. The easiest option is a free [Neon](https://neon.tech) or [Supabase](https://supabase.com) database — copy the connection string they give you.

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://user:pass@host/dbname"
python app.py
```

Open your browser at **http://127.0.0.1:5000**

Tables and sample data are created automatically on first run.

## Demo accounts

| Username | Password |
|----------|----------|
| alice    | password |
| bob      | password |
| charlie  | password |
| diana    | password |

## Project structure

```
poker_proj/
├── app.py           # Flask routes and API endpoints
├── database.py      # SQLite schema, queries, seeding
├── requirements.txt
├── README.md
├── poker.db         # Created automatically on first run
├── templates/
│   ├── base.html    # Shared layout (navbar, flash messages)
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html   # Leaderboard + charts
│   ├── log_game.html    # Form to log a result
│   └── player.html      # Per-player stats + session history
└── static/
    └── css/
        └── style.css
```

## Database schema

**players**
- `id`, `username` (unique), `password_hash`, `created_at`

**game_results**
- `id`, `player_id` (FK), `amount` (positive = win, negative = loss), `game_date`, `notes`, `created_at`

## Notes

- Passwords are SHA-256 hashed (adequate for a private friend-group app; upgrade to bcrypt for anything public-facing)
- Charts are rendered with [Chart.js](https://www.chartjs.org/) loaded from CDN
- SQLite database lives next to `app.py` as `poker.db`
