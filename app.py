from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from werkzeug.utils import secure_filename
import mysql.connector
import os
import csv
import io
import math
import joblib
import numpy as np
from functools import wraps
from datetime import date, datetime, timedelta

app = Flask(__name__)
app.secret_key = '797319272ab0a6618ecf8a707484f78515f8d246a32bceef52507641745ef2a'
app.config['UPLOAD_FOLDER'] = 'static/uploads'


# ─────────────────────────────────────────────
# DB connection factory
# FIX 1: was split into get_db() vs get_db_connection() — unified to one name
# ─────────────────────────────────────────────
def get_db():
    return mysql.connector.connect(
        host=os.environ.get('MYSQLHOST', 'localhost'),
        user=os.environ.get('MYSQLUSER', 'root'),
        password=os.environ.get('MYSQLPASSWORD', 'Root'),
        database=os.environ.get('MYSQLDATABASE', 'fire'),
        port=int(os.environ.get('MYSQLPORT', 3306)),
        connection_timeout=10,    # fail fast instead of hanging
        connect_timeout=10
    )

get_db_connection = get_db

try:
    classifier_data = joblib.load('ml/classifier.pkl')
    risk_data = joblib.load('ml/risk_scorer.pkl')
except Exception as e:
    print(f"[ML] Models not loaded: {e}")
    classifier_data = None
    risk_data = None

# ─────────────────────────────────────────────
# Auth decorators
# ─────────────────────────────────────────────
def officer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('role') != 'Officer':
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'Admin':
            flash('Access denied.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
# Home
# ─────────────────────────────────────────────
@app.route('/')
def home():
    return redirect(url_for('login'))


# ─────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name     = request.form['name']
        email    = request.form['email']
        password = request.form['password']
        role     = request.form['role']

        if not password[0].isupper():
            return render_template('register.html', error="Password must start with a capital letter")
        if len(password) < 8:
            return render_template('register.html', error="Password must be at least 8 characters long")
        if not any(c.isdigit() for c in password) or not any(c in "!@#$%^&*()" for c in password):
            return render_template('register.html', error="Password must contain a number and a special character")

        db = get_db()
        cur = db.cursor()
        cur.execute(
            "INSERT INTO users (name, email, password, role) VALUES (%s, %s, %s, %s)",
            (name, email, password, role)
        )
        db.commit()
        cur.close()
        db.close()
        return redirect(url_for('login'))

    return render_template('register.html')


# ─────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']

        db = get_db()
        cur = db.cursor()
        cur.execute(
            "SELECT user_id, name, password, role FROM users WHERE email = %s",
            (email,)
        )
        user = cur.fetchone()
        cur.close()
        db.close()

        if user and user[2] == password:
            session['user_id']  = user[0]
            session['username'] = user[1]
            session['role']     = user[3]

            if user[3] == 'Applicant':
                return redirect(url_for('applicant_dashboard'))
            elif user[3] == 'Officer':
                return redirect(url_for('officer_dashboard'))
            elif user[3] == 'Admin':
                return redirect(url_for('admin_dashboard'))
        else:
            return render_template('login.html', error="Invalid email or password")

    return render_template('login.html')


# ─────────────────────────────────────────────
# Logout
# ─────────────────────────────────────────────
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ═══════════════════════════════════════════════════════════════
# APPLICANT ROUTES  — matches new templates + fire DB schema
# ═══════════════════════════════════════════════════════════════
import os, random, string
from functools import wraps
from werkzeug.utils import secure_filename

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXT   = {'pdf', 'jpg', 'jpeg', 'png'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def applicant_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'Applicant':
            flash('Access denied.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Dashboard ─────────────────────────────────────────────────

@app.route('/applicant/dashboard')
@applicant_required
def applicant_dashboard():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s", (uid,))
    total = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Pending'", (uid,))
    pending = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Approved'", (uid,))
    approved = cur.fetchone()['n']
    cur.execute("""
        SELECT COUNT(*) AS n FROM nocs n
        JOIN applications a ON n.app_id = a.app_id
        WHERE a.applicant_id=%s AND n.status='Active'
    """, (uid,))
    nocs = cur.fetchone()['n']

    cur.execute("""
        SELECT app_id, type, status, date_submitted
        FROM applications WHERE applicant_id=%s
        ORDER BY date_submitted DESC LIMIT 5
    """, (uid,))
    recent_apps = cur.fetchall()

    cur.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN f.status='Paid'    THEN f.amount ELSE 0 END),0) AS paid,
            COALESCE(SUM(CASE WHEN f.status='Pending' THEN f.amount ELSE 0 END),0) AS pending,
            COALESCE(SUM(CASE WHEN f.status='Overdue' THEN f.amount ELSE 0 END),0) AS overdue
        FROM fee_ledger f
        JOIN applications a ON f.app_id = a.app_id
        WHERE a.applicant_id=%s
    """, (uid,))
    fees = cur.fetchone()

    cur.execute("""
        SELECT i.* FROM inspections i
        JOIN applications a ON i.app_id = a.app_id
        WHERE a.applicant_id=%s AND i.status='Scheduled' AND i.date >= CURDATE()
        ORDER BY i.date ASC LIMIT 1
    """, (uid,))
    next_inspection = cur.fetchone()

    cur.execute("""
        SELECT title, message, priority, created_at FROM announcements
        WHERE audience IN ('all','applicants')
        ORDER BY created_at DESC LIMIT 5
    """)
    announcements = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id=%s AND is_read=0", (uid,))
    unread_count = cur.fetchone()['n']

    cur.close(); conn.close()
    return render_template('applicant/applicant_dashboard.html',
                           stats=dict(total=total, pending=pending, approved=approved, nocs=nocs),
                           recent_apps=recent_apps,
                           fees=fees,
                           next_inspection=next_inspection,
                           announcements=announcements,
                           unread_count=unread_count,
                           now=datetime.now())


# ── My Applications ───────────────────────────────────────────

@app.route('/applicant/applications')
@applicant_required
def applicant_applications():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT a.app_id, a.type, a.status, a.date_submitted,
               a.location, a.building_type, a.tracking_id, a.priority_level,
               u.name AS officer_name
        FROM applications a
        LEFT JOIN users u ON a.assigned_officer = u.user_id
        WHERE a.applicant_id=%s
        ORDER BY a.date_submitted DESC
    """, (uid,))
    apps = cur.fetchall()
    cur.close(); conn.close()
    return render_template('applicant/applicant_applications.html', apps=apps)


@app.route('/applicant/applications/<int:app_id>')
@applicant_required
def applicant_view_application(app_id):
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT a.*, u.name AS officer_name, u.email AS officer_email
        FROM applications a
        LEFT JOIN users u ON a.assigned_officer = u.user_id
        WHERE a.app_id=%s AND a.applicant_id=%s
    """, (app_id, uid))
    app_data = cur.fetchone()
    if not app_data:
        flash('Application not found.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('applicant_applications'))

    cur.execute("SELECT * FROM application_documents WHERE app_id=%s", (app_id,))
    docs = cur.fetchall()
    cur.execute("""
        SELECT i.*, u.name AS officer_name FROM inspections i
        LEFT JOIN users u ON i.officer_id = u.user_id
        WHERE i.app_id=%s ORDER BY i.date DESC
    """, (app_id,))
    inspections = cur.fetchall()
    cur.execute("SELECT * FROM fee_ledger WHERE app_id=%s ORDER BY due_date DESC", (app_id,))
    fees = cur.fetchall()
    cur.execute("SELECT * FROM nocs WHERE app_id=%s", (app_id,))
    nocs = cur.fetchall()

    cur.close(); conn.close()
    return render_template('applicant/applicant_view_application.html',
                           app=app_data, docs=docs,
                           inspections=inspections, fees=fees, nocs=nocs)


@app.route('/applicant/applications/<int:app_id>/withdraw', methods=['POST'])
@applicant_required
def applicant_withdraw(app_id):
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        UPDATE applications SET status='Withdrawn'
        WHERE app_id=%s AND applicant_id=%s AND status='Pending'
    """, (app_id, uid))
    conn.commit()
    flash('Application withdrawn.' if cur.rowcount else 'Could not withdraw — only Pending apps can be withdrawn.', 
          'success' if cur.rowcount else 'error')
    cur.close(); conn.close()
    return redirect(url_for('applicant_applications'))


