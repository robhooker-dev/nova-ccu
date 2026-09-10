import sqlite3
conn = sqlite3.connect('data/nova_ccu.db')
cursor = conn.execute("UPDATE users SET role='supervisor' WHERE principal='ds.hooker@example.police.uk'")
print("rows affected:", cursor.rowcount)
conn.commit()
conn.close()