import getpass
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import timedelta
from functools import wraps
from pathlib import Path

import click
from flask import Flask, abort, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from content import PAGES, ROOT, default_records, page_template, read_text, render_page, valid_link

app = Flask(__name__, static_folder=None)
database = Path(os.environ.get("SCIENCE_DATABASE", ROOT / "instance" / "site.sqlite3"))
database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
database.touch(mode=0o600, exist_ok=True)


def connect():
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


with closing(connect()) as connection, connection:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS staff (username TEXT PRIMARY KEY, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS content (page TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT NOT NULL, at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS login_attempts_ip_at ON login_attempts(ip, at);
    """)
    connection.execute("INSERT OR IGNORE INTO settings VALUES ('secret', ?)", (secrets.token_hex(32),))
    app.secret_key = connection.execute("SELECT value FROM settings WHERE name='secret'").fetchone()[0]

app.config.update(
    MAX_CONTENT_LENGTH=1024 * 1024,
    MAX_FORM_MEMORY_SIZE=1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SCIENCE_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_REFRESH_EACH_REQUEST=False,
)
DUMMY_PASSWORD = generate_password_hash(secrets.token_hex(32))


def read_content(page):
    row = g.db.execute("SELECT revision, data FROM content WHERE page=?", (page,)).fetchone()
    return (row["revision"], json.loads(row["data"])) if row else (0, {})


@app.before_request
def get_database():
    g.db = connect()


@app.teardown_appcontext
def close_database(error):
    if "db" in g:
        g.db.close()


@app.after_request
def response_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    elif response.mimetype == "text/html":
        response.headers["Cache-Control"] = "no-cache"
    return response


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    token = request.form.get("csrf", "")
    if not session.get("csrf") or not secrets.compare_digest(session["csrf"].encode(), token.encode()):
        abort(403)


def staff_only(view):
    @wraps(view)
    def protected(*args, **kwargs):
        row = g.db.execute("SELECT password FROM staff WHERE username=?", (session.get("staff"),)).fetchone()
        if not row or not secrets.compare_digest(row["password"].rsplit("$", 1)[0], session.get("version", "")):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return protected


@app.get("/")
@app.get("/<page>.html")
def public_page(page="index"):
    if page not in PAGES:
        abort(404)
    _, values = read_content(page)
    return render_page(page, values)


@app.get("/<any(styles.css,site.js,admin.js):filename>")
def frontend_file(filename):
    return send_from_directory(ROOT, filename)


@app.get("/assets/laboratory.webp")
def laboratory_image():
    return send_from_directory(ROOT / "assets", "laboratory.webp")


@app.route("/admin", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        check_csrf()
        ip = request.remote_addr or "unknown"
        now = int(time.time())
        with g.db:
            g.db.execute("DELETE FROM login_attempts WHERE at < ?", (now - 900,))
            attempts = g.db.execute("SELECT count(*) FROM login_attempts WHERE ip=?", (ip,)).fetchone()[0]
            if attempts >= 10:
                return render_template("login.html", csrf=csrf_token(),
                                       error="Кіру әрекеттері тым көп. 15 минуттан кейін қайталаңыз."), 429
            g.db.execute("INSERT INTO login_attempts VALUES (?, ?)", (ip, now))
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        row = g.db.execute("SELECT password FROM staff WHERE username=?", (username,)).fetchone()
        password_hash = row["password"] if row else DUMMY_PASSWORD
        if len(password) <= 256 and check_password_hash(password_hash, password) and row:
            with g.db:
                g.db.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
            session.clear()
            session.permanent = True
            session["staff"] = username
            session["version"] = password_hash.rsplit("$", 1)[0]
            return redirect(url_for("edit", page="index"))
        return render_template("login.html", csrf=csrf_token(), error="Логин немесе құпиясөз қате."), 401
    return render_template("login.html", csrf=csrf_token())


@app.post("/admin/logout")
@staff_only
def logout():
    check_csrf()
    session.clear()
    return redirect(url_for("login"))


def submitted_records(anchor):
    titles = request.form.getlist(f"{anchor}.title")
    bodies = request.form.getlist(f"{anchor}.body")
    labels = request.form.getlist(f"{anchor}.label")
    urls = request.form.getlist(f"{anchor}.url")
    examples = request.form.getlist(f"{anchor}.example")
    if len(titles) > 40 or not (len(titles) == len(bodies) == len(labels) == len(urls) == len(examples)):
        raise ValueError("Бір бөлімде 40 жазбаға дейін сақтауға болады.")
    records = []
    for title, body, label, url, example in zip(titles, bodies, labels, urls, examples):
        title, body, label, url = (value.strip() for value in (title, body, label, url))
        if not title or not body or len(title) > 300 or len(body) > 6000 or len(label) > 300 or len(url) > 1000:
            raise ValueError("Әр жазбаның тақырыбы мен мәтінін толтырып, ұзындық шектерін сақтаңыз.")
        if bool(label) != bool(url) or (url and not valid_link(url)):
            raise ValueError("Сілтеме үшін атау мен жарамды HTTP(S) немесе сайт ішіндегі мекенжайды енгізіңіз.")
        records.append(dict(title=title, body=body, label=label, url=url, example=example == "yes"))
    return records


@app.route("/admin/edit/<page>", methods=["GET", "POST"])
@staff_only
def edit(page):
    if page not in PAGES:
        abort(404)
    revision, saved = read_content(page)
    _, fields, sections = page_template(page)
    values = {key: saved.get("fields", {}).get(key, read_text(tag)) for key, (_, tag) in fields.items()}
    records = {anchor: saved.get("records", {}).get(anchor, default_records(section))
               for anchor, section in sections.items()}
    if request.method == "POST":
        check_csrf()
        if request.form.get("revision") != str(revision):
            return render_template("conflict.html", page=page), 409
        try:
            values = {key: request.form.get(key, "").strip() for key in fields}
            if any(len(value) > 4000 or (not value and key != "notice") for key, value in values.items()):
                raise ValueError("Бос өрістерді толтырыңыз. Әр өріске 4000 таңбаға дейін енгізуге болады.")
            records = {anchor: submitted_records(anchor) for anchor in sections}
        except ValueError as error:
            # Keep submitted rows visible so the editor can correct them.
            for anchor in sections:
                records[anchor] = [
                    dict(title=title, body=body, label=label, url=url, example=example == "yes")
                    for title, body, label, url, example in zip(
                        *(request.form.getlist(f"{anchor}.{key}") for key in ("title", "body", "label", "url", "example"))
                    )
                ]
            flash(str(error), "error")
            return editor(page, revision, fields, values, sections, records), 400
        data = json.dumps(dict(fields=values, records=records), ensure_ascii=False)
        with g.db:
            result = g.db.execute("""
                INSERT INTO content(page, revision, data) VALUES (?, ?, ?)
                ON CONFLICT(page) DO UPDATE SET revision=excluded.revision, data=excluded.data
                WHERE content.revision=?
            """, (page, revision + 1, data, revision))
        if result.rowcount != 1:
            return render_template("conflict.html", page=page), 409
        flash("Өзгерістер сақталды және сайтта көрінеді.", "success")
        return redirect(url_for("edit", page=page))
    return editor(page, revision, fields, values, sections, records)


def editor(page, revision, fields, values, sections, records):
    return render_template("edit.html", page=page, pages=PAGES, revision=revision, fields=fields,
                           values=values, sections=sections, records=records, csrf=csrf_token())


@app.cli.command("staff-password")
@click.argument("username")
def staff_password(username):
    """Create a staff login or reset its password."""
    username = username.strip().lower()
    if not username or len(username) > 80 or any(char.isspace() for char in username):
        raise click.ClickException("Логин 1–80 таңбадан тұруы және бос орынсыз жазылуы керек.")
    password = getpass.getpass("Құпиясөз: ")
    if not 12 <= len(password) <= 256 or password != getpass.getpass("Құпиясөзді қайталаңыз: "):
        raise click.ClickException("Құпиясөздер сәйкес болуы және 12–256 таңбадан тұруы керек.")
    with closing(connect()) as connection, connection:
        connection.execute("""
            INSERT INTO staff VALUES (?, ?) ON CONFLICT(username) DO UPDATE SET password=excluded.password
        """, (username, generate_password_hash(password)))
    click.echo(f"Қызметкер аккаунты сақталды: {username}")


@app.cli.command("staff-remove")
@click.argument("username")
def staff_remove(username):
    """Remove a staff login and revoke its active sessions."""
    with closing(connect()) as connection, connection:
        result = connection.execute("DELETE FROM staff WHERE username=?", (username.strip().lower(),))
    if result.rowcount == 0:
        raise click.ClickException("Логин табылмады.")
    click.echo("Қызметкер аккаунты жойылды.")