@app.route('/applicant/applications/<int:app_id>/edit', methods=['GET', 'POST'])
@applicant_required
def edit_application(app_id):
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT app_id, type, location, building_type, description,
               preferred_date, preferred_time, priority_level
        FROM applications WHERE app_id=%s AND applicant_id=%s AND status='Pending'
    """, (app_id, uid))
    application = cur.fetchone()
    if not application:
        flash('Application not found or cannot be edited.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('applicant_applications'))

    if request.method == 'POST':
        cur.execute("""
            UPDATE applications
            SET type=%s, location=%s, building_type=%s, description=%s,
                preferred_date=%s, preferred_time=%s, priority_level=%s
            WHERE app_id=%s AND applicant_id=%s
        """, (
            request.form.get('type'),
            request.form.get('location'),
            request.form.get('building_type'),
            request.form.get('description'),
            request.form.get('preferred_date') or None,
            request.form.get('preferred_time'),
            request.form.get('priority_level', 'Normal'),
            app_id, uid
        ))
        conn.commit()
        flash('Application updated.', 'success')
        cur.close(); conn.close()
        return redirect(url_for('applicant_applications'))

    cur.close(); conn.close()
    return render_template('applicant/edit_application.html', application=application)


# ── Submit New Application ────────────────────────────────────

@app.route('/applicant/apply', methods=['GET', 'POST'])
@applicant_required
def applicant_apply():
    if request.method == 'POST':
        uid         = session['user_id']
        tracking_id = 'FD-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("""
            INSERT INTO applications
                (applicant_id, type, date_submitted, status, location,
                 building_type, description, preferred_date, preferred_time,
                 priority_level, tracking_id)
            VALUES (%s,%s,CURDATE(),'Pending',%s,%s,%s,%s,%s,%s,%s)
        """, (
            uid,
            request.form.get('type'),
            request.form.get('location'),
            request.form.get('building_type'),
            request.form.get('description'),
            request.form.get('preferred_date') or None,
            request.form.get('preferred_time'),
            request.form.get('priority_level', 'Normal'),
            tracking_id
        ))
        app_id = cur.lastrowid
        conn.commit()

        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        for f in request.files.getlist('documents'):
            if f and f.filename and allowed_file(f.filename):
                filename  = secure_filename(f.filename)
                ext       = filename.rsplit('.', 1)[1].lower()
                save_name = f"app_{app_id}_{filename}"
                f.save(os.path.join(UPLOAD_FOLDER, save_name))
                cur.execute("""
                    INSERT INTO application_documents
                        (app_id, doc_name, doc_type, file_path, uploaded_at)
                    VALUES (%s,%s,%s,%s,%s)
                """, (app_id, filename, ext, f'uploads/{save_name}', datetime.now()))

        cur.execute("""
            INSERT INTO notifications (user_id, title, message, is_read, created_at)
            VALUES (%s,'Application Submitted',%s,0,%s)
        """, (uid, f'Application submitted. Tracking ID: {tracking_id}', datetime.now()))
        conn.commit()
        cur.close(); conn.close()
        run_ml_on_application(app_id)

        flash(f'Application submitted! Tracking ID: {tracking_id}', 'success')
        return redirect(url_for('applicant_applications'))

    settings = get_admin_settings()
    return render_template('applicant/applicant_apply.html',
                           settings=settings, now=datetime.now())


# ── Fee Status ────────────────────────────────────────────────

@app.route('/applicant/fees')
@applicant_required
def applicant_fees():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT f.ledger_id, f.app_id, f.amount, f.status, f.due_date, f.paid_on
        FROM fee_ledger f
        JOIN applications a ON f.app_id = a.app_id
        WHERE a.applicant_id=%s ORDER BY f.due_date DESC
    """, (uid,))
    fees = cur.fetchall()
    cur.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN f.status='Paid'    THEN f.amount ELSE 0 END),0) AS paid,
            COALESCE(SUM(CASE WHEN f.status='Pending' THEN f.amount ELSE 0 END),0) AS pending,
            COALESCE(SUM(CASE WHEN f.status='Overdue' THEN f.amount ELSE 0 END),0) AS overdue
        FROM fee_ledger f
        JOIN applications a ON f.app_id = a.app_id
        WHERE a.applicant_id=%s
    """, (uid,))
    summary = cur.fetchone()
    cur.close(); conn.close()
    return render_template('applicant/applicant_fees.html', fees=fees, summary=summary)


# ── NOC Certificates ──────────────────────────────────────────

@app.route('/applicant/nocs')
@applicant_required
def applicant_nocs():
    uid    = session['user_id']
    conn   = get_db()
    cur    = conn.cursor(dictionary=True)
    cutoff = datetime.now() + timedelta(days=30)
    cur.execute("""
        SELECT n.noc_id, n.app_id, n.status, n.issue_date, n.validity,
               (n.validity <= %s AND n.status='Active') AS is_expiring
        FROM nocs n
        JOIN applications a ON n.app_id = a.app_id
        WHERE a.applicant_id=%s ORDER BY n.issue_date DESC
    """, (cutoff, uid))
    nocs = cur.fetchall()
    cur.close(); conn.close()
    return render_template('applicant/applicant_nocs.html', nocs=nocs)


@app.route('/applicant/nocs/<int:noc_id>/download')
@applicant_required
def applicant_download_noc(noc_id):
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT n.noc_id, n.issue_date, n.validity, n.status,
               a.type, a.location, a.building_type,
               u.name AS applicant_name, u.email,
               o.name AS officer_name
        FROM nocs n
        JOIN applications a ON n.app_id      = a.app_id
        JOIN users u         ON a.applicant_id = u.user_id
        LEFT JOIN users o    ON a.officer_id   = o.user_id
        WHERE n.noc_id=%s AND a.applicant_id=%s AND n.status='Active'
    """, (noc_id, uid))
    noc = cur.fetchone()
    cur.close(); conn.close()
    if not noc:
        flash('Certificate not found or not available.', 'error')
        return redirect(url_for('applicant_nocs'))
    return render_template('applicant/letter.html', letter=noc)


# ── Notifications ─────────────────────────────────────────────

@app.route('/applicant/notifications')
@applicant_required
def applicant_notifications():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM notifications WHERE user_id=%s ORDER BY created_at DESC", (uid,))
    notifications = cur.fetchall()
    cur.close(); conn.close()
    return render_template('applicant/applicant_notifications.html', notifications=notifications)


@app.route('/applicant/notifications/<int:notif_id>/read', methods=['POST'])
@applicant_required
def applicant_mark_read(notif_id):
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE notif_id=%s AND user_id=%s", (notif_id, uid))
    conn.commit()
    cur.close(); conn.close()
    return redirect(url_for('applicant_notifications'))


@app.route('/applicant/notifications/mark-all-read', methods=['POST'])
@applicant_required
def applicant_mark_all_read():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (uid,))
    conn.commit()
    cur.close(); conn.close()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('applicant_notifications'))


# ── Documents ─────────────────────────────────────────────────

@app.route('/applicant/documents')
@applicant_required
def applicant_documents():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT d.doc_id, d.app_id, d.doc_name, d.doc_type, d.file_path, d.uploaded_at
        FROM application_documents d
        JOIN applications a ON d.app_id = a.app_id
        WHERE a.applicant_id=%s ORDER BY d.uploaded_at DESC
    """, (uid,))
    docs = cur.fetchall()
    cur.execute("""
        SELECT app_id, type FROM applications
        WHERE applicant_id=%s ORDER BY date_submitted DESC
    """, (uid,))
    apps = cur.fetchall()
    cur.close(); conn.close()
    return render_template('applicant/applicant_documents.html', docs=docs, apps=apps)


@app.route('/applicant/documents/upload', methods=['POST'])
@applicant_required
def applicant_upload_document():
    uid    = session['user_id']
    app_id = request.form.get('app_id')
    name   = request.form.get('doc_name', '').strip()
    dtype  = request.form.get('doc_type', '')
    file   = request.files.get('document')

    if not app_id or not file or not file.filename:
        flash('Please select an application and a file.', 'error')
        return redirect(url_for('applicant_documents'))
    if not allowed_file(file.filename):
        flash('Only PDF, JPG, and PNG files are allowed.', 'error')
        return redirect(url_for('applicant_documents'))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT app_id FROM applications WHERE app_id=%s AND applicant_id=%s", (app_id, uid))
    if not cur.fetchone():
        flash('Invalid application selected.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('applicant_documents'))

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    filename  = secure_filename(file.filename)
    ext       = filename.rsplit('.', 1)[1].lower()
    save_name = f"app_{app_id}_{filename}"
    file.save(os.path.join(UPLOAD_FOLDER, save_name))
    cur.execute("""
        INSERT INTO application_documents (app_id, doc_name, doc_type, file_path, uploaded_at)
        VALUES (%s,%s,%s,%s,%s)
    """, (app_id, name or filename, dtype or ext, f'uploads/{save_name}', datetime.now()))
    conn.commit()
    cur.close(); conn.close()
    flash('Document uploaded successfully.', 'success')
    return redirect(url_for('applicant_documents'))


# ── Inspections ───────────────────────────────────────────────

@app.route('/applicant/inspections')
@applicant_required
def applicant_inspections():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT i.inspection_id, i.app_id, i.date, i.status, i.remarks,
               u.name AS officer_name
        FROM inspections i
        JOIN applications a ON i.app_id = a.app_id
        LEFT JOIN users u ON i.officer_id = u.user_id
        WHERE a.applicant_id=%s ORDER BY i.date DESC
    """, (uid,))
    inspections = cur.fetchall()
    cur.execute("""
        SELECT i.inspection_id, i.app_id, i.date, i.status, i.remarks,
               u.name AS officer_name
        FROM inspections i
        JOIN applications a ON i.app_id = a.app_id
        LEFT JOIN users u ON i.officer_id = u.user_id
        WHERE a.applicant_id=%s AND i.status='Scheduled' AND i.date >= CURDATE()
        ORDER BY i.date ASC
    """, (uid,))
    upcoming = cur.fetchall()
    cur.close(); conn.close()
    return render_template('applicant/applicant_inspections.html',
                           inspections=inspections, upcoming=upcoming)


