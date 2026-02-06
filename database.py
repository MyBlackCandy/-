import os
import psycopg2
from datetime import datetime, timedelta
import pytz

# --- ⚙️ 基础配置 (Basic Configuration) ---
BKK_TZ = pytz.timezone('Asia/Bangkok')

def get_db_connection():
    """连接到 PostgreSQL 数据库 (Railway)"""
    try:
        url = os.getenv('DATABASE_URL').replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url, sslmode='require')
    except Exception as e:
        print(f"❌ 数据库连接错误: {e}")
        return None

def init_db():
    """自动创建数据库表 (Auto-create tables)"""
    conn = get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    
    # SQL สำหรับสร้างทุกตารางที่จำเป็น
    sql_commands = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT,
            chat_id BIGINT,
            username TEXT,
            full_name TEXT,
            salary DECIMAL(12, 2) DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (user_id, chat_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
            expire_date TIMESTAMP,
            is_master BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id BIGINT PRIMARY KEY,
            off_days TEXT DEFAULT 'Sunday', 
            bonus_amount DECIMAL(12, 2) DEFAULT 0,
            toilet_limit INTEGER DEFAULT 15,
            smoke_limit INTEGER DEFAULT 10,
            work_hours TEXT DEFAULT '08:00-17:00'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            check_in TIMESTAMP,
            check_out TIMESTAMP,
            late_mins INTEGER DEFAULT 0,
            work_date DATE DEFAULT CURRENT_DATE,
            UNIQUE(user_id, chat_id, work_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            type TEXT,
            start_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS leave_requests (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            leave_type TEXT,
            reason TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'APPROVED'
        )
        """
    ]
    
    try:
        for command in sql_commands:
            cursor.execute(command)
        conn.commit()
        print("✅ 数据库表初始化完成 (Database tables initialized)")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
    finally:
        cursor.close()
        conn.close()

# --- 👮 权限与员工管理 ---
def get_user_role(user_id, chat_id):
    master_id = os.getenv('ADMIN_ID')
    if str(user_id) == str(master_id): return "master"
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT expire_date FROM admins WHERE user_id = %s", (user_id,))
    res_admin = cursor.fetchone()
    if res_admin and res_admin[0] > datetime.utcnow():
        cursor.close(); conn.close(); return "admin"
    cursor.execute("SELECT is_active, full_name FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
    res_user = cursor.fetchone()
    cursor.close(); conn.close()
    if res_user: return "user" if res_user[0] else "fired"
    return None

# --- 🗓️ 考勤与休假管理 ---
def is_off_day(chat_id, target_date):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT off_days FROM chat_settings WHERE chat_id = %s", (chat_id,))
    res = cursor.fetchone()
    cursor.close(); conn.close()
    if not res or not res[0]: return False
    day_name, date_str = target_date.strftime('%A'), target_date.strftime('%Y-%m-%d')
    off_list = [x.strip() for x in res[0].split(',')]
    return day_name in off_list or date_str in off_list

# --- 💰 薪资与统计管理 ---
def get_monthly_stats(user_id, chat_id, month, year):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT work_date), SUM(late_mins) FROM attendance 
        WHERE user_id = %s AND chat_id = %s AND EXTRACT(MONTH FROM work_date) = %s AND EXTRACT(YEAR FROM work_date) = %s
    """, (user_id, chat_id, month, year))
    attend_res = cursor.fetchone()
    cursor.execute("""
        SELECT COUNT(*) FROM leave_requests WHERE user_id = %s AND chat_id = %s AND status = 'APPROVED'
        AND EXTRACT(MONTH FROM timestamp) = %s AND EXTRACT(YEAR FROM timestamp) = %s
    """, (user_id, chat_id, month, year))
    leave_res = cursor.fetchone()
    cursor.close(); conn.close()
    return (attend_res[0] or 0), (attend_res[1] or 0), (leave_res[0] or 0)

# --- 🚽 休息监控管理 ---
def get_overtime_activities(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT toilet_limit, smoke_limit FROM chat_settings WHERE chat_id = %s", (chat_id,))
    limits = cursor.fetchone() or (15, 10)
    cursor.execute("""
        SELECT l.user_id, u.username, l.type, l.start_at FROM activity_logs l
        JOIN users u ON l.user_id = u.user_id AND l.chat_id = u.chat_id
        WHERE l.chat_id = %s AND l.end_at IS NULL
    """, (chat_id,))
    active_logs = cursor.fetchall()
    overtime_list = []
    now = datetime.now(BKK_TZ)
    for uid, uname, act_type, start_at in active_logs:
        limit = limits[0] if act_type == 'toilet' else limits[1]
        duration = (now - start_at.astimezone(BKK_TZ)).total_seconds() / 60
        if duration > limit:
            overtime_list.append({'username': uname, 'type': '洗手间' if act_type == 'toilet' else '抽烟', 'duration': int(duration)})
    cursor.close(); conn.close()
    return overtime_list
