import os
import re
import pytz
import logging
import psycopg2
from datetime import datetime, time, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from database import get_db_connection, get_user_role, is_off_day, get_monthly_stats, BKK_TZ, get_overtime_activities
from database import init_db

# --- ⚙️ 系统配置 (System Configuration) ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
TOKEN = os.getenv('TOKEN')
MASTER_ID = os.getenv('ADMIN_ID')

# --- 📖 帮助菜单 (Detailed Chinese Help) ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    role = get_user_role(uid, cid)
    
    msg = "🍎 **黑糖果 HR & 薪酬管理系统手册**\n"
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    
    # 👤 员工部分 (User Section)
    msg += "👤 **【员工指令 - 使用指南】**\n"
    msg += "1️⃣ **入职注册**\n"
    msg += "   • `/注册 [姓名]`\n"
    msg += "   • *示例：`/注册 张三`*\n"
    msg += "2️⃣ **上下班签到**\n"
    msg += "   • `/上班` (签到) | `/下班` (签退)\n"
    msg += "   • *说明：系统自动抹除秒数，计算迟到时间。*\n"
    msg += "3️⃣ **休息计时 (需先签到)**\n"
    msg += "   • `/洗手间` | `/抽烟`\n"
    msg += "   • *示例：去的时候发一次开始，回来再发一次结束。*\n"
    msg += "4️⃣ **请假与离职**\n"
    msg += "   • `/请假 [病假/事假] [原因]`\n"
    msg += "   • *示例：`/请假 病假 发烧感冒`*\n"
    msg += "   • `/辞职` (申请离职)\n"
    msg += "5️⃣ **查询状态**\n"
    msg += "   • `/状态` (查看个人考勤统计)\n\n"
    
    # 👮 管理员部分 (Admin Section)
    if role in ['admin', 'master']:
        msg += "👮 **【管理员指令 - 考勤薪资】**\n"
        msg += "1️⃣ **考勤设置**\n"
        msg += "   • `/设置工时 [时间段]`\n"
        msg += "   • *例：`/设置工时 08:00-12:00,13:00-17:00`*\n"
        msg += "   • `/设置休息日 [日期]`\n"
        msg += "   • *例：`/设置休息日 Sunday`*\n"
        msg += "2️⃣ **休息限时 (超时自动提醒)**\n"
        msg += "   • `/设置洗手间时限 [分钟]`\n"
        msg += "   • `/设置抽烟时限 [分钟]`\n"
        msg += "3️⃣ **薪资与全勤奖**\n"
        msg += "   • `/设置薪资 [@用户名] [金额]`\n"
        msg += "   • `/设置全勤奖 [金额]`\n"
        msg += "4️⃣ **报表与人事**\n"
        msg += "   • `/当日报表` (今日全员考勤)\n"
        msg += "   • `/月度结算` (本月工资单)\n"
        msg += "   • `/开除 [@用户名]` (封禁用户)\n\n"
        
    if role == 'master':
        msg += "👑 **【主管理员特权】**\n"
        msg += "   • `/设置管理员 [@用户名] [天数]`\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n"
    msg += "💡 **提示**：输入 `/帮助` 可随时查看此手册。"
    
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
    if role == "fired": return await update.message.reply_text("🚫 您已被开除。")
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
    except: await update.message.reply_text("⚠️ 今日已签到。")
    finally: cursor.close(); conn.close()