# ── Messages ──────────────────────────────────────────────────

@app.route('/applicant/messages')
@applicant_required
def applicant_messages():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT m.msg_id, m.sender_id, m.receiver_id, m.subject,
               m.message, m.status, m.created_at, u.name AS sender_name
        FROM messages m
        JOIN users u ON m.sender_id = u.user_id
        WHERE m.sender_id=%s OR m.receiver_id=%s
        ORDER BY m.created_at ASC
    """, (uid, uid))
    messages_list = cur.fetchall()
    cur.execute("""
        SELECT app_id, type FROM applications
        WHERE applicant_id=%s ORDER BY date_submitted DESC
    """, (uid,))
    apps = cur.fetchall()
    cur.execute("SELECT COUNT(*) AS n FROM messages WHERE sender_id=%s OR receiver_id=%s", (uid, uid))
    total_msgs = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM messages WHERE receiver_id=%s AND status='Unread'", (uid,))
    unread_msgs = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM messages WHERE sender_id=%s AND status='Responded'", (uid,))
    responded = cur.fetchone()['n']
    cur.close(); conn.close()
    return render_template('applicant/applicant_messages.html',
                           messages_list=messages_list, apps=apps,
                           msg_stats=dict(total=total_msgs, unread=unread_msgs, responded=responded))


@app.route('/applicant/messages/send', methods=['POST'])
@applicant_required
def applicant_send_message():
    uid     = session['user_id']
    app_id  = request.form.get('app_id') or None
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()

    if not message:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('applicant_messages'))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    receiver_id = None
    if app_id:
        cur.execute("""
            SELECT assigned_officer FROM applications
            WHERE app_id=%s AND applicant_id=%s
        """, (app_id, uid))
        row = cur.fetchone()
        if row and row['assigned_officer']:
            receiver_id = row['assigned_officer']
    if not receiver_id:
        cur.execute("SELECT user_id FROM users WHERE role='Admin' LIMIT 1")
        row = cur.fetchone()
        receiver_id = row['user_id'] if row else None
    if not receiver_id:
        flash('No officer or admin available.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('applicant_messages'))

    cur.execute("""
        INSERT INTO messages (sender_id, receiver_id, subject, message, status, created_at)
        VALUES (%s,%s,%s,%s,'Unread',%s)
    """, (uid, receiver_id, subject, message, datetime.now()))
    conn.commit()
    cur.close(); conn.close()
    flash('Message sent successfully.', 'success')
    return redirect(url_for('applicant_messages'))


# ── Profile ───────────────────────────────────────────────────

@app.route('/applicant/profile', methods=['GET', 'POST'])
@applicant_required
def applicant_profile():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    if request.method == 'POST':
        name  = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        if not name or not email:
            flash('Name and email are required.', 'error')
            cur.close(); conn.close()
            return redirect(url_for('applicant_profile'))
        cur.execute("SELECT user_id FROM users WHERE email=%s AND user_id!=%s", (email, uid))
        if cur.fetchone():
            flash('That email is already in use.', 'error')
            cur.close(); conn.close()
            return redirect(url_for('applicant_profile'))
        cur.execute("UPDATE users SET name=%s, email=%s, phone=%s WHERE user_id=%s",
                    (name, email, phone, uid))
        conn.commit()
        session['username'] = name
        session['email']    = email
        flash('Profile updated successfully.', 'success')
        cur.close(); conn.close()
        return redirect(url_for('applicant_profile'))

    cur.execute("SELECT user_id, name, email, phone, created_at FROM users WHERE user_id=%s", (uid,))
    user = cur.fetchone()
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s", (uid,))
    total = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Approved'", (uid,))
    approved = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Pending'", (uid,))
    pending = cur.fetchone()['n']
    cur.execute("""
        SELECT COUNT(*) AS n FROM nocs n
        JOIN applications a ON n.app_id = a.app_id
        WHERE a.applicant_id=%s AND n.status='Active'
    """, (uid,))
    nocs = cur.fetchone()['n']
    cur.close(); conn.close()
    return render_template('applicant/applicant_profile.html',
                           user=user,
                           stats=dict(total=total, approved=approved,
                                      pending=pending, nocs=nocs))


# ── Change Password ───────────────────────────────────────────

@app.route('/applicant/change-password', methods=['POST'])
@applicant_required
def applicant_change_password():
    uid         = session['user_id']
    current_pwd = request.form.get('current_password', '').strip()
    new_pwd     = request.form.get('new_password', '').strip()
    confirm_pwd = request.form.get('confirm_password', '').strip()

    if new_pwd != confirm_pwd:
        flash('New passwords do not match.', 'error')
        return redirect(url_for('applicant_profile'))
    if len(new_pwd) < 8:
        flash('Password must be at least 8 characters.', 'error')
        return redirect(url_for('applicant_profile'))

    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT password FROM users WHERE user_id=%s", (uid,))
    user = cur.fetchone()
    if not user or user['password'].strip() != current_pwd:
        flash('Current password is incorrect.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('applicant_profile'))

    cur.execute("UPDATE users SET password=%s WHERE user_id=%s", (new_pwd, uid))
    conn.commit()
    cur.close(); conn.close()
    flash('Password changed successfully.', 'success')
    return redirect(url_for('applicant_profile'))


# ── Insights ──────────────────────────────────────────────────

@app.route('/applicant/insights')
@applicant_required
def applicant_insights():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s", (uid,))
    total = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Approved'", (uid,))
    approved = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Pending'", (uid,))
    pending = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE applicant_id=%s AND status='Withdrawn'", (uid,))
    withdrawn = cur.fetchone()['n']
    approval_rate = round(approved / total * 100, 1) if total > 0 else 0
    cur.execute("""
        SELECT type, COUNT(*) AS cnt FROM applications
        WHERE applicant_id=%s GROUP BY type ORDER BY cnt DESC LIMIT 1
    """, (uid,))
    common_type = cur.fetchone()
    cur.execute("""
        SELECT type, status, date_submitted FROM applications
        WHERE applicant_id=%s ORDER BY date_submitted DESC LIMIT 5
    """, (uid,))
    recent_activity = cur.fetchall()
    cur.close(); conn.close()

    narrative = []
    if total == 0:
        narrative.append("You haven't submitted any applications yet.")
    else:
        if pending > 0:
            narrative.append(f"You have {pending} pending application(s) awaiting review.")
        if withdrawn > approved:
            narrative.append("You've withdrawn more than you've had approved — review requirements before resubmitting.")
        if approved > 0:
            narrative.append(f"Your approval rate is {approval_rate}%. Keep it up!")

    return render_template('applicant/insights.html',
                           total_apps=total, approved_apps=approved,
                           pending_apps=pending, withdrawn_apps=withdrawn,
                           approval_rate=approval_rate,
                           common_type=common_type,
                           recent_activity=recent_activity,
                           narrative=narrative)


# ═══════════════════════════════════════════════════════════════
# OFFICER ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route('/officer/dashboard')
@officer_required
def officer_dashboard():
    db = get_db()
    cur = db.cursor(dictionary=True)
    officer_id = session['user_id']

    cur.execute("SELECT COUNT(*) AS cnt FROM applications WHERE status = 'Pending'")
    pending_count = cur.fetchone()['cnt']

    cur.execute("SELECT COUNT(*) AS cnt FROM applications WHERE status = 'Approved'")
    approved_count = cur.fetchone()['cnt']

    cur.execute("SELECT COUNT(*) AS cnt FROM applications WHERE status = 'Rejected'")
    rejected_count = cur.fetchone()['cnt']

    cur.execute("""
        SELECT COUNT(*) AS cnt FROM inspections
        WHERE officer_id = %s AND status = 'Scheduled' AND date = CURDATE()
    """, (officer_id,))
    todays_inspections = cur.fetchone()['cnt']

    cur.execute("SELECT COUNT(*) AS cnt FROM iot_devices WHERE status = 'Offline'")
    offline_devices = cur.fetchone()['cnt']

    cur.execute("""
        SELECT COUNT(*) AS cnt FROM nocs
        WHERE status = 'Active' AND validity BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 30 DAY)
    """)
    expiring_nocs = cur.fetchone()['cnt']

    cur.execute("""
        SELECT COUNT(*) AS cnt FROM messages
        WHERE receiver_id = %s AND status = 'Unread'
    """, (officer_id,))
    unread_messages = cur.fetchone()['cnt']

    # ── ML Risk counts ─────────────────────────────────────────
    cur.execute("SELECT COUNT(*) AS cnt FROM applications WHERE ml_risk_level = 'High'")
    high_risk_count = cur.fetchone()['cnt']

    cur.execute("SELECT COUNT(*) AS cnt FROM applications WHERE ml_risk_level = 'Medium'")
    medium_risk = cur.fetchone()['cnt']

    cur.execute("SELECT COUNT(*) AS cnt FROM applications WHERE ml_risk_level = 'Low'")
    low_risk = cur.fetchone()['cnt']

    total_scored = high_risk_count + medium_risk + low_risk or 1  # avoid division by zero

    risk_counts = dict(
        high=high_risk_count,
        medium=medium_risk,
        low=low_risk,
        high_pct=round(high_risk_count / total_scored * 100),
        medium_pct=round(medium_risk / total_scored * 100),
        low_pct=round(low_risk / total_scored * 100),
    )

    cur.close(); db.close()
    return render_template('officer/officer_dashboard.html',
                           pending_count=pending_count,
                           approved_count=approved_count,
                           rejected_count=rejected_count,
                           todays_inspections=todays_inspections,
                           offline_devices=offline_devices,
                           expiring_nocs=expiring_nocs,
                           unread_messages=unread_messages,
                           high_risk_count=high_risk_count,
                           risk_counts=risk_counts)


# ─── Application Queue ────────────────────────────────────────
@app.route('/officer/applications')
@officer_required
def application_queue():
    db = get_db()
    cur = db.cursor(dictionary=True)

    status_filter   = request.args.get('status', 'Pending')
    priority_filter = request.args.get('priority', '')
    search          = request.args.get('search', '')

    query = """
        SELECT a.app_id, a.type, a.date_submitted, a.status, a.location,
               a.building_type, a.priority_level, a.tracking_id, a.inspection_score,
               u_ap.name  AS applicant_name,
               u_ap.email AS applicant_email,
               u_ap.phone AS applicant_phone,
               DATEDIFF(CURDATE(), a.date_submitted) AS days_pending,
               u_off.name AS assigned_officer_name
        FROM applications a
        JOIN users u_ap  ON a.applicant_id        = u_ap.user_id
        LEFT JOIN users u_off ON a.assigned_officer = u_off.user_id
        WHERE 1=1
    """
    params = []

    if status_filter:
        query += " AND a.status = %s"
        params.append(status_filter)
    if priority_filter:
        query += " AND a.priority_level = %s"
        params.append(priority_filter)
    if search:
        query += " AND (u_ap.name LIKE %s OR a.tracking_id LIKE %s OR a.location LIKE %s)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]

    query += " ORDER BY a.date_submitted ASC"
    cur.execute(query, params)
    applications = cur.fetchall()

    cur.execute("SELECT user_id, name FROM users WHERE role = 'Officer'")
    officers = cur.fetchall()

    cur.close(); db.close()
    return render_template('officer/officer_applications.html',
                           applications=applications,
                           officers=officers,
                           status_filter=status_filter,
                           priority_filter=priority_filter,
                           search=search)


# ─── Application Action ───────────────────────────────────────
@app.route('/officer/applications/<int:app_id>/action', methods=['POST'])
@officer_required
def application_action(app_id):
    db = get_db()
    cur = db.cursor()
    officer_id = session['user_id']
    action     = request.form.get('action')
    notes      = request.form.get('notes', '')
    assign_to  = request.form.get('assign_to')

    if action in ('Approved', 'Rejected'):
        cur.execute(
            "UPDATE applications SET status = %s, officer_id = %s WHERE app_id = %s",
            (action, officer_id, app_id)
        )
        cur.execute("""
            INSERT INTO application_history (app_id, status, changed_by, notes)
            VALUES (%s, %s, %s, %s)
        """, (app_id, action, officer_id, notes))
        if action == 'Approved':
            validity = date.today() + timedelta(days=365)
            cur.execute("""
                INSERT INTO nocs (app_id, issue_date, validity, status)
                VALUES (%s, %s, %s, 'Active')
            """, (app_id, date.today(), validity))
    elif action == 'assign' and assign_to:
        cur.execute(
            "UPDATE applications SET assigned_officer = %s WHERE app_id = %s",
            (assign_to, app_id)
        )

    db.commit()
    cur.close(); db.close()
    return redirect(url_for('application_queue'))


# ─── Alerts Page ──────────────────────────────────────────────
@app.route('/officer/alerts')
@officer_required
def alerts_page():
    return render_template('officer/alerts.html')


# ─── Alerts API ───────────────────────────────────────────────
@app.route('/officer/api/alerts')
@officer_required
def api_alerts():
    db = get_db()
    cur = db.cursor(dictionary=True)
    alerts = []

    cur.execute("""
        SELECT d.device_id, d.device_type, d.last_heartbeat,
               u.name AS owner_name, u.phone AS owner_phone
        FROM iot_devices d
        JOIN users u ON d.user_id = u.user_id
        WHERE d.status = 'Offline'
        ORDER BY d.last_heartbeat ASC LIMIT 20
    """)
    for row in cur.fetchall():
        alerts.append({
            'severity': 'critical', 'category': 'IOT', 'icon': '📡',
            'title': f"{row['device_type']} Offline",
            'detail': f"Owner: {row['owner_name']} | Last seen: {row['last_heartbeat']}",
            'time': str(row['last_heartbeat'])
        })

    cur.execute("""
        SELECT a.app_id, a.tracking_id, a.type, a.date_submitted,
               u.name AS applicant_name,
               DATEDIFF(CURDATE(), a.date_submitted) AS days_pending
        FROM applications a
        JOIN users u ON a.applicant_id = u.user_id
        WHERE a.status = 'Pending' AND DATEDIFF(CURDATE(), a.date_submitted) > 14
        ORDER BY days_pending DESC LIMIT 20
    """)
    for row in cur.fetchall():
        alerts.append({
            'severity': 'warning', 'category': 'SLA', 'icon': '⏱️',
            'title': f"SLA Breach — {row['tracking_id']}",
            'detail': f"{row['applicant_name']} | {row['type']} | {row['days_pending']} days overdue",
            'time': str(row['date_submitted'])
        })

    cur.execute("""
        SELECT i.inspection_id, i.date, i.remarks,
               a.tracking_id, a.location, a.building_type,
               u.name AS applicant_name
        FROM inspections i
        JOIN applications a ON i.app_id = a.app_id
        JOIN users u ON a.applicant_id = u.user_id
        WHERE i.status = 'Failed'
        ORDER BY i.date DESC LIMIT 20
    """)
    for row in cur.fetchall():
        alerts.append({
            'severity': 'critical', 'category': 'Inspection', 'icon': '🔥',
            'title': f"Inspection Failed — {row['tracking_id']}",
            'detail': f"{row['location']} | {row['building_type']} | {row['remarks'] or 'No remarks'}",
            'time': str(row['date'])
        })

    cur.execute("""
        SELECT n.noc_id, n.validity, a.tracking_id, a.location,
               u.name AS applicant_name, u.phone
        FROM nocs n
        JOIN applications a ON n.app_id = a.app_id
        JOIN users u ON a.applicant_id = u.user_id
        WHERE n.status = 'Active'
          AND n.validity BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 7 DAY)
        ORDER BY n.validity ASC LIMIT 20
    """)
    for row in cur.fetchall():
        alerts.append({
            'severity': 'warning', 'category': 'NOC', 'icon': '📋',
            'title': f"NOC Expiring Soon — {row['tracking_id']}",
            'detail': f"{row['applicant_name']} | {row['location']} | Expires: {row['validity']}",
            'time': str(row['validity'])
        })

    cur.execute("""
        SELECT f.follow_up_id, f.follow_up_date, f.reason,
               a.tracking_id, u.name AS applicant_name
        FROM follow_ups f
        JOIN applications a ON f.app_id = a.app_id
        JOIN users u ON a.applicant_id = u.user_id
        WHERE f.status = 'Pending' AND f.follow_up_date < CURDATE()
        ORDER BY f.follow_up_date ASC LIMIT 10
    """)
    for row in cur.fetchall():
        alerts.append({
            'severity': 'info', 'category': 'Follow-up', 'icon': '🔔',
            'title': f"Overdue Follow-up — {row['tracking_id']}",
            'detail': f"{row['applicant_name']} | Due: {row['follow_up_date']} | {row['reason'] or ''}",
            'time': str(row['follow_up_date'])
        })

    cur.close(); db.close()
    severity_order = {'critical': 0, 'warning': 1, 'info': 2}
    alerts.sort(key=lambda x: severity_order.get(x['severity'], 3))
    return jsonify({'alerts': alerts, 'total': len(alerts)})


# ─── Analytics ────────────────────────────────────────────────
@app.route('/officer/analytics')
@officer_required
def officer_analytics():
    db = get_db()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT DATE_FORMAT(MIN(date_submitted), '%b %Y') AS month,
               COUNT(*) AS total,
               SUM(status = 'Approved') AS approved,
               SUM(status = 'Rejected') AS rejected,
               SUM(status = 'Pending')  AS pending
        FROM applications
        WHERE date_submitted >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
        GROUP BY DATE_FORMAT(date_submitted, '%Y-%m')
        ORDER BY MIN(date_submitted) ASC
    """)
    monthly_data = cur.fetchall()

    cur.execute("SELECT type, COUNT(*) AS count FROM applications GROUP BY type ORDER BY count DESC")
    type_breakdown = cur.fetchall()

    cur.execute("SELECT status, COUNT(*) AS count FROM inspections GROUP BY status")
    inspection_stats = cur.fetchall()

    cur.execute("""
        SELECT AVG(DATEDIFF(ah.changed_at, a.date_submitted)) AS avg_days
        FROM application_history ah
        JOIN applications a ON ah.app_id = a.app_id
        WHERE ah.status IN ('Approved', 'Rejected')
    """)
    avg_processing = cur.fetchone()['avg_days'] or 0

    cur.execute("""
        SELECT u.name AS officer_name,
               COUNT(ah.history_id)          AS processed,
               SUM(ah.status = 'Approved')   AS approved,
               SUM(ah.status = 'Rejected')   AS rejected
        FROM application_history ah
        JOIN users u ON ah.changed_by = u.user_id
        WHERE u.role = 'Officer'
        GROUP BY u.user_id, u.name
        ORDER BY processed DESC
    """)
    officer_performance = cur.fetchall()

    cur.execute("SELECT status, COUNT(*) AS count FROM nocs GROUP BY status")
    noc_status = cur.fetchall()

    cur.execute("SELECT status, SUM(amount) AS total, COUNT(*) AS count FROM fee_ledger GROUP BY status")
    fee_summary = cur.fetchall()

    cur.execute("""
        SELECT building_type, AVG(inspection_score) AS avg_score, COUNT(*) AS total
        FROM applications
        WHERE building_type IS NOT NULL AND inspection_score > 0
        GROUP BY building_type ORDER BY avg_score ASC
    """)
    building_risk = cur.fetchall()

    cur.close(); db.close()
    return render_template('officer/analytics.html',
                           monthly_data=monthly_data,
                           type_breakdown=type_breakdown,
                           inspection_stats=inspection_stats,
                           avg_processing=round(float(avg_processing), 1),
                           officer_performance=officer_performance,
                           noc_status=noc_status,
                           fee_summary=fee_summary,
                           building_risk=building_risk)


