import sqlite3
conn = sqlite3.connect('data/nova_ccu.db')
rows = conn.execute("SELECT principal, role FROM users").fetchall()
for row in rows:
    print(row)
    