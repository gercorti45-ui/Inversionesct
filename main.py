#!/usr/bin/env python3
# completofinal1.py
# InversionesCT - versión con:
# - OCR para comprobantes
# - Reglas: todas las inversiones requieren comprobante; después de la primera
#   inversión se exige traer un referido nuevo que también haya invertido.
# - Perfil editable
# - Panel admin para aprobar/rechazar inversiones
# - Flask keep-alive + /download-db
# - /dumpdb (solo admin) y backup diario

import os
import time
import sqlite3
import datetime
import traceback
import zipfile
import threading
import re
from io import BytesIO

from telebot import TeleBot, types

# OCR libs
try:
    from PIL import Image
    import pytesseract
    TESSERACT_AVAILABLE = True
except Exception:
    TESSERACT_AVAILABLE = False

# Flask
from flask import Flask, send_file, request, abort

# ---------------- CONFIG ----------------
# Preferir variables de entorno (Replit)
TOKEN = os.environ.get("BOT_TOKEN") or "8362936227:AAHlr3AY5iUDdIk8oFoK63wxT6bsgrYYfDk"   # reemplazar en local o usar env var
BOT_USERNAME = os.environ.get("BOT_USERNAME", "InversionesCT_bot")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "5871502663"))  # por defecto
NEQUI_DESTINO = os.environ.get("NEQUI_DESTINO", "3053706109")

DB_FILE = os.path.join(os.getcwd(), "inversionesct.db")
DOWNLOAD_DIR = os.path.join(os.getcwd(), "comprobantes")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

bot = TeleBot(TOKEN, parse_mode=None)