# ─── Inspections ──────────────────────────────────────────────
@app.route('/officer/inspections')
@officer_required
def officer_inspections():
    db = get_db()
    cur = db.cursor(dictionary=True)
    officer_id    = session['user_id']
    status_filter = request.args.get('status', '')

    query = """
        SELECT i.inspection_id, i.date, i.status, i.remarks,
               a.app_id, a.tracking_id, a.type, a.location,
               a.building_type, a.inspection_score,
               u_ap.name  AS applicant_name,
               u_ap.phone AS applicant_phone,
               u_off.name AS officer_name
        FROM inspections i
        JOIN applications a ON i.app_id    = a.app_id
        JOIN users u_ap  ON a.applicant_id = u_ap.user_id
        JOIN users u_off ON i.officer_id   = u_off.user_id
        WHERE i.officer_id = %s
    """
    params = [officer_id]

    if status_filter:
        query += " AND i.status = %s"
        params.append(status_filter)

    query += " ORDER BY i.date DESC"
    cur.execute(query, params)
    inspections_list = cur.fetchall()

    cur.execute("""
        SELECT i.inspection_id, i.date, i.status,
               a.tracking_id, a.location, a.building_type,
               u.name AS applicant_name, u.phone AS applicant_phone
        FROM inspections i
        JOIN applications a ON i.app_id    = a.app_id
        JOIN users u ON a.applicant_id     = u.user_id
        WHERE i.officer_id = %s AND i.date = CURDATE()
        ORDER BY i.status ASC
    """, (officer_id,))
    todays_schedule = cur.fetchall()

    cur.close(); db.close()
    return render_template('officer/inspections.html',
                           inspections=inspections_list,
                           todays_schedule=todays_schedule,
                           status_filter=status_filter)


