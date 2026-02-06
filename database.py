import os
import psycopg2
from datetime import datetime, timedelta
import pytz

# --- ⚙️ 基础配置 (Basic Configuration) ---
# ตั้งค่าโซนเวลาเป็นประเทศไทย (Asia/Bangkok) เพื่อความแม่นยำของเวลาเข้างาน
BKK_TZ = pytz.timezone('Asia/Bangkok')

def get_db_connection():
    """连接到 PostgreSQL 数据库 (Railway)"""
    try:
        # รับ DATABASE_URL จาก Environment Variable และปรับแต่งให้รองรับ PostgreSQL
        url = os.getenv('DATABASE_URL').replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url, sslmode='require')
    except Exception as e:
        print(f"❌ 数据库连接错误 (Database Error): {e}")
        return None

# --- 👮 权限与员工管理 (Permission & Staff Management) ---

def get_user_role(user_id, chat_id):
    """检查用户权限级别 (Master/Admin/User/Fired)"""
    master_id = os.getenv('ADMIN_ID')
    # 检查是否为主管理员 (Master)
    if str(user_id) == str(master_id):
        return "master"
    
    conn = get_db_connection()
    if not conn: return None
    cursor = conn.cursor()
    
    # 1. 检查管理员 (Admin) 权限及其有效期
    cursor.execute("SELECT expire_date FROM admins WHERE user_id = %s", (user_id,))
    res_admin = cursor.fetchone()
    if res_admin and res_admin[0] > datetime.utcnow():
        cursor.close(); conn.close()
        return "admin"
        
    # 2. 检查员工状态 (Active/Fired)
    cursor.execute("SELECT is_active, full_name FROM users WHERE user_id = %s AND chat_id = %s", (user_id, chat_id))
    res_user = cursor.fetchone()
    cursor.close(); conn.close()
    
    if res_user:
        # is_active 为 True 表示正常工作，False 表示已被开除 (Fired)
        return "user" if res_user[0] else "fired"
    return None

# --- 🗓️ 考勤与休假管理 (Attendance & Off-days) ---

def is_off_day(chat_id, target_date):
    """检查今天是否为该群组设置的休息日 (如: Sunday 或 2026-02-10)"""
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT off_days FROM chat_settings WHERE chat_id = %s", (chat_id,))
    res = cursor.fetchone()
    cursor.close(); conn.close()
    
    if not res or not res[0]: return False
    
    day_name = target_date.strftime('%A')  # 英文星期名 (例: Sunday)
    date_str = target_date.strftime('%Y-%m-%d') # 日期字符串
    off_list = [x.strip() for x in res[0].split(',')]
    
    return day_name in off_list or date_str in off_list

# --- 💰 薪资与统计管理 (Payroll & Stats) ---

def get_monthly_stats(user_id, chat_id, month, year):
    """获取员工月度统计：工作天数、迟到总分钟数、已批准的请假天数"""
    conn = get_db_connection(); cursor = conn.cursor()
    
    # 统计实际出勤天数和迟到时间
    cursor.execute("""
        SELECT COUNT(DISTINCT work_date), SUM(late_mins) 
        FROM attendance 
        WHERE user_id = %s AND chat_id = %s 
        AND EXTRACT(MONTH FROM work_date) = %s 
        AND EXTRACT(YEAR FROM work_date) = %s
    """, (user_id, chat_id, month, year))
    attend_res = cursor.fetchone()
    
    # 统计已批准的请假天数 (病假/事假)
    cursor.execute("""
        SELECT COUNT(*) FROM leave_requests 
        WHERE user_id = %s AND chat_id = %s AND status = 'APPROVED'
        AND EXTRACT(MONTH FROM timestamp) = %s 
        AND EXTRACT(YEAR FROM timestamp) = %s
    """, (user_id, chat_id, month, year))
    leave_res = cursor.fetchone()
    
    cursor.close(); conn.close()
    
    work_days = attend_res[0] if attend_res[0] else 0
    total_late = attend_res[1] if attend_res[1] else 0
    total_leaves = leave_res[0] if leave_res[0] else 0
    
    return work_days, total_late, total_leaves

# --- 🚽 休息监控管理 (Break Monitoring) ---

def get_overtime_activities(chat_id):
    """扫描当前正在休息且超时的员工 (洗手间/抽烟)"""
    conn = get_db_connection(); cursor = conn.cursor()
    
    # 获取群组的时限设置
    cursor.execute("SELECT toilet_limit, smoke_limit FROM chat_settings WHERE chat_id = %s", (chat_id,))
    limits = cursor.fetchone()
    if not limits: limits = (15, 10) # 默认：洗手间15分钟，抽烟10分钟
    
    # 查询尚未结束的休息活动 (end_at IS NULL)
    cursor.execute("""
        SELECT l.user_id, u.username, l.type, l.start_at 
        FROM activity_logs l
        JOIN users u ON l.user_id = u.user_id AND l.chat_id = u.chat_id
        WHERE l.chat_id = %s AND l.end_at IS NULL
    """, (chat_id,))
    active_logs = cursor.fetchall()
    
    overtime_list = []
    now = datetime.now(BKK_TZ)
    
    for uid, uname, act_type, start_at in active_logs:
        # 匹配限时
        limit = limits[0] if act_type == 'toilet' else limits[1]
        
        # 计算已休息时长 (分钟)
        duration = (now - start_at.astimezone(BKK_TZ)).total_seconds() / 60
        
        if duration > limit:
            overtime_list.append({
                'username': uname,
                'type': '洗手间' if act_type == 'toilet' else '抽烟',
                'duration': int(duration)
            })
            
    cursor.close(); conn.close()
    return overtime_list