async def work_out(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    now = datetime.now(BKK_TZ)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE attendance SET check_out = %s WHERE user_id = %s AND chat_id = %s AND work_date = %s", (now, uid, cid, now.date()))
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text("🏁 下班签退成功！")

async def activity_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    if not get_user_role(uid, cid): return
    cmd = update.message.text.lower()
    act_type = 'toilet' if '洗手间' in cmd else 'smoke'
    now = datetime.now(BKK_TZ)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id FROM attendance WHERE user_id = %s AND work_date = %s", (uid, now.date()))
    if not cursor.fetchone(): return await update.message.reply_text("⚠️ 请先签到 (/上班) 再操作。")
    cursor.execute("SELECT id FROM activity_logs WHERE user_id = %s AND type = %s AND end_at IS NULL", (uid, act_type))
    active_log = cursor.fetchone()
    if active_log:
        cursor.execute("UPDATE activity_logs SET end_at = %s WHERE id = %s", (now, active_log[0]))
        text = f"✅ {'洗手间' if act_type=='toilet' else '抽烟'} 结束"
    else:
        cursor.execute("INSERT INTO activity_logs (user_id, chat_id, type, start_at) VALUES (%s, %s, %s, %s)", (uid, cid, act_type, now))
        text = f"⏳ {'洗手间' if act_type=='toilet' else '抽烟'} 开始计时..."
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(text)

async def leave_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid, cid = update.effective_user.id, update.effective_chat.id
    if len(context.args) < 2: return await update.message.reply_text("⚠️ 用法：`/请假 [病假/事假] [原因]`")
    l_type, reason = context.args[0], " ".join(context.args[1:])
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO leave_requests (user_id, chat_id, leave_type, reason) VALUES (%s, %s, %s, %s)", (uid, cid, l_type, reason))
    conn.commit(); cursor.close(); conn.close()
    await update.message.reply_text(f"✅ 请假申请已提交: {l_type}")

# --- 👮 管理员功能 (Admin Features) ---

async def set_salary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_role(update.effective_user.id, update.effective_chat.id) not in ['admin', 'master']: return
    try:
        uname, amt = context.args[0].replace('@', ''), float(context.args[1])
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE users SET salary = %s WHERE username = %s AND chat_id = %s", (amt, uname, update.effective_chat.id))
        conn.commit(); cursor.close(); conn.close()
        await update.message.reply_text(f"✅ 已设置 @{uname} 底薪: {amt:,.2f}")
    except: await update.message.reply_text("⚠️ 用法：`/设置薪资 @用户名 金额`")

async def report_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_user_role(update.effective_user.id, update.effective_chat.id) not in ['admin', 'master']: return
    cid, now = update.effective_chat.id, datetime.now(BKK_TZ)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, salary FROM users WHERE chat_id = %s AND is_active = TRUE", (cid,))
    staff = cursor.fetchall()
    cursor.execute("SELECT bonus_amount FROM chat_settings WHERE chat_id = %s", (cid,))
    bonus_cfg = cursor.fetchone()[0] if cursor.rowcount > 0 else 0
    msg = f"📅 **{now.month}月工资结算单**\n━━━━━━━━━━━━━━━\n"
    for uid, uname, fname, salary in staff:
        work_days, late, leaves = get_monthly_stats(uid, cid, now.month, now.year)
        has_bonus = (late == 0 and leaves == 0 and work_days > 0)
        final = float(salary) + (bonus_cfg if has_bonus else 0)
        msg += f"👤 {fname} (@{uname})\n  • 出勤:{work_days} | 迟到:{late} | 请假:{leaves}\n  • 奖金:{'✅' if has_bonus else '❌'} | **实发:{final:,.2f}**\n----------------\n"
    cursor.close(); conn.close()
    await update.message.reply_text(msg, parse_mode='Markdown')

# --- 👑 主管理员 (Master Features) ---

async def set_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(MASTER_ID): return
    try:
        uname, days = context.args[0].replace('@', ''), int(context.args[1])
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users WHERE username = %s", (uname,))
        uid = cursor.fetchone()[0]
        exp = datetime.utcnow() + timedelta(days=days)
        cursor.execute("INSERT INTO admins (user_id, expire_date) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET expire_date = EXCLUDED.expire_date", (uid, exp))
        conn.commit(); cursor.close(); conn.close()
        await update.message.reply_text(f"✅ 管理员授权成功: @{uname} ({days}天)")
    except: await update.message.reply_text("⚠️ 用法：`/设置管理员 @用户名 天数`")

# --- ⏲️ 自动监控 ---
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

# --- 🚀 Main ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    
    # 指令注册 (Chinese Commands)
    app.add_handler(CommandHandler(["帮助", "start"], help_command))
    app.add_handler(CommandHandler("注册", register))
    app.add_handler(CommandHandler("上班", work_in))
    app.add_handler(CommandHandler("下班", work_out))
    app.add_handler(CommandHandler("洗手间", activity_toggle))
    app.add_handler(CommandHandler("抽烟", activity_toggle))
    app.add_handler(CommandHandler("请假", leave_request))
    app.add_handler(CommandHandler("设置薪资", set_salary))
    app.add_handler(CommandHandler("月度结算", report_month))
    app.add_handler(CommandHandler("设置管理员", set_admin))
    
    # 后台 Job
    app.job_queue.run_repeating(monitor_overtime, interval=60, first=10)
    
    print("🚀 Black Candy HR System Online (Chinese Commands)...")
    app.run_polling()

if __name__ == '__main__':
    main()