# ─── Update Inspection ────────────────────────────────────────
@app.route('/officer/inspections/<int:inspection_id>/update', methods=['POST'])
@officer_required
def update_inspection(inspection_id):
    db = get_db()
    cur = db.cursor()
    officer_id = session['user_id']
    new_status = request.form.get('status')
    remarks    = request.form.get('remarks', '')
    score      = request.form.get('inspection_score')

    cur.execute("""
        UPDATE inspections SET status = %s, remarks = %s
        WHERE inspection_id = %s AND officer_id = %s
    """, (new_status, remarks, inspection_id, officer_id))

    if score:
        cur.execute("""
            UPDATE applications a
            JOIN inspections i ON a.app_id = i.app_id
            SET a.inspection_score = %s
            WHERE i.inspection_id = %s
        """, (score, inspection_id))

    db.commit()
    cur2 = db.cursor(dictionary=True)
    cur2.execute("SELECT app_id FROM inspections WHERE inspection_id=%s", (inspection_id,))
    row = cur2.fetchone()
    cur2.close()
    if row:
        run_ml_on_application(row['app_id'])

    cur.close(); db.close()
    return redirect(url_for('officer_inspections'))

# ── Officer Messages ──────────────────────────────────────────
# Add these two routes inside app.py, after the update_inspection route
# and before the ADMIN ROUTES section

@app.route('/officer/messages')
@officer_required
def officer_messages():
    uid  = session['user_id']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    # Show messages sent TO this officer OR sent by applicants to Admin
    # (so officers can see general inquiries too)
    cur.execute("""
        SELECT m.msg_id, m.sender_id, m.receiver_id, m.subject,
               m.message, m.status, m.created_at,
               u.name AS sender_name
        FROM messages m
        JOIN users u ON m.sender_id = u.user_id
        WHERE m.receiver_id = %s
           OR (m.sender_id = %s)
           OR (m.receiver_id IN (SELECT user_id FROM users WHERE role='Admin')
               AND u.role = 'Applicant')
        ORDER BY m.created_at ASC
    """, (uid, uid))
    messages_list = cur.fetchall()
  
    # Stats
    cur.execute("SELECT COUNT(*) AS n FROM messages WHERE receiver_id=%s AND status='Unread'", (uid,))
    unread = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM messages WHERE sender_id=%s OR receiver_id=%s", (uid, uid))
    total = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM messages WHERE receiver_id=%s AND status='Responded'", (uid,))
    responded = cur.fetchone()['n']

    # Mark all received messages as Responded when officer views them
    cur.execute("""
        UPDATE messages SET status='Responded'
        WHERE receiver_id=%s AND status='Unread'
    """, (uid,))
    conn.commit()

    cur.close(); conn.close()
    return render_template('officer/officer_messages.html',
                           messages_list=messages_list,
                           msg_stats=dict(total=total, unread=unread, responded=responded))


@app.route('/officer/messages/reply', methods=['POST'])
@officer_required
def officer_reply_message():
    uid         = session['user_id']
    receiver_id = request.form.get('receiver_id')
    subject     = request.form.get('subject', '').strip()
    message     = request.form.get('message', '').strip()

    if not message or not receiver_id:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('officer_messages'))

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO messages (sender_id, receiver_id, subject, message, status, created_at)
        VALUES (%s,%s,%s,%s,'Unread',%s)
    """, (uid, receiver_id, subject, message, datetime.now()))
    conn.commit()
    cur.close(); conn.close()
    flash('Reply sent successfully.', 'success')
    return redirect(url_for('officer_messages'))

# ═══════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ═══════════════════════════════════════════════════════════════

# ── Audit helpers ─────────────────────────────────────────────

def log_audit(actor_id, actor, actor_email, actor_role,
              action, entity=None, details=None, ip=None):
    """Write one row to the audit_log table."""
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO audit_log
                (actor_id, actor, actor_email, actor_role,
                 action, entity, details, ip_address, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (actor_id, actor, actor_email, actor_role,
              action, entity, details, ip, datetime.now()))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        print(f"[audit_log error] {e}")


def _audit(action, entity=None, details=None):
    """Convenience wrapper — reads actor from session."""
    log_audit(
        actor_id    = session.get('user_id'),
        actor       = session.get('username', 'Unknown'),   # FIX 9: was 'name', key is 'username'
        actor_email = session.get('email', ''),
        actor_role  = session.get('role', ''),
        action      = action,
        entity      = entity,
        details     = details,
        ip          = request.remote_addr
    )


def get_admin_settings():
    """Return all rows from system_settings as a plain dict."""
    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("SELECT setting_key, setting_value FROM system_settings")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return {r['setting_key']: r['setting_value'] for r in rows}
    except Exception:
        return {}


def save_setting(key, value):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO system_settings (setting_key, setting_value)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
    """, (key, value))
    conn.commit(); cur.close(); conn.close()