# ---------------- DB helpers ----------------
def get_conn():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        user_id INTEGER PRIMARY KEY,
        nombre TEXT,
        telefono TEXT,
        nequi TEXT,
        cedula TEXT,
        referido_por INTEGER,
        referidos INTEGER DEFAULT 0,
        total_invertido INTEGER DEFAULT 0,
        ganancia_total INTEGER DEFAULT 0
    );
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS inversiones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        monto INTEGER,
        fecha_inversion TEXT,
        fecha_pago TEXT,
        estado TEXT,
        comprobante_path TEXT,
        ocr_text TEXT
    );
    ''')
    conn.commit(); conn.close()

init_db()

# ---------------- Utilities ----------------
def fmt_money(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except:
        return str(n)

def safe_send(chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id, text, **kwargs)
    except Exception:
        try:
            bot.send_message(chat_id, text)
        except Exception:
            pass

def iso_today():
    return datetime.date.today().isoformat()  # YYYY-MM-DD

def parse_date_iso(s):
    try:
        return datetime.date.fromisoformat(s)
    except:
        return None

# ---------------- Menú ----------------
def menu_principal_for(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id == ADMIN_ID:
        markup.add(types.KeyboardButton("📈 Panel admin"), types.KeyboardButton("💰 Invertir"))
        markup.add(types.KeyboardButton("🤝 Referir amigos"), types.KeyboardButton("📊 Mi perfil"))
    else:
        markup.add(types.KeyboardButton("💰 Invertir"), types.KeyboardButton("🤝 Referir amigos"))
        markup.add(types.KeyboardButton("📊 Mi perfil"), types.KeyboardButton("👥 Mis referidos"))
    return markup

# ---------------- START / REGISTRO ----------------
@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        parts = message.text.split()
        referido = None
        if len(parts) > 1:
            try:
                referido = int(parts[1])
            except:
                referido = None

        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM usuarios WHERE user_id=?", (user_id,))
        exists = cur.fetchone()
        cur.execute("INSERT OR IGNORE INTO usuarios (user_id, referido_por) VALUES (?, ?)", (user_id, referido))
        conn.commit()

        if referido and referido != user_id:
            try:
                # sumar referidos al referer una sola vez:
                cur.execute("UPDATE usuarios SET referidos = referidos + 1 WHERE user_id=?", (referido,))
                conn.commit()
                try:
                    safe_send(referido, f"🎉 Nuevo usuario registrado gracias a tu enlace: ID {user_id}")
                except:
                    pass
            except Exception:
                pass

        conn.close()

        if exists:
            safe_send(chat_id, "👋 Bienvenido de nuevo. Mostrando menú principal.", reply_markup=menu_principal_for(user_id))
        else:
            safe_send(chat_id, "👋 Bienvenido a *InversionesCT* 💰\nPor favor escribe tu nombre completo:", parse_mode="Markdown")
            bot.register_next_step_handler_by_chat_id(chat_id, step_nombre)
    except Exception:
        traceback.print_exc()

def step_nombre(message):
    try:
        user_id = message.from_user.id
        nombre = message.text.strip()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("UPDATE usuarios SET nombre=? WHERE user_id=?", (nombre, user_id))
        conn.commit(); conn.close()
        safe_send(user_id, "📱 Ingresa tu número de teléfono:")
        bot.register_next_step_handler_by_chat_id(user_id, step_telefono)
    except Exception:
        traceback.print_exc()

def step_telefono(message):
    try:
        user_id = message.from_user.id
        telefono = message.text.strip()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("UPDATE usuarios SET telefono=? WHERE user_id=?", (telefono, user_id))
        conn.commit(); conn.close()
        safe_send(user_id, "🪪 Ingresa tu número de cédula:")
        bot.register_next_step_handler_by_chat_id(user_id, step_cedula)
    except Exception:
        traceback.print_exc()

def step_cedula(message):
    try:
        user_id = message.from_user.id
        cedula = message.text.strip()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("UPDATE usuarios SET cedula=? WHERE user_id=?", (cedula, user_id))
        conn.commit(); conn.close()
        safe_send(user_id, "💳 Ingresa tu número de Nequi:")
        bot.register_next_step_handler_by_chat_id(user_id, step_nequi)
    except Exception:
        traceback.print_exc()

def step_nequi(message):
    try:
        user_id = message.from_user.id
        nequi = message.text.strip()
        conn = get_conn(); cur = conn.cursor()
        cur.execute("UPDATE usuarios SET nequi=? WHERE user_id=?", (nequi, user_id))
        conn.commit(); conn.close()
        safe_send(user_id, "✅ Registro completado. Aquí tienes el menú principal.", reply_markup=menu_principal_for(user_id))
    except Exception:
        traceback.print_exc()

# ---------------- Referidos ----------------
@bot.message_handler(func=lambda m: m.text == "🤝 Referir amigos")
def handler_referir(m):
    user_id = m.from_user.id
    bot_name = BOT_USERNAME
    referral_link = f"https://t.me/{bot_name}?start={user_id}"
    safe_send(user_id, "✨ Comparte tu enlace con tus amigos.")
    safe_send(user_id, f"🔗 Tu enlace personal:\n{referral_link}")
    safe_send(user_id, "Cada persona que se registre desde tu enlace quedará asociada a ti.")

# ---------------- Perfil y actualización ----------------
_pending_updates = {}

@bot.message_handler(func=lambda m: m.text == "📊 Mi perfil")
def handler_perfil(m):
    try:
        user_id = m.from_user.id
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT nombre, telefono, nequi, cedula, total_invertido, ganancia_total, referidos FROM usuarios WHERE user_id=?", (user_id,))
        r = cur.fetchone(); conn.close()
        if not r:
            safe_send(user_id, "⚠️ No estás registrado. Usa /start para registrarte.")
            return
        nombre, telefono, nequi, cedula, total_invertido, ganancia_total, referidos = r
        text = (
            f"👤 *Tu Perfil*\n\n"
            f"Nombre: {nombre}\n"
            f"Teléfono: {telefono}\n"
            f"Nequi: {nequi}\n"
            f"Cédula: {cedula}\n"
            f"Total invertido: ${fmt_money(total_invertido)}\n"
            f"Ganancia acumulada: ${fmt_money(ganancia_total)}\n"
            f"Referidos: {referidos}"
        )
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton("✏️ Actualizar datos"), types.KeyboardButton("🔙 Volver al menú"))
        safe_send(user_id, text, parse_mode="Markdown")
        safe_send(user_id, "¿Deseas actualizar algún dato?", reply_markup=markup)
    except Exception:
        traceback.print_exc()

@bot.message_handler(func=lambda m: m.text == "🔙 Volver al menú")
def volver_menu(m):
    safe_send(m.chat.id, "Volviendo al menú principal...", reply_markup=menu_principal_for(m.from_user.id))

@bot.message_handler(func=lambda m: m.text == "✏️ Actualizar datos")
def iniciar_actualizar(m):
    uid = m.from_user.id
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📛 Nombre", callback_data="UPD|nombre"))
    kb.add(types.InlineKeyboardButton("📱 Teléfono", callback_data="UPD|telefono"))
    kb.add(types.InlineKeyboardButton("🪪 Cédula", callback_data="UPD|cedula"))
    kb.add(types.InlineKeyboardButton("💳 Nequi", callback_data="UPD|nequi"))
    bot.send_message(uid, "Selecciona el dato que deseas actualizar:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("UPD|"))
def callback_update_field(c):
    try:
        uid = c.from_user.id
        _, field = c.data.split("|", 1)
        _pending_updates[uid] = field
        bot.answer_callback_query(c.id, "Perfecto — escribe el nuevo valor ahora.")
        bot.send_message(uid, f"✏️ Ingresa el nuevo valor para *{field.upper()}*:", parse_mode="Markdown")
        bot.register_next_step_handler_by_chat_id(uid, procesar_update_valor)
    except Exception:
        traceback.print_exc()

def procesar_update_valor(message):
    try:
        uid = message.from_user.id
        if uid not in _pending_updates:
            bot.send_message(uid, "No se detectó ninguna actualización pendiente. Vuelve a seleccionar el campo.")
            return
        field = _pending_updates.pop(uid)
        nuevo = message.text.strip()
        if field == "telefono":
            nuevo = nuevo.replace(" ", "").replace("-", "")
        if field == "cedula":
            nuevo = nuevo.replace(" ", "")
        conn = get_conn(); cur = conn.cursor()
        if field in ("nombre", "telefono", "cedula", "nequi"):
            cur.execute(f"UPDATE usuarios SET {field}=? WHERE user_id=?", (nuevo, uid))
            conn.commit()
            conn.close()
            bot.send_message(uid, f"✅ {field.capitalize()} actualizado correctamente.", reply_markup=menu_principal_for(uid))
        else:
            conn.close()
            bot.send_message(uid, "Campo no válido.")
    except Exception:
        traceback.print_exc()
        try:
            bot.send_message(uid, "Error actualizando datos.")
        except:
            pass

# ---------------- Mis referidos ----------------
@bot.message_handler(func=lambda m: m.text == "👥 Mis referidos")
def handler_mis_referidos(m):
    try:
        user_id = m.from_user.id
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT referidos FROM usuarios WHERE user_id=?", (user_id,))
        r = cur.fetchone(); conn.close()
        referidos = r[0] if r else 0
        safe_send(user_id, f"👥 Has referido a {referidos} persona(s).")
    except Exception:
        traceback.print_exc()

# ---------------- Inversiones (reglas avanzadas) ----------------
INV_OPTIONS = [100000, 300000, 500000]

@bot.message_handler(func=lambda m: m.text == "💰 Invertir")
def handler_invertir(m):
    try:
        markup = types.InlineKeyboardMarkup(row_width=3)
        for amt in INV_OPTIONS:
            btn = types.InlineKeyboardButton(f"💵 {fmt_money(amt)}", callback_data=f"INV|{amt}")
            markup.add(btn)
        safe_send(m.chat.id, "Selecciona el monto a invertir:", reply_markup=markup)
    except Exception:
        traceback.print_exc()

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("INV|"))
def callback_inv(c):
    try:
        parts = c.data.split("|")
        monto = int(parts[1])
        uid = c.from_user.id
        # Antes de aceptar comprobante, verificamos la regla:
        if not can_user_invest(uid):
            safe_send(uid, "❌ Para seguir invirtiendo, debes invitar a un nuevo usuario con tu enlace y asegurarte de que también realice su primera inversión. Vuelve cuando cumplas ese requisito.")
            return
        safe_send(uid, f"📸 Envía la imagen del comprobante Nequi por el valor de ${fmt_money(monto)} al número {NEQUI_DESTINO}.")
        bot.register_next_step_handler_by_chat_id(uid, lambda m: procesar_comprobante(m, monto))
    except Exception:
        traceback.print_exc()

def can_user_invest(user_id):
    """
    Reglas:
    - Si el usuario NO tiene inversiones previas -> puede invertir (primera inversión).
    - Si ya tiene al menos 1 inversión -> requiere que exista al menos un referido
      que se haya registrado tras la última inversión del usuario y que ese referido
      tenga al menos 1 inversión (estado 'Pendiente' o 'Aprobado').
    """
    try:
        conn = get_conn(); cur = conn.cursor()
        # contar inversiones del usuario
        cur.execute("SELECT COUNT(*), MAX(fecha_inversion) FROM inversiones WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        count = row[0] if row else 0
        last_date = row[1] if row and row[1] else None
        if count == 0:
            return True  # primera inversión permitida
        # buscar referidos registrados del usuario
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM usuarios WHERE referido_por=?", (user_id,))
        referidos = [r[0] for r in cur.fetchall()]
        if not referidos:
            conn.close()
            return False
        # convertir last_date a date
        last_dt = parse_date_iso(last_date) if last_date else None
        # revisar cada referido: debe tener al menos 1 inversion (Pendiente o Aprobado)
        for rid in referidos:
            cur.execute("SELECT MIN(fecha_inversion) FROM inversiones WHERE user_id=?", (rid,))
            rmin = cur.fetchone()
            if not rmin:
                continue
            first_inv_date = rmin[0]
            if not first_inv_date:
                continue
            first_dt = parse_date_iso(first_inv_date)
            # referido debe haberse invertido y esa inversión debe ser posterior a la última inversión del usuario
            if first_dt and last_dt:
                if first_dt > last_dt:
                    # además verificar que referido tenga al menos una inversión con estado Pendiente o Aprobado
                    cur.execute("SELECT COUNT(*) FROM inversiones WHERE user_id=? AND estado IN ('Pendiente','Aprobado')", (rid,))
                    cnt = cur.fetchone()[0]
                    if cnt and cnt > 0:
                        conn.close()
                        return True
            else:
                # si no hay last_dt por alguna razón, basta con que referido tenga inversión
                cur.execute("SELECT COUNT(*) FROM inversiones WHERE user_id=? AND estado IN ('Pendiente','Aprobado')", (rid,))
                cnt = cur.fetchone()[0]
                if cnt and cnt > 0:
                    conn.close()
                    return True
        conn.close()
        return False
    except Exception:
        traceback.print_exc()
        return False

def save_file_from_message(message, filename):
    try:
        file_id = None
        if message.photo:
            file_id = message.photo[-1].file_id
        elif message.document:
            file_id = message.document.file_id
        else:
            return None, "No hay archivo en el mensaje."
        file_info = bot.get_file(file_id)
        data = bot.download_file(file_info.file_path)
        path = os.path.join(DOWNLOAD_DIR, filename)
        with open(path, "wb") as f:
            f.write(data)
        return path, None
    except Exception as e:
        return None, str(e)

def procesar_comprobante(message, monto):
    try:
        chat_id = message.chat.id
        user_id = message.from_user.id
        if not (message.photo or message.document):
            safe_send(chat_id, "⚠️ Debes enviar una imagen del comprobante.")
            return
        safe_send(chat_id, "🧾 Comprobante recibido. Verificando, esto puede tardar unos segundos ⏳...")
        timestamp = int(time.time())
        filename = f"comp_{user_id}_{timestamp}.jpg"
        saved_path, err = save_file_from_message(message, filename)
        if not saved_path:
            safe_send(chat_id, f"⚠️ Error al guardar archivo: {err}")
            return

        ocr_text = ""
        ocr_ok = False
        ocr_reason = ""
        if TESSERACT_AVAILABLE:
            try:
                img = Image.open(saved_path)
                ocr_text = pytesseract.image_to_string(img, lang='spa')
                # extracción de números (posible monto - buscamos cifra grande)
                nums = re.findall(r'\d{3,}', ocr_text.replace(".", "").replace(",", ""))
                monto_detected = int(max(nums, key=len)) if nums else None
                cleaned_text = ocr_text.replace(" ", "").replace("\n","")
                # validar número destino y monto aproximado
                if NEQUI_DESTINO in cleaned_text and monto_detected and abs(monto_detected - monto) <= 2000:
                    ocr_ok = True
                else:
                    ocr_ok = False
                    ocr_reason = "No se detectó número destino o monto coincidente."
            except Exception as e:
                ocr_ok = False
                ocr_reason = f"OCR falló: {e}"
        else:
            ocr_ok = False
            ocr_reason = "OCR no disponible en este entorno."

        fecha_inversion = iso_today()
        fecha_pago = (datetime.date.today() + datetime.timedelta(days=3)).strftime("%d/%m/%Y")

        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO inversiones (user_id, monto, fecha_inversion, fecha_pago, estado, comprobante_path, ocr_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, monto, str(fecha_inversion), fecha_pago, "Pendiente", saved_path, ocr_text))
        conn.commit(); conn.close()

        if ocr_ok:
            safe_send(chat_id, f"✅ Comprobante recibido y verificado preliminarmente. Está pendiente de aprobación por el administrador.\n📅 Fecha estimada de pago: {fecha_pago}")
            safe_send(ADMIN_ID, f"📥 Nuevo comprobante PENDIENTE de {message.from_user.first_name} (${fmt_money(monto)}). OCR OK.")
        else:
            safe_send(chat_id, f"⚠️ Comprobante recibido pero no se pudo verificar automáticamente: {ocr_reason}\nEl administrador lo revisará manualmente.")
            safe_send(ADMIN_ID, f"📥 Nuevo comprobante PENDIENTE de {message.from_user.first_name} (${fmt_money(monto)}). OCR: {ocr_reason}")
    except Exception:
        traceback.print_exc()
        safe_send(message.chat.id, "⚠️ Ocurrió un error procesando el comprobante. Intenta nuevamente.")

# ---------------- Admin Panel ----------------
@bot.message_handler(func=lambda m: m.text == "📈 Panel admin")
def panel_admin(m):
    if m.from_user.id != ADMIN_ID:
        safe_send(m.chat.id, "❌ No tienes acceso.")
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📊 Estadísticas"), types.KeyboardButton("🔎 Revisar pendientes"))
    markup.add(types.KeyboardButton("📜 Historial"), types.KeyboardButton("🔙 Volver"))
    safe_send(m.chat.id, "Panel admin - selecciona una opción:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📊 Estadísticas")
def admin_stats(m):
    if m.from_user.id != ADMIN_ID:
        return
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM usuarios"); total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM inversiones WHERE estado='Pendiente'"); pend = cur.fetchone()[0]
    cur.execute("SELECT SUM(monto) FROM inversiones WHERE estado='Aprobado'"); s = cur.fetchone()[0] or 0
    conn.close()
    safe_send(m.chat.id, f"📊 Usuarios: {total_users}\nInversiones pendientes: {pend}\nTotal invertido (aprobado): ${fmt_money(s)}")

@bot.message_handler(func=lambda m: m.text == "🔎 Revisar pendientes")
def admin_revisar_pendientes(m):
    if m.from_user.id != ADMIN_ID:
        return
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, user_id, monto, fecha_inversion, fecha_pago, comprobante_path, ocr_text FROM inversiones WHERE estado='Pendiente' ORDER BY id ASC")
    rows = cur.fetchall(); conn.close()
    if not rows:
        safe_send(m.chat.id, "✅ No hay inversiones pendientes.")
        return
    for r in rows:
        inv_id, uid, monto, finv, fpago, path, ocr_text = r
        text = f"ID:{inv_id} · Usuario:{uid} · Monto:${fmt_money(monto)} · Fecha pago:{fpago}\nOCR: {ocr_text[:200] if ocr_text else 'N/A'}"
        if path and os.path.exists(path):
            try:
                bot.send_photo(m.chat.id, open(path, "rb"), caption=text)
            except:
                safe_send(m.chat.id, text)
        else:
            safe_send(m.chat.id, text)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Aprobar", callback_data=f"APP|{inv_id}"),
               types.InlineKeyboardButton("❌ Rechazar", callback_data=f"REJ|{inv_id}"))
        safe_send(m.chat.id, "Acciones:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data and (c.data.startswith("APP|") or c.data.startswith("REJ|")))
def admin_process_callback(c):
    try:
        if c.from_user.id != ADMIN_ID:
            return bot.answer_callback_query(c.id, "No autorizado.")
        action, inv_id = c.data.split("|")
        inv_id = int(inv_id)
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id, monto, fecha_pago FROM inversiones WHERE id=?", (inv_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return bot.answer_callback_query(c.id, "Inversión no encontrada.")
        uid, monto, fecha_pago = row
        if action == "APP":
            cur.execute("UPDATE inversiones SET estado='Aprobado' WHERE id=?", (inv_id,))
            ganancia = int(monto * 0.6)  # 60% ganancia como antes
            cur.execute("UPDATE usuarios SET total_invertido = total_invertido + ?, ganancia_total = ganancia_total + ? WHERE user_id=?", (monto, ganancia, uid))
            conn.commit(); conn.close()
            bot.answer_callback_query(c.id, "Inversión aprobada.")
            safe_send(ADMIN_ID, f"✅ Inversión {inv_id} aprobada.")
            try:
                safe_send(uid, f"✅ Tu inversión de ${fmt_money(monto)} ha sido aprobada.\n💰 Ganancia estimada: ${fmt_money(ganancia)}\n📅 Recibirás tu pago el {fecha_pago}")
            except:
                pass
        else:
            cur.execute("UPDATE inversiones SET estado='Rechazado' WHERE id=?", (inv_id,))
            conn.commit(); conn.close()
            bot.answer_callback_query(c.id, "Inversión rechazada.")
            safe_send(ADMIN_ID, f"❌ Inversión {inv_id} rechazada.")
            try:
                safe_send(uid, f"❌ Tu comprobante de ${fmt_money(monto)} fue rechazado. Revisa la información y vuelve a enviar uno válido.")
            except:
                pass
    except Exception:
        traceback.print_exc()
        try:
            bot.answer_callback_query(c.id, "Error procesando acción.")
        except:
            pass

@bot.message_handler(func=lambda m: m.text == "📜 Historial")
def admin_historial(m):
    if m.from_user.id != ADMIN_ID:
        return
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, user_id, monto, estado, fecha_inversion FROM inversiones ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall(); conn.close()
    if not rows:
        safe_send(m.chat.id, "No hay historial.")
        return
    s = "📜 Historial (últimos 50):\n"
    for r in rows:
        s += f"ID {r[0]} | U:{r[1]} | ${fmt_money(r[2])} | {r[3]} | Inv:{r[4]}\n"
    safe_send(m.chat.id, s)

@bot.message_handler(func=lambda m: m.text == "🔙 Volver")
def admin_volver(m):
    safe_send(m.chat.id, "Volviendo al menú...", reply_markup=menu_principal_for(m.from_user.id))
# ---------------- Comando /ping (solo admin) ----------------
@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ No tienes permiso para usar este comando.")
        return

    start = time.time()
    msg = bot.reply_to(message, "⏳ Ping en progreso...")
    end = time.time()
    latency = (end - start) * 1000  # milisegundos

    bot.edit_message_text(
        f"Pong 🟢 ({latency:.1f} ms)",
        chat_id=message.chat.id,
        message_id=msg.message_id
    )

# ---------------- Comando admin /dumpdb ----------------
@bot.message_handler(commands=["dumpdb"])
def cmd_dumpdb(message):
    try:
        if message.from_user.id != ADMIN_ID:
            bot.reply_to(message, "No autorizado.")
            return
        if not os.path.exists(DB_FILE):
            bot.reply_to(message, "❌ No existe la base de datos.")
            return
        with open(DB_FILE, "rb") as f:
            bot.send_document(ADMIN_ID, f, caption="📥 Base de datos (inversionesct.db)")
    except Exception:
        traceback.print_exc()
        try:
            bot.reply_to(message, "Error al enviar la base de datos.")
        except:
            pass

# ---------------- Flask keep-alive + /download-db (protegido) ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "InversionesCT está en línea ✅"

@app.route("/download-db")
def download_db():
    token = request.args.get("token", "")
    DB_DOWNLOAD_TOKEN = os.environ.get("DB_DOWNLOAD_TOKEN", str(ADMIN_ID))
    if token != DB_DOWNLOAD_TOKEN:
        abort(403)
    if not os.path.exists(DB_FILE):
        abort(404)
    zip_path = "/tmp/inversionesct_db_backup.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(DB_FILE, arcname=os.path.basename(DB_FILE))
    return send_file(zip_path, as_attachment=True)

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# ---------------- Backup automático diario ----------------
def backup_task(interval_hours=24):
    while True:
        try:
            if not os.path.exists(DB_FILE):
                time.sleep(60*10)
                continue
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
            zip_path = f"/tmp/inversionesct_backup_{timestamp}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(DB_FILE, arcname=os.path.basename(DB_FILE))
            try:
                with open(zip_path, "rb") as f:
                    bot.send_document(ADMIN_ID, f, caption=f"🔁 Backup automático - {timestamp}")
            except Exception:
                try:
                    bot.send_message(ADMIN_ID, f"⚠️ Backup creado en servidor: {zip_path}")
                except:
                    pass
        except Exception as e:
            try:
                bot.send_message(ADMIN_ID, f"⚠️ Error en backup automático: {e}")
            except:
                pass
        time.sleep(interval_hours * 3600)

backup_thread = threading.Thread(target=backup_task, args=(24,), daemon=True)
backup_thread.start()

# ---------------- Fallback handler ----------------
@bot.message_handler(func=lambda m: True)
def fallback(m):
    safe_send(m.chat.id, "Selecciona una opción:", reply_markup=menu_principal_for(m.from_user.id))

# ---------------- Polling con reconexión ----------------
def start_polling_with_retries():
    print("🤖 InversionesCT iniciado. TESSERACT_AVAILABLE =", TESSERACT_AVAILABLE)
    fails = 0
    last_ok = time.time()
    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=60)
            fails = 0
            last_ok = time.time()
        except Exception as e:
            fails += 1
            print("⚠️ Polling error:", e)
            traceback.print_exc()
            if fails >= 3:
                try:
                    safe_send(ADMIN_ID, f"⚠️ El bot ha fallado {fails} veces seguidas. Revisar conexión.")
                except Exception:
                    pass
            time.sleep(15)

# ---------------- Autoping automático ----------------
def self_ping(interval=300):
    """
    Envía solicitudes periódicas al propio servidor Flask para evitar suspensión.
    Compatible con Replit y Pydroid.
    """
    import requests
    import socket

    repl_slug = os.environ.get("REPL_SLUG")
    repl_owner = os.environ.get("REPL_OWNER")

    if repl_slug and repl_owner:
        url = f"https://{repl_slug}.{repl_owner}.repl.co/"
    else:
        local_ip = socket.gethostbyname(socket.gethostname())
        url = f"http://{local_ip}:8080/"

    print(f"🌐 Autoping activo hacia: {url}")

    time.sleep(10)  # esperar que Flask levante

    while True:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                print(f"✅ Autoping OK: {r.status_code}")
            else:
                print(f"⚠️ Autoping código {r.status_code}")
        except Exception as e:
            print(f"⚠️ Error en autoping: {e}")
        time.sleep(interval)

# ---------------- Watchdog (reinicio por inactividad) ----------------
def watchdog(interval=300, timeout_limit=600):
    """
    Revisa periódicamente si el bot sigue respondiendo.
    Si detecta inactividad prolongada, reinicia el proceso.
    """
    import sys
    last_check = time.time()
    while True:
        now = time.time()
        if now - last_check > timeout_limit:
            try:
                safe_send(ADMIN_ID, "⚠️ Watchdog: reiniciando bot por inactividad prolongada.")
            except:
                pass
            os.execv(sys.executable, ['python'] + sys.argv)
        time.sleep(interval)
        last_check = now

# ---------------- MAIN ----------------
if __name__ == "__main__":
    keep_alive()
    threading.Thread(target=self_ping, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        bot.send_message(ADMIN_ID, "🤖 Bot InversionesCT iniciado (modo estable 24/7).")
    except:
        pass

    # Bucle de polling con autoreinicio
    start_polling_with_retries()

    # Bucle infinito de seguridad
    while True:
        time.sleep(60)
