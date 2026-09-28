import sqlite3

conn = sqlite3.connect("chat.db")

conn.execute("""
    INSERT INTO users (username, email)
    VALUES (?, ?)
""", ("zhangsan", "zhangsan@example.com"))

conn.commit()
conn.close()