# ── Dashboard ─────────────────────────────────────────────────

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='Applicant'")
    total_users = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='Officer'")
    total_officers = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM users WHERE status='Suspended'")
    suspended_users = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE status='Pending'")
    pending_apps = cur.fetchone()['n']

    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status='Paid'")
    revenue_collected = cur.fetchone()['n']

    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status IN ('Pending','Overdue')")
    revenue_pending = cur.fetchone()['n']

    cur.execute("SELECT COUNT(*) AS n FROM nocs WHERE status='Active'")   # FIX 10: noc_certificates → nocs
    nocs_issued = cur.fetchone()['n']

    cutoff = datetime.now() + timedelta(days=30)
    cur.execute("SELECT COUNT(*) AS n FROM nocs WHERE status='Active' AND validity <= %s", (cutoff,))
    nocs_expiring = cur.fetchone()['n']

    stats = dict(
        total_users=total_users, total_officers=total_officers,
        suspended_users=suspended_users, pending_apps=pending_apps,
        revenue_collected=float(revenue_collected),
        revenue_pending=float(revenue_pending),
        nocs_issued=nocs_issued, nocs_expiring=nocs_expiring
    )

    # FIX 3: use applicant_id not user_id
    cur.execute("""
        SELECT a.app_id, a.status, a.type, a.date_submitted,
               u.name AS applicant_name
        FROM applications a
        JOIN users u ON a.applicant_id = u.user_id
        ORDER BY a.date_submitted DESC LIMIT 10
    """)
    recent_apps = cur.fetchall()

    cur.execute("""
        SELECT actor, action, created_at
        FROM audit_log ORDER BY created_at DESC LIMIT 8
    """)
    recent_audit = cur.fetchall()

    cur.close(); conn.close()
    return render_template('admin/admin_dashboard.html',
                           stats=stats,
                           recent_apps=recent_apps,
                           recent_audit=recent_audit,
                           now=datetime.now())


# ── Users ─────────────────────────────────────────────────────

@app.route('/admin/users')
@admin_required
def admin_users():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    # FIX 6: join on applicant_id not user_id
    cur.execute("""
        SELECT u.user_id, u.name, u.email, u.role, u.status, u.created_at,
               COUNT(a.app_id) AS app_count
        FROM users u
        LEFT JOIN applications a ON a.applicant_id = u.user_id
        GROUP BY u.user_id
        ORDER BY u.created_at DESC
    """)
    users = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='Applicant'")
    applicants = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='Officer'")
    officers = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE status='Pending'")
    pending_approval = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM users WHERE status='Suspended'")
    suspended = cur.fetchone()['n']

    cur.close(); conn.close()
    return render_template('admin/admin_users.html',
                           users=users,
                           counts=dict(applicants=applicants,
                                       officers=officers,
                                       pending_approval=pending_approval,
                                       suspended=suspended))


@app.route('/admin/users/add-officer', methods=['POST'])
@admin_required
def admin_add_officer():
    name     = request.form['name'].strip()
    email    = request.form['email'].strip().lower()
    password = request.form['password']
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT user_id FROM users WHERE email=%s", (email,))
    if cur.fetchone():
        flash('An account with that email already exists.', 'error')
    else:
        cur.execute("""
            INSERT INTO users (name, email, password, role, status, created_at)
            VALUES (%s,%s,%s,'Officer','Active',%s)
        """, (name, email, datetime.now()))
        conn.commit()
        _audit('CREATE', 'User', f'Added officer: {email}')
        flash(f'Officer account created for {name}.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/suspend', methods=['POST'])
@admin_required
def admin_suspend_user(user_id):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE users SET status='Suspended' WHERE user_id=%s", (user_id,))
    conn.commit()
    _audit('SUSPEND', 'User', f'user_id={user_id}')
    flash('User suspended.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/activate', methods=['POST'])
@admin_required
def admin_activate_user(user_id):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE users SET status='Active' WHERE user_id=%s", (user_id,))
    conn.commit()
    _audit('UPDATE', 'User', f'Activated user_id={user_id}')
    flash('User activated.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/role', methods=['POST'])
@admin_required
def admin_change_role(user_id):
    role = request.form['role']
    if role not in ('Applicant', 'Officer', 'Admin'):
        flash('Invalid role.', 'error')
        return redirect(url_for('admin_users'))
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE users SET role=%s WHERE user_id=%s", (role, user_id))
    conn.commit()
    _audit('UPDATE', 'User', f'Changed role user_id={user_id} to {role}')
    flash(f'Role updated to {role}.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_users'))


# ── Applications ──────────────────────────────────────────────

@app.route('/admin/applications')
@admin_required
def admin_applications():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    # FIX 3: use applicant_id; FIX 5: use assigned_officer column name
    cur.execute("""
        SELECT a.app_id, a.status, a.type, a.date_submitted, a.location,
               u.name  AS applicant_name,
               u.email AS applicant_email,
               o.name  AS officer_name
        FROM applications a
        JOIN users u ON a.applicant_id    = u.user_id
        LEFT JOIN users o ON a.assigned_officer = o.user_id
        ORDER BY a.date_submitted DESC
    """)
    applications = cur.fetchall()

    # FIX 2: explicit variables instead of locals()[key]
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE status='Pending'")
    pending = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE status='Approved'")
    approved = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE status='Rejected'")
    rejected = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications WHERE status='Scheduled'")
    inspection = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM applications")
    total = cur.fetchone()['n']

    # FIX 5: use assigned_officer column name
    cur.execute("""
        SELECT u.user_id, u.name,
               COUNT(a.app_id) AS active_count
        FROM users u
        LEFT JOIN applications a
               ON a.assigned_officer = u.user_id
              AND a.status IN ('Pending','Scheduled')
        WHERE u.role='Officer' AND u.status='Active'
        GROUP BY u.user_id
        ORDER BY active_count ASC
    """)
    officers = cur.fetchall()

    cur.close(); conn.close()
    return render_template('admin/admin_applications.html',
                           applications=applications,
                           officers=officers,
                           counts=dict(total=total, pending=pending,
                                       approved=approved, rejected=rejected,
                                       inspection=inspection))


@app.route('/admin/applications/<int:app_id>')
@admin_required
def admin_view_application(app_id):
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    # FIX 4: use applicant_id; FIX 5: use assigned_officer
    cur.execute("""
        SELECT a.*, u.name AS applicant_name, u.email AS applicant_email,
               o.name AS officer_name
        FROM applications a
        JOIN users u ON a.applicant_id     = u.user_id
        LEFT JOIN users o ON a.assigned_officer = o.user_id
        WHERE a.app_id = %s
    """, (app_id,))
    app_data = cur.fetchone()
    if not app_data:
        flash('Application not found.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('admin_applications'))

    cur.execute("SELECT * FROM application_documents WHERE app_id=%s", (app_id,))
    docs = cur.fetchall()
    cur.execute("SELECT user_id, name FROM users WHERE role='Officer' AND status='Active' ORDER BY name")
    officers = cur.fetchall()
    cur.close(); conn.close()
    return render_template('admin/admin_view_application.html',
                           app=app_data, docs=docs, officers=officers)


@app.route('/admin/applications/<int:app_id>/assign', methods=['POST'])
@admin_required
def admin_assign_officer(app_id):
    officer_id = request.form.get('officer_id')
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    # FIX 5: use assigned_officer column name (not assigned_officer_id)
    cur.execute("UPDATE applications SET assigned_officer=%s WHERE app_id=%s",
                (officer_id, app_id))
    conn.commit()
    _audit('ASSIGN', 'Application', f'app_id={app_id} to officer_id={officer_id}')
    flash('Officer assigned successfully.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_applications'))


# ── Fee Management ────────────────────────────────────────────

@app.route('/admin/fees')
@admin_required
def admin_fees():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    # FIX 3: use applicant_id
    cur.execute("""
        SELECT f.ledger_id, f.app_id, f.amount, f.status, f.due_date, f.paid_on,
               u.name AS applicant_name
        FROM fee_ledger f
        JOIN applications a ON f.app_id = a.app_id
        JOIN users u ON a.applicant_id = u.user_id
        ORDER BY f.due_date DESC
    """)
    fees = cur.fetchall()

    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status='Paid'")
    collected = float(cur.fetchone()['n'])
    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status IN ('Pending','Overdue')")
    pending = float(cur.fetchone()['n'])
    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status='Waived'")
    waived = float(cur.fetchone()['n'])
    cur.execute("SELECT COUNT(*) AS n FROM fee_ledger")
    total_txns = cur.fetchone()['n']

    cur.close(); conn.close()
    return render_template('admin/admin_fees.html',
                           fees=fees,
                           summary=dict(collected=collected, pending=pending,
                                        waived=waived, total_txns=total_txns))


