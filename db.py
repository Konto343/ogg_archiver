import sqlite3
conn = sqlite3.connect("cache.db")

def init():
    cursor = conn.cursor()
    types = ['channel', 'channel_alt', 'playlist', 'video']

    for cache_type in types:
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {cache_type}(item_id PRIMARY KEY, json TEXT NOT NULL);")
        conn.commit()

def add_entry(cache_type, item_id, json):
    cursor = conn.cursor()
    cursor.execute(f"INSERT INTO {cache_type} (item_id, json) VALUES (?, ?)", (item_id, json))
    conn.commit()

def update_entry(cache_type, item_id, new_json):
    cursor = conn.cursor()
    cursor.execute(f"UPDATE {cache_type} SET json = '{new_json}' WHERE item_id = '{item_id}'")
    conn.commit()

def get_entry(cache_type, item_id):
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT json FROM {cache_type} WHERE item_id = '{item_id}'")
        return cursor.fetchone()
    except Exception as e:
        print('SQL error:', e)
        return None