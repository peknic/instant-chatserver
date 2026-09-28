import sqlite3

DB_PATH = "chat.db"
USER_ID = 1

conn = sqlite3.connect(DB_PATH)

try:
    cursor = conn.cursor()

    # 先查看用户
    cursor.execute("SELECT * FROM users WHERE id = ?", (USER_ID,))
    user = cursor.fetchone()

    if user is None:
        print("用户不存在")
    else:
        print("准备删除用户：", user)

        # 删除用户
        cursor.execute("DELETE FROM users WHERE id = ?", (USER_ID,))

        # 提交修改
        conn.commit()

        print("用户删除成功")

except sqlite3.Error as e:
    conn.rollback()
    print("数据库操作失败：", e)

finally:
    conn.close()