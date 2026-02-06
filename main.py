import os
import re
import pytz
import logging
from datetime import datetime, time, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from database import get_db_connection, get_user_role, is_off_day, get_monthly_stats, BKK_TZ, get_overtime_activities, init_db

# --- ⚙️ 系统配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
TOKEN = os.getenv('TOKEN')
MASTER_ID = os.getenv('ADMIN_ID')

# --- 📖 帮助菜单 (Detailed Help) ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = get_user_role(uid, cid)
    msg = "🍎 **黑糖果 HR & 薪酬管理系统手册**\n━━━━━━━━━━━━━━━━━━\n\n👤 **【员工指令】**\n1️⃣ **入职注册**: `/注册 [姓名]`\n2️⃣ **上班下班**: `/上班` | `/下班`\n3️⃣ **休息计时**: `/洗手间` | `/抽烟`\n4️⃣ **请假辞职**: `/请假 [类型] [原因]` | `/辞职`\n5️⃣ **状态查询**: `/状态`\n\n"
    if role in ['admin', 'master']:
        msg += "👮 **【管理员指令】**\n1️⃣ **考勤设置**: `/设置工时 08:00-17:00` | `/设置休息日 Sunday`\n2️⃣ **休息限时**: `/设置洗手间时限 15` | `/设置抽烟时限 10`\n3️⃣ **薪资设置**: `/设置薪资 @user 30000` | `/设置全勤奖 3000`\n4️⃣ **报表管理**: `/当日报表` | `/月度结算` | `/开除 @user`\n\n"
    if role == 'master':
        msg += "👑 **【主管理员特权】**\n• `/设置管理员 @用户名 [天数]`\n\n"
    msg += "━━━━━━━━━━━━━━━━━━\n💡 提示: 必须先 `/上班` 才能使用休息计时功能。"
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- 👤 员工功能 ---
async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    username = update.effective_user.username or str(uid)
    full_name = " ".join(context.args)
    if not full_name: return await update.message.reply_text("⚠️ 请输入姓名！例: `/注册 张三`")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, chat_id, username, full_name) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, chat_id) DO UPDATE SET full_name = EXCLUDED.full_name, is_active = TRUE", (uid, cid, username, full_name))
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(f"✅ 注册成功！姓名: {full_name}")

async def work_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = get_user_role(uid, cid)
    if role == "fired": return await update.message.reply_text("🚫 您已被开除。")
    if not role: return await update.message.reply_text("❌ 请先 `/注册`。")
    now = datetime.now(BKK_TZ).replace(second=0, microsecond=0)
    conn = get_db_connection(); cursor = conn.cursor()
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

async def work_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    now = datetime.now(BKK_TZ)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE attendance SET check_out = %s WHERE user_id = %s AND chat_id = %s AND work_date = %s", (now, uid, cid, now.date()))
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text("🏁 下班签退成功！辛苦了。")

async def activity_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    if not get_user_role(uid, cid): return
    cmd = update.message.text
    act_type = 'toilet' if '洗手间' in cmd else 'smoke'
    now = datetime.now(BKK_TZ)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id FROM attendance WHERE user_id = %s AND work_date = %s", (uid, now.date()))
    if not cursor.fetchone(): return await update.message.reply_text("⚠️ 请先签到 (/上班) 后再操作。")
    cursor.execute("SELECT id FROM activity_logs WHERE user_id = %s AND type = %s AND end_at IS NULL", (uid, act_type))
    active_log = cursor.fetchone()
    if active_log:
        cursor.execute("UPDATE activity_logs SET end_at = %s WHERE id = %s", (now, active_log[0]))
        text = f"✅ {'洗手间' if act_type=='toilet' else '抽烟'} 结束。"
    else:
        cursor.execute("INSERT INTO activity_logs (user_id, chat_id, type, start_at) VALUES (%s, %s, %s, %s)", (uid, cid, act_type, now))
        text = f"⏳ {'洗手间' if act_type=='toilet' else '抽烟'} 开始计时，请勿超时。"
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(text)

async def monitor_overtime(context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM chat_settings")
    chats = cursor.fetchall()
    for (cid,) in chats:
        overtimes = get_overtime_activities(cid)
        for ot in overtimes:
            try: await context.bot.send_message(cid, f"🚨 **超时警告**\n👤 @{ot['username']} {ot['type']} 已超时 {ot['duration']} 分钟！")
            except: pass
    cursor.close(); conn.close()

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["帮助", "start"], help_command))
    app.add_handler(CommandHandler("注册", register))
    app.add_handler(CommandHandler("上班", work_in))
    app.add_handler(CommandHandler("下班", work_out))
    app.add_handler(CommandHandler(["洗手间", "抽烟"], activity_toggle))
    # ตั้งค่าให้ตรวจสอบคนพักเกินเวลาทุกๆ 1 นาที
    app.job_queue.run_repeating(monitor_overtime, interval=60, first=10)
    print("🚀 Black Candy HR System Online (Chinese Commands)...")
    app.run_polling()

if __name__ == '__main__': main()
