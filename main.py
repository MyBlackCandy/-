import os
import re
import pytz
import logging
import psycopg2
from datetime import datetime, time, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
# นำเข้าฟังก์ชันจาก database.py
from database import get_db_connection, get_user_role, is_off_day, get_monthly_stats, BKK_TZ, get_overtime_activities, init_db

# --- ⚙️ 系统配置 (System Configuration) ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
TOKEN = os.getenv('TOKEN')
MASTER_ID = os.getenv('ADMIN_ID')

# --- 📖 帮助菜单 (Comprehensive Chinese Help) ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = get_user_role(uid, cid)
    
    msg = "🍎 **黑糖果 HR & 薪酬管理系统手册**\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    msg += "👤 **【员工指令 - 使用指南】**\n"
    msg += "1️⃣ **入职注册**: `/注册 [姓名]` (例: `/注册 张三`)\n"
    msg += "2️⃣ **上下班签到**: `/上班` (签到) | `/下班` (签退)\n"
    msg += "3️⃣ **休息计时**: `/洗手间` | `/抽烟` (去时发一次，回时再发一次)\n"
    msg += "4️⃣ **请假离职**: `/请假 [类型] [原因]` | `/辞职` (申请)\n"
    msg += "5️⃣ **查询状态**: `/状态` (查看今日统计)\n\n"
    
    if role in ['admin', 'master']:
        msg += "👮 **【管理员指令 - 考勤薪资】**\n"
        msg += "1️⃣ **时间设置**: `/设置工时 08:00-17:00` | `/设置休息日 Sunday`\n"
        msg += "2️⃣ **休息限时**: `/设置洗手间时限 15` | `/设置抽烟时限 10`\n"
        msg += "3️⃣ **薪资结算**: `/设置薪资 @user 30000` | `/设置全勤奖 3000`\n"
        msg += "4️⃣ **管理报表**: `/当日报表` | `/月度结算` | `/开除 @user`\n\n"
        
    if role == 'master':
        msg += "👑 **【主管理员特权】**\n"
        msg += "• `/设置管理员 @用户名 [天数]`\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += "💡 **提示**: 必须先 `/上班` 才能使用休息计时功能。"
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- 👤 员工功能 (Employee Features) ---

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    username = update.effective_user.username or str(uid)
    full_name = " ".join(context.args)
    if not full_name:
        return await update.message.reply_text("⚠️ 请输入姓名！例: `/注册 张三`")
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, chat_id, username, full_name) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, chat_id) DO UPDATE SET full_name = EXCLUDED.full_name, is_active = TRUE", (uid, cid, username, full_name))
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(f"✅ 注册成功！姓名: {full_name}")

async def work_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = get_user_role(uid, cid)
    if role == "fired": return await update.message.reply_text("🚫 您已被开除，无法使用系统。")
    if not role: return await update.message.reply_text("❌ 请先使用 `/注册` 姓名。")
    now = datetime.now(BKK_TZ).replace(second=0, microsecond=0)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT work_hours FROM chat_settings WHERE chat_id = %s", (cid,))
    res = cursor.fetchone()
    start_time_str = res[0].split(',')[0].split('-')[0] if res else "08:00"
    start_time = datetime.strptime(start_time_str, "%H:%M").time()
    late = 0
    if now.time() > start_time:
        late = (datetime.combine(now.date(), now.time()) - datetime.combine(now.date(), start_time)).total_seconds() // 60
    try:
        cursor.execute("INSERT INTO attendance (user_id, chat_id, check_in, late_mins, work_date) VALUES (%s, %s, %s, %s, %s)", (uid, cid, now, int(late), now.date()))
        conn.commit()
        await update.message.reply_text(f"✅ 上班签到成功\n⏰ 时间: {now.strftime('%H:%M')}\n⚠️ 迟到: {int(late)} 分钟")
    except: await update.message.reply_text("⚠️ 今日已完成签到。")
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
    if not cursor.fetchone(): return await update.message.reply_text("⚠️ 请先签到 (/上班) 后再进行计时。")
    cursor.execute("SELECT id FROM activity_logs WHERE user_id = %s AND type = %s AND end_at IS NULL", (uid, act_type))
    active_log = cursor.fetchone()
    if active_log:
        cursor.execute("UPDATE activity_logs SET end_at = %s WHERE id = %s", (now, active_log[0]))
        text = f"✅ {'洗手间' if act_type=='toilet' else '抽烟'} 计时结束。"
    else:
        cursor.execute("INSERT INTO activity_logs (user_id, chat_id, type, start_at) VALUES (%s, %s, %s, %s)", (uid, cid, act_type, now))
        text = f"⏳ {'洗手间' if act_type=='toilet' else '抽烟'} 开始计时，请勿超时。"
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(text)

# --- 🚀 Main Entry (With Auto-Init) ---
def main():
    # 自动创建并初始化数据库表
    init_db()
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler(["帮助", "start"], help_command))
    app.add_handler(CommandHandler("注册", register))
    app.add_handler(CommandHandler("上班", work_in))
    app.add_handler(CommandHandler("下班", work_out))
    app.add_handler(CommandHandler(["洗手间", "抽烟"], activity_toggle))
    
    # 按照相同模式添加其他 Handler (请假、设置薪资等)...
    
    print("🚀 Black Candy HR System Online (Auto-Init Active)...")
    app.run_polling()

if __name__ == '__main__':
    main()