@app.route('/admin/fees/<int:ledger_id>/mark-paid', methods=['POST'])
@admin_required
def admin_mark_paid(ledger_id):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE fee_ledger SET status='Paid', paid_on=%s WHERE ledger_id=%s",
                (datetime.now(), ledger_id))
    conn.commit()
    _audit('UPDATE', 'FeeLedger', f'Marked paid ledger_id={ledger_id}')  # FIX: typo 'FeeL edger'
    flash('Fee marked as paid.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_fees'))


@app.route('/admin/fees/<int:ledger_id>/waive', methods=['POST'])
@admin_required
def admin_waive_fee(ledger_id):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE fee_ledger SET status='Waived' WHERE ledger_id=%s", (ledger_id,))
    conn.commit()
    _audit('UPDATE', 'FeeLedger', f'Waived ledger_id={ledger_id}')
    flash('Fee waived.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_fees'))


# ── NOC Certificates ──────────────────────────────────────────

@app.route('/admin/nocs')
@admin_required
def admin_nocs():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cutoff = datetime.now() + timedelta(days=30)
    # FIX 10: use nocs table (not noc_certificates), validity (not valid_until)
    cur.execute("""
        SELECT n.noc_id, n.app_id, n.status, n.issue_date, n.validity,
               u.name AS applicant_name,
               o.name AS issued_by,
               (n.validity <= %s AND n.status='Active') AS is_expiring
        FROM nocs n
        JOIN applications a ON n.app_id = a.app_id
        JOIN users u ON a.applicant_id = u.user_id
        LEFT JOIN users o ON a.officer_id = o.user_id
        ORDER BY n.issue_date DESC
    """, (cutoff,))
    nocs = cur.fetchall()

    cur.execute("SELECT COUNT(*) AS n FROM nocs WHERE status='Active'")
    active = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM nocs WHERE status='Active' AND validity<=%s", (cutoff,))
    expiring = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM nocs WHERE status='Expired'")
    expired = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM nocs WHERE status='Revoked'")
    revoked = cur.fetchone()['n']

    cur.close(); conn.close()
    return render_template('admin/admin_nocs.html',
                           nocs=nocs,
                           counts=dict(active=active, expiring=expiring,
                                       expired=expired, revoked=revoked))


@app.route('/admin/nocs/<int:noc_id>/revoke', methods=['POST'])
@admin_required
def admin_revoke_noc(noc_id):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("UPDATE nocs SET status='Revoked' WHERE noc_id=%s", (noc_id,))
    conn.commit()
    _audit('DELETE', 'NOC', f'Revoked noc_id={noc_id}')
    flash('NOC certificate revoked.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_nocs'))


# ── Announcements ─────────────────────────────────────────────

@app.route('/admin/announcements')
@admin_required
def admin_announcements():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT a.ann_id, a.title, a.message, a.audience, a.priority,
               a.created_at, a.recipient_count,
               u.name AS created_by
        FROM announcements a
        JOIN users u ON a.created_by_id = u.user_id
        ORDER BY a.created_at DESC
    """)
    announcements = cur.fetchall()
    cur.close(); conn.close()
    return render_template('admin/admin_announcements.html',
                           announcements=announcements)


@app.route('/admin/announcements/send', methods=['POST'])
@admin_required
def admin_send_announcement():
    title    = request.form['title'].strip()
    message  = request.form['message'].strip()
    audience = request.form.get('audience', 'all')
    priority = request.form.get('priority', 'normal')

    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    if audience == 'all':
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE status='Active'")
    elif audience == 'applicants':
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='Applicant' AND status='Active'")
    else:
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE role='Officer' AND status='Active'")
    recipient_count = cur.fetchone()['n']

    cur.execute("""
        INSERT INTO announcements
            (title, message, audience, priority, created_by_id, recipient_count, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (title, message, audience, priority,
          session['user_id'], recipient_count, datetime.now()))
    ann_id = cur.lastrowid

    if audience == 'all':
        cur.execute("SELECT user_id FROM users WHERE status='Active'")
    elif audience == 'applicants':
        cur.execute("SELECT user_id FROM users WHERE role='Applicant' AND status='Active'")
    else:
        cur.execute("SELECT user_id FROM users WHERE role='Officer' AND status='Active'")

    recipients = cur.fetchall()
    for r in recipients:
        cur.execute("""
            INSERT INTO notifications (user_id, title, message, is_read, created_at)
            VALUES (%s,%s,%s,0,%s)
        """, (r['user_id'], title, message, datetime.now()))

    conn.commit()
    _audit('CREATE', 'Announcement', f'ann_id={ann_id}, audience={audience}, recipients={recipient_count}')
    flash(f'Announcement sent to {recipient_count} users.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_announcements'))


@app.route('/admin/announcements/<int:ann_id>/delete', methods=['POST'])
@admin_required
def admin_delete_announcement(ann_id):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("DELETE FROM announcements WHERE ann_id=%s", (ann_id,))
    conn.commit()
    _audit('DELETE', 'Announcement', f'ann_id={ann_id}')
    flash('Announcement deleted.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_announcements'))


# ── Audit Logs ────────────────────────────────────────────────

@app.route('/admin/audit')
@admin_required
def admin_audit():
    page     = request.args.get('page', 1, type=int)
    per_page = 50
    offset   = (page - 1) * per_page

    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    cur.execute("SELECT COUNT(*) AS n FROM audit_log")
    total = cur.fetchone()['n']

    cur.execute("""
        SELECT * FROM audit_log
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """, (per_page, offset))
    logs = cur.fetchall()

    today      = datetime.now().date()
    week_start = today - timedelta(days=7)

    cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE DATE(created_at)=%s", (today,))
    today_count = cur.fetchone()['n']
    cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE created_at>=%s", (week_start,))
    week_count = cur.fetchone()['n']
    cur.execute("SELECT COUNT(DISTINCT actor_id) AS n FROM audit_log")
    unique_actors = cur.fetchone()['n']

    pages = math.ceil(total / per_page) if total else 1

    class Pag:
        def __init__(self):
            self.page     = page;      self.pages    = pages
            self.has_prev = page > 1;  self.has_next = page < pages
            self.prev_num = page - 1;  self.next_num = page + 1

    cur.close(); conn.close()
    return render_template('admin/admin_audit.html',
                           logs=logs,
                           counts=dict(today=today_count, this_week=week_count,
                                       total=total, unique_actors=unique_actors),
                           pagination=Pag())


@app.route('/admin/audit/export')
@admin_required
def admin_export_audit():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM audit_log ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close(); conn.close()

    if not rows:
        flash('No audit records to export.', 'error')
        return redirect(url_for('admin_audit'))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = \
        f'attachment; filename=audit_log_{datetime.now().strftime("%Y%m%d")}.csv'
    response.headers['Content-Type'] = 'text/csv'
    _audit('EXPORT', 'AuditLog', 'Exported full audit log CSV')
    return response


# ── Reports ───────────────────────────────────────────────────

@app.route('/admin/reports')
@admin_required
def admin_reports():
    conn = get_db()
    cur  = conn.cursor(dictionary=True)

    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    cur.execute("""
        SELECT status, COUNT(*) AS count
        FROM applications
        WHERE date_submitted >= %s
        GROUP BY status
    """, (month_start,))
    raw = cur.fetchall()

    total = sum(r['count'] for r in raw) or 1
    monthly_status = [{**r, 'pct': round(r['count'] / total * 100)} for r in raw]

    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status='Paid'    AND created_at>=%s", (month_start,))
    collected = float(cur.fetchone()['n'])
    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status='Pending' AND created_at>=%s", (month_start,))
    pending   = float(cur.fetchone()['n'])
    cur.execute("SELECT COALESCE(SUM(amount),0) AS n FROM fee_ledger WHERE status='Waived'  AND created_at>=%s", (month_start,))
    waived    = float(cur.fetchone()['n'])

    cur.close(); conn.close()
    return render_template('admin/admin_reports.html',
                           monthly_status=monthly_status,
                           monthly_revenue=dict(collected=collected,
                                                pending=pending,
                                                waived=waived))


