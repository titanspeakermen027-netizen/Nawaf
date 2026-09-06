import sqlite3
from pathlib import Path

DB_PATH = Path("nawaf.sqlite3")


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id INTEGER PRIMARY KEY,
            announcement_channel INTEGER,
            ad_channel INTEGER,
            application_channel INTEGER,
            application_review_channel INTEGER,
            dhikr_channel INTEGER,
            dhikr_enabled INTEGER DEFAULT 0,
            dhikr_interval INTEGER DEFAULT 3600,
            currency_name TEXT DEFAULT 'Nawaf Coin',
            currency_symbol TEXT DEFAULT '🪙',
            ticket_category INTEGER,
            ticket_log_channel INTEGER,
            ticket_panel_channel INTEGER,
            ticket_panel_message INTEGER,
            ticket_panel_title TEXT DEFAULT '🎫 الدعم الفني',
            ticket_panel_description TEXT DEFAULT 'اضغط على الزر لفتح تذكرة.',
            ticket_rating_max INTEGER DEFAULT 10,
            application_panel_channel INTEGER,
            application_panel_message INTEGER,
            shop_order_channel INTEGER,
            jail_role_id INTEGER,
            jail_channel_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            closed_by INTEGER,
            closed_at TEXT,
            rating INTEGER,
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xp (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS level_rewards (
            guild_id INTEGER NOT NULL,
            level INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            enabled INTEGER DEFAULT 1,
            PRIMARY KEY (guild_id, level)
        );
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            type_id INTEGER,
            answers TEXT NOT NULL,
            image_url TEXT,
            status TEXT DEFAULT 'pending',
            reviewer_id INTEGER,
            review_reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS application_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'تقديم الإدارة',
            description TEXT DEFAULT 'اضغط على الزر لبدء التقديم.',
            color INTEGER DEFAULT 5793266,
            image_url TEXT,
            review_channel_id INTEGER,
            result_channel_id INTEGER,
            accepted_role_id INTEGER,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS application_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            question TEXT NOT NULL,
            required INTEGER DEFAULT 1,
            UNIQUE(type_id, position)
        );
        CREATE TABLE IF NOT EXISTS balances (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            balance INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS points (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS sent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS shop_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price INTEGER NOT NULL,
            stock INTEGER DEFAULT -1,
            active INTEGER DEFAULT 1,
            delivery_type TEXT DEFAULT 'generic',
            role_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS shop_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            details TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS jails (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            previous_roles TEXT DEFAULT '[]',
            expires_at TEXT,
            jailed_by INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        );
        """)

        def add_column(table, column, definition):
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # SQLite does not allow CURRENT_TIMESTAMP as a default in ALTER TABLE.
        # New columns that need a timestamp are therefore added without a
        # non-constant default, then populated explicitly for old rows.
        add_column("guild_config", "ticket_panel_title", "TEXT")
        add_column("guild_config", "ticket_panel_description", "TEXT")
        add_column("guild_config", "ticket_rating_max", "INTEGER")
        add_column("guild_config", "jail_role_id", "INTEGER")
        add_column("guild_config", "jail_channel_id", "INTEGER")
        add_column("applications", "type_id", "INTEGER")
        add_column("applications", "image_url", "TEXT")
        add_column("applications", "review_reason", "TEXT")
        add_column("tickets", "created_at", "TEXT")
        add_column("shop_products", "delivery_type", "TEXT")
        add_column("shop_products", "role_id", "INTEGER")
        add_column("shop_orders", "details", "TEXT")

        con.execute("UPDATE guild_config SET ticket_panel_title='🎫 الدعم الفني' WHERE ticket_panel_title IS NULL")
        con.execute("UPDATE guild_config SET ticket_panel_description='اضغط على الزر لفتح تذكرة.' WHERE ticket_panel_description IS NULL")
        con.execute("UPDATE guild_config SET ticket_rating_max=10 WHERE ticket_rating_max IS NULL OR ticket_rating_max < 1")
        con.execute("UPDATE tickets SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL")
        con.execute("UPDATE shop_products SET delivery_type='generic' WHERE delivery_type IS NULL OR delivery_type=''")


def ensure_guild(guild_id):
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO guild_config (guild_id) VALUES (?)", (guild_id,))


def get_config(guild_id):
    ensure_guild(guild_id)
    with connect() as con:
        return con.execute("SELECT * FROM guild_config WHERE guild_id=?", (guild_id,)).fetchone()


def set_config(guild_id, **values):
    ensure_guild(guild_id)
    allowed = set(get_config(guild_id).keys()) - {"guild_id"}
    values = {k: v for k, v in values.items() if k in allowed}
    if not values:
        return
    fields = ", ".join(f"{k}=?" for k in values)
    with connect() as con:
        con.execute(f"UPDATE guild_config SET {fields} WHERE guild_id=?", (*values.values(), guild_id))
