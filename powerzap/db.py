"""Camada de persistência local com SQLite."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
    "powerzap",
)
DB_PATH = os.path.join(DB_DIR, "powerzap.db")
MEDIA_DIR = os.path.join(DB_DIR, "media")

MAX_MEDIA_BYTES = 16 * 1024 * 1024


def normalize_number(number: str) -> str:
    """Normaliza número em um só lugar.

    Preserva JID de grupo (ex: '123@g.us'). Para números comuns,
    mantém só dígitos (remove +, espaços, traços, parênteses).
    """
    raw = (number or "").strip()
    if not raw:
        return ""
    if "@g.us" in raw or "@s.whatsapp.net" in raw:
        return raw.strip()
    return "".join(ch for ch in raw if ch.isdigit())


def media_dir() -> str:
    os.makedirs(MEDIA_DIR, exist_ok=True)
    return MEDIA_DIR


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def conn():
    os.makedirs(DB_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#2196f3'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL,
    text TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    tag_id INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pendente',
    sent_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_status_time
    ON messages(status, scheduled_at);
CREATE TABLE IF NOT EXISTS contacts (
    number TEXT PRIMARY KEY,
    name TEXT,
    is_group INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
"""


def _ensure_columns():
    """Migração leve: adiciona colunas novas sem quebrar banco existente."""
    wanted = {
        "media_path": "TEXT",
        "media_type": "TEXT",
        "caption": "TEXT",
        "mimetype": "TEXT",
        "recurrence": "TEXT NOT NULL DEFAULT 'none'",
        "recurrence_end": "TEXT",
    }
    with conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(messages)")}
        for name, ddl in wanted.items():
            if name not in cols:
                c.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
    _ensure_columns()
    # Templates iniciais (só se tabela vazia)
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
        if total == 0:
            c.executemany(
                "INSERT INTO templates(title, body) VALUES(?, ?)",
                [
                    ("Boas-vindas", "Olá! Aqui é da nossa equipe. Como posso ajudar?"),
                    ("Lembrete", "Olá! Passando para lembrar do nosso compromisso amanhã."),
                    ("Agradecimento", "Obrigado pelo contato! Qualquer dúvida estou à disposição."),
                ],
            )


# ---------------- Configurações ----------------

DEFAULT_SETTINGS = {
    "evolution_url": "http://localhost:8080",
    "api_key": "powerzap",
    "instance": "powerzap",
    "my_number": "",
}


def get_settings() -> dict:
    init_db()
    with conn() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}
    return {**DEFAULT_SETTINGS, **rows}


def set_setting(key: str, value: str):
    with conn() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ---------------- Etiquetas ----------------

def list_tags():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM tags ORDER BY name")]


def create_tag(name: str, color: str):
    with conn() as c:
        c.execute("INSERT INTO tags(name, color) VALUES(?, ?)", (name, color))


def update_tag(tag_id: int, name: str, color: str):
    with conn() as c:
        c.execute(
            "UPDATE tags SET name=?, color=? WHERE id=?", (name, color, tag_id)
        )


def delete_tag(tag_id: int):
    with conn() as c:
        c.execute("DELETE FROM tags WHERE id=?", (tag_id,))


# ---------------- Mensagens ----------------

def list_messages(day: str | None = None, search: str = ""):
    query = (
        "SELECT m.*, t.name AS tag_name, t.color AS tag_color "
        "FROM messages m LEFT JOIN tags t ON t.id = m.tag_id "
    )
    params: list = []
    clauses: list = []
    if day:
        clauses.append("date(m.scheduled_at) = ?")
        params.append(day)
    if search and search.strip():
        clauses.append("(m.number LIKE ? OR m.text LIKE ? OR m.caption LIKE ?)")
        like = f"%{search.strip()}%"
        params += [like, like, like]
    if clauses:
        query += "WHERE " + " AND ".join(clauses) + " "
    query += "ORDER BY m.scheduled_at DESC"
    with conn() as c:
        return [dict(r) for r in c.execute(query, params)]


def list_pending(now: str):
    with conn() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM messages "
                "WHERE status='pendente' AND scheduled_at <= ? "
                "ORDER BY scheduled_at",
                (now,),
            )
        ]


def create_message(number: str, text: str, scheduled_at: str, tag_id: int | None,
                    media_path: str | None = None, media_type: str | None = None,
                    caption: str | None = None, mimetype: str | None = None,
                    recurrence: str = "none", recurrence_end: str | None = None):
    number = normalize_number(number)
    with conn() as c:
        c.execute(
            "INSERT INTO messages(number, text, scheduled_at, tag_id, created_at, "
            "media_path, media_type, caption, mimetype, recurrence, recurrence_end) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (number, text or "", scheduled_at, tag_id, _now(),
             media_path, media_type, caption, mimetype,
             recurrence or "none", recurrence_end),
        )


def update_message(msg_id: int, number: str, text: str, scheduled_at: str, tag_id: int | None,
                   media_path: str | None = None, media_type: str | None = None,
                   caption: str | None = None, mimetype: str | None = None,
                   recurrence: str = "none", recurrence_end: str | None = None):
    number = normalize_number(number)
    # Ao editar, a mensagem volta para 'pendente' (limpa erro/envio anterior),
    # senão uma mensagem 'falhou' ou 'enviada' editada nunca seria entregue,
    # pois o scheduler só busca status='pendente'.
    with conn() as c:
        c.execute(
            "UPDATE messages SET number=?, text=?, scheduled_at=?, tag_id=?, "
            "media_path=?, media_type=?, caption=?, mimetype=?, "
            "recurrence=?, recurrence_end=?, status='pendente', "
            "sent_at=NULL, error=NULL WHERE id=?",
            (number, text or "", scheduled_at, tag_id,
             media_path, media_type, caption, mimetype,
             recurrence or "none", recurrence_end, msg_id),
        )


