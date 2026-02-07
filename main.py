import os
import pytz
import logging
from datetime import datetime, time, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# นำเข้าฟังก์ชันจากไฟล์ใหม่ db_manager.py
from db_manager import get_db_conn, fetch_user_role, fetch_monthly_stats, BKK_TZ, fetch_overtime_alerts, init_db_tables

# --- ⚙️ 系统配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
TOKEN = os.getenv('TOKEN')
MASTER_ID = os.getenv('ADMIN_ID')

# --- 📖 帮助菜单 (Command เป็นภาษาอังกฤษตามกฎ Telegram) ---
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = fetch_user_role(uid, cid)
    msg = "🍎 **黑糖果 HR & 薪酬管理系统手册**\n━━━━━━━━━━━━━━━━━━\n\n👤 **【员工指令】**\n1️⃣ **注册**: `/register [姓名]`\n2️⃣ **上班下班**: `/in` | `/out`\n3️⃣ **休息**: `/toilet` | `/smoke`\n4️⃣ **假/辞**: `/leave` | `/resign`\n5️⃣ **状态**: `/status`\n\n"
    if role in ['admin', 'master']:
        msg += "👮 **【管理员指令】**\n1️⃣ **考勤**: `/set_work` | `/set_off`\n2️⃣ **薪资**: `/set_salary` | `/set_bonus`\n3️⃣ **限时**: `/set_toilet` | `/set_smoke`\n4️⃣ **报表**: `/report_day` | `/report_month` | `/fire @user`\n\n"
    if role == 'master':
        msg += "👑 **【主管理员特权】**\n• `/setadmin @用户名 [天数]`\n\n"
    msg += "━━━━━━━━━━━━━━━━━━\n💡 提示: 请确保先使用 `/in` 开启今日工时。"
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- 👤 员工功能 ---
async def user_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    username = update.effective_user.username or str(uid)
    full_name = " ".join(context.args)
    if not full_name: return await update.message.reply_text("⚠️ 请输入姓名！例: `/register 张三`")
    conn = get_db_conn()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, chat_id, username, full_name) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, chat_id) DO UPDATE SET full_name = EXCLUDED.full_name, is_active = TRUE", (uid, cid, username, full_name))
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(f"✅ 注册成功！姓名: {full_name}")

async def user_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = fetch_user_role(uid, cid)
    if role == "fired": return await update.message.reply_text("🚫 您已被开除。")
    if not role: return await update.message.reply_text("❌ 请先 `/register`。")
    now = datetime.now(BKK_TZ).replace(second=0, microsecond=0)
    conn = get_db_conn()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("SELECT work_hours FROM chat_settings WHERE chat_id = %s", (cid,))
    res = cursor.fetchone()
    start_time_str = res[0].split(',')[0].split('-')[0] if res else "08:00"
    start_time = datetime.strptime(start_time_str, "%H:%M").time()
    late = 0
    if now.time() > start_time: late = (datetime.combine(now.date(), now.time()) - datetime.combine(now.date(), start_time)).total_seconds() // 60
    try:
        cursor.execute("INSERT INTO attendance (user_id, chat_id, check_in, late_mins, work_date) VALUES (%s, %s, %s, %s, %s)", (uid, cid, now, int(late), now.date()))
        conn.commit()
        await update.message.reply_text(f"✅ 上班签到成功\n⏰ 时间: {now.strftime('%H:%M')}\n⚠️ 迟到: {int(late)} 分钟")
    except: await update.message.reply_text("⚠️ 今日已签到。")
    finally: cursor.close(); conn.close()

async def break_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    if not fetch_user_role(uid, cid): return
    cmd = update.message.text
    act_type = 'toilet' if 'toilet' in cmd else 'smoke'
    now = datetime.now(BKK_TZ)
    conn = get_db_conn()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM attendance WHERE user_id = %s AND work_date = %s", (uid, now.date()))
    if not cursor.fetchone(): return await update.message.reply_text("⚠️ 请先签到 (/in) 后再操作。")
    cursor.execute("SELECT id FROM activity_logs WHERE user_id = %s AND type = %s AND end_at IS NULL", (uid, act_type))
    active_log = cursor.fetchone()
    if active_log:
        cursor.execute("UPDATE activity_logs SET end_at = %s WHERE id = %s", (now, active_log[0]))
        text = "✅ 休息计时结束。"
    else:
        cursor.execute("INSERT INTO activity_logs (user_id, chat_id, type, start_at) VALUES (%s, %s, %s, %s)", (uid, cid, act_type, now))
        text = "⏳ 休息开始计时..."
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(text)

async def check_overtime(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_conn()
    if not conn: return
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM chat_settings")
    chats = cursor.fetchall()
    for (cid,) in chats:
        overtimes = fetch_overtime_alerts(cid)
        for ot in overtimes:
            try: await context.bot.send_message(cid, f"🚨 **超时警告**\n👤 @{ot['username']} {ot['type']} 已超时 {ot['duration']} 分钟！")
            except: pass
    cursor.close(); conn.close()

def main():
    init_db_tables()
    app = Application.builder().token(TOKEN).build()
    
    # แก้ปัญหา AttributeError: 'NoneType'
    if app.job_queue:
        app.job_queue.run_repeating(check_overtime, interval=60, first=10)
    
    app.add_handler(CommandHandler(["help", "start"], help_cmd))
    app.add_handler(CommandHandler("register", user_register))
    app.add_handler(CommandHandler("in", user_in))
    app.add_handler(CommandHandler(["toilet", "smoke"], break_toggle))
    
    print("🚀 Black Candy HR System is starting...")
    app.run_polling()

if __name__ == '__main__':
    main()