@app.route('/admin/reports/export', methods=['POST'])
@admin_required
def admin_export_report():
    report_type = request.form.get('report_type')
    date_range  = int(request.form.get('date_range', 30))
    role_filter = request.form.get('role_filter', 'all')
    stat_filter = request.form.get('status_filter', 'all')

    conn  = get_db()
    cur   = conn.cursor(dictionary=True)
    since = datetime.now() - timedelta(days=date_range)

    if report_type == 'applications':
        # FIX 3 + FIX 5
        cur.execute("""
            SELECT a.app_id, u.name, u.email, a.type, a.status,
                   a.location, a.date_submitted, o.name AS officer
            FROM applications a
            JOIN users u ON a.applicant_id      = u.user_id
            LEFT JOIN users o ON a.assigned_officer = o.user_id
            WHERE a.date_submitted >= %s
            ORDER BY a.date_submitted DESC
        """, (since,))

    elif report_type == 'revenue':
        cur.execute("""
            SELECT f.ledger_id, u.name, a.app_id, f.amount,
                   f.status, f.due_date, f.paid_on
            FROM fee_ledger f
            JOIN applications a ON f.app_id = a.app_id
            JOIN users u ON a.applicant_id  = u.user_id
            WHERE f.created_at >= %s
            ORDER BY f.due_date DESC
        """, (since,))

    elif report_type == 'users':
        q      = "SELECT user_id,name,email,role,status,created_at FROM users"
        params = []
        if role_filter != 'all':
            q += " WHERE role=%s"
            params.append(role_filter)
        q += " ORDER BY created_at DESC"
        cur.execute(q, params)

    elif report_type == 'nocs':
        # FIX 10: nocs table, validity column
        q      = "SELECT * FROM nocs"
        params = []
        if stat_filter != 'all':
            q += " WHERE status=%s"
            params.append(stat_filter)
        q += " ORDER BY issue_date DESC"
        cur.execute(q, params)

    elif report_type == 'audit':
        cur.execute("SELECT * FROM audit_log WHERE created_at>=%s ORDER BY created_at DESC", (since,))

    elif report_type == 'officer_performance':
        cur.execute("""
            SELECT o.name AS officer, o.email,
                   COUNT(a.app_id)             AS total_handled,
                   SUM(a.status='Approved')    AS approved,
                   SUM(a.status='Rejected')    AS rejected,
                   AVG(DATEDIFF(a.date_decided, a.date_submitted)) AS avg_days
            FROM users o
            JOIN applications a ON a.assigned_officer = o.user_id
            WHERE o.role='Officer' AND a.date_submitted>=%s
            GROUP BY o.user_id
            ORDER BY total_handled DESC
        """, (since,))
    else:
        flash('Unknown report type.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('admin_reports'))

    rows = cur.fetchall()
    cur.close(); conn.close()

    if not rows:
        flash('No data found for selected filters.', 'error')
        return redirect(url_for('admin_reports'))

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

    response = make_response(output.getvalue())
    fname = f'{report_type}_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    response.headers['Content-Disposition'] = f'attachment; filename={fname}'
    response.headers['Content-Type'] = 'text/csv'
    _audit('EXPORT', 'Report', f'type={report_type}')
    return response


# ── Settings ──────────────────────────────────────────────────

@app.route('/admin/settings')
@admin_required
def admin_settings():
    settings = get_admin_settings()
    return render_template('admin/admin_settings.html', settings=settings)


@app.route('/admin/settings/save', methods=['POST'])
@admin_required
def admin_save_settings():
    section = request.form.get('section', '')

    if section == 'fees':
        for key in ['fee_noc','fee_inspection','fee_renewal','fee_late','waiver_days']:
            val = request.form.get(key)
            if val is not None:
                save_setting(key, val)

    elif section == 'noc':
        for key in ['noc_validity_months','noc_warn_days','noc_autorevoke','noc_prefix']:
            val = request.form.get(key)
            if val is not None:
                save_setting(key, val)

    elif section == 'notifications':
        for key in ['notif_app_submitted','notif_app_approved','notif_app_rejected',
                    'notif_inspection','notif_noc_expiry','notif_fee_due','notif_officer_assign']:
            save_setting(key, '1' if request.form.get(key) else '0')

    elif section == 'security':
        for key in ['session_timeout','max_login_attempts','lockout_minutes','password_min_len']:
            val = request.form.get(key)
            if val is not None:
                save_setting(key, val)
        save_setting('require_2fa', '1' if request.form.get('require_2fa') else '0')

    _audit('UPDATE', 'Settings', f'Section: {section}')
    flash('Settings saved.', 'success')
    return redirect(url_for('admin_settings'))


@app.route('/admin/settings/clear-old-audit', methods=['POST'])
@admin_required
def admin_clear_old_audit():
    cutoff = datetime.now() - timedelta(days=90)
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("DELETE FROM audit_log WHERE created_at < %s", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    _audit('DELETE', 'AuditLog', f'Cleared {deleted} records older than 90 days')
    flash(f'Deleted {deleted} old audit records.', 'success')
    cur.close(); conn.close()
    return redirect(url_for('admin_settings'))

# ═══════════════════════════════════════════════════════════════
# MACHINE LEARNING
# ═══════════════════════════════════════════════════════════════

def run_ml_on_application(app_id):
    try:
        conn = get_db()
        cur  = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT a.building_type, a.description, a.priority_level,
                   a.inspection_score,
                   COUNT(DISTINCT i.inspection_id)                    AS total_inspections,
                   COALESCE(DATEDIFF(NOW(), MAX(i.date)), 0)          AS days_since_inspection,
                   COALESCE(MAX(ic.overall_score), 0)                 AS checklist_score,
                   COALESCE(MAX(
                       (ic.fire_extinguishers = 0) +
                       (ic.fire_alarm_system  = 0) +
                       (ic.sprinkler_system   = 0) +
                       (ic.smoke_detectors    = 0)), 0)               AS failed_checks,
                   COUNT(CASE WHEN fu.status='Pending' THEN 1 END)    AS pending_followups,
                   MAX(CASE WHEN n.status='Active' THEN 1 ELSE 0 END) AS noc_active,
                   COUNT(ah.history_id)                               AS status_changes
            FROM applications a
            LEFT JOIN inspections i            ON a.app_id = i.app_id
            LEFT JOIN inspection_checklists ic ON a.app_id = ic.app_id
            LEFT JOIN follow_ups fu            ON a.app_id = fu.app_id
            LEFT JOIN nocs n                   ON a.app_id = n.app_id
            LEFT JOIN application_history ah   ON a.app_id = ah.app_id
            WHERE a.app_id = %s
            GROUP BY a.app_id
        """, (app_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return

        # ── Classifier ──────────────────────────────────
        try:
            building = row.get('building_type') or 'Unknown'
            known_buildings = list(classifier_data['le_building'].classes_)
            if building not in known_buildings:
                building = known_buildings[0]
            building_enc = classifier_data['le_building'].transform([building])[0]

            priority = row.get('priority_level') or 'Normal'
            known_priorities = list(classifier_data['le_priority'].classes_)
            if priority not in known_priorities:
                priority = known_priorities[0]
            priority_enc = classifier_data['le_priority'].transform([priority])[0]

            base_features = np.array([[building_enc, priority_enc,
                                       float(row.get('checklist_score') or 0),
                                       0.0]], dtype=float)

            if classifier_data['has_tfidf'] and classifier_data['tfidf'] is not None:
                desc_vec = classifier_data['tfidf'].transform(
                               [str(row.get('description') or '')]).toarray()
                X_cls = np.hstack([base_features, desc_vec])
            else:
                X_cls = base_features

            pred_label = classifier_data['le_type'].inverse_transform(
                             classifier_data['model'].predict(X_cls))[0]
            confidence = round(float(
                             classifier_data['model'].predict_proba(X_cls)[0].max()) * 100, 1)
        except Exception as e:
            print(f"[ML classifier error] app_id={app_id}: {e}")
            pred_label = None
            confidence = None

        # ── Risk Scorer ─────────────────────────────────
        try:
            features = risk_data['features']
            row_values = {
                'total_inspections':     float(row.get('total_inspections')     or 0),
                'days_since_inspection': float(row.get('days_since_inspection') or 0),
                'checklist_score':       float(row.get('checklist_score')       or 0),
                'failed_checks':         float(row.get('failed_checks')         or 0),
                'pending_followups':     float(row.get('pending_followups')     or 0),
                'noc_active':            float(row.get('noc_active')            or 0),
                'status_changes':        float(row.get('status_changes')        or 0),
            }
            X_risk     = np.array([[row_values[f] for f in features]])
            risk_level = risk_data['model'].predict(X_risk)[0]
            risk_score = int(risk_data['model'].predict_proba(X_risk)[0].max() * 100)
        except Exception as e:
            print(f"[ML risk error] app_id={app_id}: {e}")
            risk_level = 'Low'
            risk_score = 0

        # ── Save to DB ───────────────────────────────────
        cur.execute("""
            UPDATE applications
            SET ml_risk_level     = %s,
                ml_risk_score     = %s,
                ml_predicted_type = %s,
                ml_confidence     = %s
            WHERE app_id = %s
        """, (risk_level, risk_score, pred_label, confidence, app_id))
        conn.commit()
        cur.close(); conn.close()
        print(f"[ML] app_id={app_id} → risk={risk_level} ({risk_score}%) | type={pred_label}")

    except Exception as e:
        print(f"[run_ml_on_application error] app_id={app_id}: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