def delete_message(msg_id: int):
    with conn() as c:
        c.execute("DELETE FROM messages WHERE id=?", (msg_id,))


def mark_sent(msg_id: int):
    with conn() as c:
        c.execute(
            "UPDATE messages SET status='enviada', sent_at=? WHERE id=?",
            (_now(), msg_id),
        )


def mark_failed(msg_id: int, error: str):
    with conn() as c:
        c.execute(
            "UPDATE messages SET status='falhou', error=? WHERE id=?",
            (error[:500], msg_id),
        )


def get_message(msg_id: int):
    with conn() as c:
        row = c.execute(
            "SELECT m.*, t.name AS tag_name, t.color AS tag_color "
            "FROM messages m LEFT JOIN tags t ON t.id = m.tag_id "
            "WHERE m.id=?", (msg_id,),
        ).fetchone()
        return dict(row) if row else None


def retry_message(msg_id: int):
    """Volta mensagem com falha para pendente (botão Tentar de novo)."""
    with conn() as c:
        c.execute(
            "UPDATE messages SET status='pendente', error=NULL WHERE id=?",
            (msg_id,),
        )


def duplicate_message(msg_id: int):
    """Duplica agendamento para o dia seguinte no mesmo horário."""
    from datetime import datetime, timedelta
    msg = get_message(msg_id)
    if not msg:
        return
    try:
        dt = datetime.strptime(msg["scheduled_at"], "%Y-%m-%d %H:%M:%S") + timedelta(days=1)
        new_dt = dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        new_dt = msg["scheduled_at"]
    with conn() as c:
        c.execute(
            "INSERT INTO messages(number, text, scheduled_at, tag_id, created_at, "
            "media_path, media_type, caption, mimetype, recurrence, recurrence_end, status) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'none', NULL, 'pendente')",
            (msg["number"], msg["text"], new_dt, msg["tag_id"], _now(),
             msg.get("media_path"), msg.get("media_type"), msg.get("caption"),
             msg.get("mimetype")),
        )


def schedule_next_occurrence(msg: dict):
    """Cria próxima ocorrência para mensagens recorrentes após envio."""
    from datetime import datetime, timedelta
    rec = (msg.get("recurrence") or "none").lower()
    if rec not in ("daily", "weekly"):
        return
    try:
        dt = datetime.strptime(msg["scheduled_at"], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return
    delta = timedelta(days=1) if rec == "daily" else timedelta(weeks=1)
    nxt = dt + delta
    end_raw = msg.get("recurrence_end")
    if end_raw:
        try:
            end_dt = datetime.strptime(end_raw, "%Y-%m-%d")
            if nxt.date() > end_dt.date():
                return
        except ValueError:
            pass
    with conn() as c:
        c.execute(
            "INSERT INTO messages(number, text, scheduled_at, tag_id, created_at, "
            "media_path, media_type, caption, mimetype, recurrence, recurrence_end, status) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendente')",
            (msg["number"], msg.get("text") or "", nxt.strftime("%Y-%m-%d %H:%M:%S"),
             msg.get("tag_id"), _now(), msg.get("media_path"), msg.get("media_type"),
             msg.get("caption"), msg.get("mimetype"), rec, end_raw),
        )


# ---------------- Modelos rápidos ----------------

def list_templates():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM templates ORDER BY title")]


def create_template(title: str, body: str):
    with conn() as c:
        c.execute("INSERT INTO templates(title, body) VALUES(?, ?)", (title, body))


# ---------------- Contatos ----------------

def replace_contacts(contacts: list):
    # Não apaga o cache se a API voltou vazia (comum logo após o QR Code).
    # Isso evita o seletor visual ficar com "Nada por aqui".
    if not contacts:
        return
    now = _now()
    with conn() as c:
        c.execute("DELETE FROM contacts")
        c.executemany(
            "INSERT INTO contacts(number, name, is_group, updated_at) VALUES(?, ?, ?, ?)",
            [
                (ct["number"], ct["name"], 1 if ct.get("is_group") else 0, now)
                for ct in contacts
            ],
        )


def list_contacts(query: str = ""):
    sql = "SELECT * FROM contacts "
    params: list = []
    if query:
        sql += "WHERE lower(name) LIKE ? OR number LIKE ? "
        like = f"%{query.lower()}%"
        params = [like, f"%{query}%"]
    sql += ("ORDER BY is_group, CASE WHEN name = '' THEN 1 ELSE 0 END, name "
            "LIMIT 10000")
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params)]


def count_contacts() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]


def count_groups() -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM contacts WHERE is_group=1").fetchone()[0]


def filter_local(contacts: list, query: str, kind: str = "all") -> list:
    q = (query or "").strip().lower()
    out = []
    for ct in contacts:
        is_group = bool(ct.get("is_group"))
        if kind == "contacts" and is_group:
            continue
        if kind == "groups" and not is_group:
            continue
        if not q:
            out.append(ct)
            continue
        name = (ct.get("name") or "").lower()
        number = str(ct.get("number") or "")
        if q in name or q in number.lower():
            out.append(ct)
    return out
