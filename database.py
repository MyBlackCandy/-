import os
import psycopg2
from datetime import datetime, timedelta
import pytz

BKK_TZ = pytz.timezone('Asia/Bangkok')

def get_db_connection():
    url = os.getenv('DATABASE_URL').replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url, sslmode='require')

def init_db():
    """自动创建所有必要的数据库表"""
    conn = get_db_connection(); cursor = conn.cursor()
    tables = [
        "CREATE TABLE IF NOT EXISTS users (user_id BIGINT, chat_id BIGINT, username TEXT, full_name TEXT, salary DECIMAL DEFAULT 0, is_active BOOLEAN DEFAULT TRUE, PRIMARY KEY (user_id, chat_id))",
        "CREATE TABLE IF NOT EXISTS admins (user_id BIGINT PRIMARY KEY, expire_date TIMESTAMP, is_master BOOLEAN DEFAULT FALSE)",
        "CREATE TABLE IF NOT EXISTS chat_settings (chat_id BIGINT PRIMARY KEY, off_days TEXT DEFAULT 'Sunday', bonus_amount DECIMAL DEFAULT 0, toilet_limit INTEGER DEFAULT 15, smoke_limit INTEGER DEFAULT 10, work_hours TEXT DEFAULT '08:00-17:00')",
        "CREATE TABLE IF NOT EXISTS attendance (id SERIAL PRIMARY KEY, user_id BIGINT, chat_id BIGINT, check_in TIMESTAMP, check_out TIMESTAMP, late_mins INTEGER DEFAULT 0, work_date DATE DEFAULT CURRENT_DATE, UNIQUE(user_id, chat_id, work_date))",
        "CREATE TABLE IF NOT EXISTS activity_logs (id SERIAL PRIMARY KEY, user_id BIGINT, chat_id BIGINT, type TEXT, start_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, end_at TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS leave_requests (id SERIAL PRIMARY KEY, user_id BIGINT, chat_id BIGINT, leave_type TEXT, reason TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'APPROVED')"
    ]
    for table in tables: cursor.execute(table)
    conn.commit(); cursor.close(); conn.close()
    print("✅ Database Init Successful.")

def get_user_role(user_id, chat_id):
    master_id = os.getenv('ADMIN_ID')
    if str(user_id) == str(master_id): return "master"
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT expire_date FROM admins WHERE user_id = %s", (user_id,))
    res = cursor.fetchone()
    if res and res[0] > datetime.utcnow():
        cursor.close(); conn.close(); return "admin"
    cursor.execute("SELECT is_active FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
    res_user = cursor.fetchone()
    cursor.close(); conn.close()
    return "user" if (res_user and res_user[0]) else ("fired" if res_user else None)

# ฟังก์ชันเสริมอื่นๆ (is_off_day, get_monthly_stats, get_overtime_activities) ใส่ตามเดิม...
