import os
from contextlib import contextmanager

try:
    import psycopg
except ImportError:
    psycopg = None

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customers (
    id BIGSERIAL PRIMARY KEY,
    business_id BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_id, phone)
);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    business_id BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id BIGINT REFERENCES customers(id) ON DELETE SET NULL,
    customer_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    service TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS appointments_business_date_idx
ON appointments (business_id, date, time);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    business_id BIGINT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    message_id TEXT,
    channel TEXT NOT NULL,
    direction TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT 'other',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS messages_business_created_idx
ON messages (business_id, created_at DESC);
"""

@contextmanager
def connection():
    if not DATABASE_URL or psycopg is None:
        raise RuntimeError("DATABASE_URL veya psycopg kullanılamıyor.")
    with psycopg.connect(DATABASE_URL) as conn:
        yield conn

def enabled():
    return bool(DATABASE_URL and psycopg is not None)

def ensure_schema():
    if not enabled():
        return
    with connection() as conn:
        conn.execute(SCHEMA)
        conn.commit()

def get_business_id(config):
    ensure_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT id FROM businesses WHERE name = %s ORDER BY id LIMIT 1",
            (config.get("business_name", "Demo İşletme"),)
        ).fetchone()
        if row:
            return row[0]
        row = conn.execute(
            "INSERT INTO businesses (name, config) VALUES (%s, %s) RETURNING id",
            (config.get("business_name", "Demo İşletme"), psycopg.types.json.Json(config))
        ).fetchone()
        conn.commit()
        return row[0]

def load_appointments(config):
    business_id = get_business_id(config)
    with connection() as conn:
        rows = conn.execute("""
            SELECT id, customer_name, phone, date, time, service, note, status,
                   to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            FROM appointments
            WHERE business_id = %s
            ORDER BY created_at
        """, (business_id,)).fetchall()
    keys = ["id","customer_name","phone","date","time","service","note","status","created_at"]
    return [dict(zip(keys, row)) for row in rows]

def save_appointments(config, items):
    business_id = get_business_id(config)
    with connection() as conn:
        for item in items:
            conn.execute("""
                INSERT INTO customers (business_id, name, phone)
                VALUES (%s, %s, %s)
                ON CONFLICT (business_id, phone)
                DO UPDATE SET name = EXCLUDED.name, updated_at = NOW()
            """, (business_id, item.get("customer_name",""), item.get("phone","")))
            customer = conn.execute(
                "SELECT id FROM customers WHERE business_id=%s AND phone=%s",
                (business_id, item.get("phone",""))
            ).fetchone()
            conn.execute("""
                INSERT INTO appointments
                (id,business_id,customer_id,customer_name,phone,date,time,service,note,status,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz,NOW()))
                ON CONFLICT (id) DO UPDATE SET
                  customer_id=EXCLUDED.customer_id, customer_name=EXCLUDED.customer_name,
                  phone=EXCLUDED.phone, date=EXCLUDED.date, time=EXCLUDED.time,
                  service=EXCLUDED.service, note=EXCLUDED.note, status=EXCLUDED.status
            """, (
                item["id"], business_id, customer[0] if customer else None,
                item.get("customer_name",""), item.get("phone",""), item.get("date",""),
                item.get("time",""), item.get("service",""), item.get("note",""),
                item.get("status","pending"), item.get("created_at")
            ))
        conn.commit()

def load_messages(config):
    business_id = get_business_id(config)
    with connection() as conn:
        rows = conn.execute("""
            SELECT id, channel, direction, customer_name, phone, message, intent,
                   to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                   COALESCE(message_id,'')
            FROM messages WHERE business_id=%s ORDER BY created_at
        """, (business_id,)).fetchall()
    keys = ["id","channel","direction","customer_name","phone","message","intent","created_at","message_id"]
    return [dict(zip(keys,row)) for row in rows]

def save_messages(config, items):
    business_id = get_business_id(config)
    with connection() as conn:
        for item in items:
            conn.execute("""
                INSERT INTO messages
                (id,business_id,message_id,channel,direction,customer_name,phone,message,intent,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz,NOW()))
                ON CONFLICT (id) DO UPDATE SET
                  message_id=EXCLUDED.message_id, channel=EXCLUDED.channel,
                  direction=EXCLUDED.direction, customer_name=EXCLUDED.customer_name,
                  phone=EXCLUDED.phone, message=EXCLUDED.message, intent=EXCLUDED.intent
            """, (
                item["id"], business_id, item.get("message_id",""), item.get("channel","web"),
                item.get("direction","inbound"), item.get("customer_name",""), item.get("phone",""),
                item.get("message",""), item.get("intent","other"), item.get("created_at")
            ))
        conn.commit()
