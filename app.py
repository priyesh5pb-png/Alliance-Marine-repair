# app.py
import os
import io
import re
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for, session, flash,
    jsonify, send_file
)
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
import pandas as pd

# PDF libs
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle

# Excel styling
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# ---------------- App config ----------------
app = Flask(__name__)
app.secret_key = "secret_key"   # change in production!
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

# ---------------- Models ----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="user")  # "user" or "admin"

class Tariff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    dimensions = db.Column(db.String(200))  # optional
    mat_cost = db.Column(db.Float, nullable=False)

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    container_no = db.Column(db.String(50), nullable=False)
    line = db.Column(db.String(50), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)  # pdf / excel
    file_path = db.Column(db.String(200), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# ---------------- Utilities ----------------
def safe_filename(name: str):
    if not name:
        return "estimation"
    name = str(name).strip()
    # Replace invalid filename characters with underscore
    return re.sub(r'[^A-Za-z0-9_\-\.]', "_", name)

def ensure_user_dir(username):
    base_dir = os.path.join(os.getcwd(), "reports")
    user_dir = os.path.join(base_dir, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

# ---------------- CLI: import tariffs from Excel ----------------
@app.cli.command("import-tariffs")
def import_tariffs():
    """Import Tarrifs_repairs.xlsx into Tariff table (one-time)."""
    excel_path = os.path.join(os.path.dirname(__file__), "Tarrifs_repairs.xlsx")
    if not os.path.exists(excel_path):
        print(f"❌ Excel file not found at {excel_path}")
        return

    df = pd.read_excel(excel_path).fillna("")

    # normalize column names
    df.columns = [c.strip().lower() for c in df.columns]

    col_map = {"category": None, "description": None, "mat cost": None, "dimensions": None}
    for col in df.columns:
        if "category" in col:
            col_map["category"] = col
        elif "desc" in col:
            col_map["description"] = col
        elif "mat" in col and "cost" in col:
            col_map["mat cost"] = col
        elif "dim" in col:
            col_map["dimensions"] = col

    if not col_map["category"] or not col_map["description"] or not col_map["mat cost"]:
        print("❌ Could not detect required columns (Category, Description, MAT COST).")
        print("Columns found:", df.columns.tolist())
        return

    # clear existing
    Tariff.query.delete()
    db.session.commit()

    for _, row in df.iterrows():
        try:
            mat_val = float(str(row[col_map["mat cost"]]).replace(",", "").strip() or 0)
        except:
            mat_val = 0.0
        t = Tariff(
            category=str(row[col_map["category"]]).strip(),
            description=str(row[col_map["description"]]).strip(),
            dimensions=str(row[col_map["dimensions"]]).strip() if col_map["dimensions"] else "",
            mat_cost=mat_val
        )
        db.session.add(t)
    db.session.commit()

    print("✅ Tariffs imported into DB.")
    print("\n🔎 Preview (first 5 rows):")
    preview = Tariff.query.limit(5).all()
    for p in preview:
        print(f"- {p.category} | {p.description} | {p.dimensions or '(no dim)'} | {p.mat_cost}")

# ---------------- AUTH ----------------
@app.route("/", methods=["GET", "POST"])
@app.route("/login_register", methods=["GET", "POST"])
def login_register():
    if request.method == "POST":
        action = request.form.get("action")
        username = request.form.get("username").strip()
        password = request.form.get("password")

        if action == "register":
            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                flash("Username already exists!", "error")
            else:
                hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
                new_user = User(username=username, password=hashed_pw)
                db.session.add(new_user)
                db.session.commit()
                session["user"] = username
                flash("Registered successfully! Logged in.", "success")
                return redirect(url_for("dashboard"))

        elif action == "login":
            user = User.query.filter_by(username=username).first()
            if user and bcrypt.check_password_hash(user.password, password):
                session["user"] = username
                flash("Login successful!", "success")
                return redirect(url_for("dashboard"))
            else:
                flash("Invalid credentials!", "error")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out successfully!", "info")
    return redirect(url_for("login_register"))

# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login_register"))

    username = session["user"]
    user = User.query.filter_by(username=username).first()

    # ✅ Admin Dashboard
    if user.role == "admin":
        total_reports = Report.query.count()

        # Reports generated this month
        now = datetime.now()
        reports_this_month = Report.query.filter(
            db.extract("month", Report.timestamp) == now.month,
            db.extract("year", Report.timestamp) == now.year
        ).count()

        # Breakdown by line → convert Row objects to plain list
        by_line = db.session.query(Report.line, db.func.count()).group_by(Report.line).all()
        by_line_data = [[row[0], row[1]] for row in by_line]

        return render_template("dashboard_admin.html",
                               username=username,
                               total_reports=total_reports,
                               reports_this_month=reports_this_month,
                               by_line=by_line_data)

    # ✅ Normal User Dashboard
    else:
        report_count = Report.query.filter_by(username=username).count()
        return render_template("dashboard.html",
                               username=username,
                               report_count=report_count)

# ---------------- ESTIMATION (PAGE 2) ----------------
@app.route("/estimation", methods=["GET", "POST"])
def estimation():
    if "user" not in session:
        return redirect(url_for("login_register"))

    if request.method == "POST":
        container_no = request.form.get("container_no").strip()
        in_date = request.form.get("in_date")
        mfg_date = request.form.get("mfg_date")
        line = request.form.get("line").strip()
        size = request.form.get("size").strip()
        gw = request.form.get("gw")
        tw = request.form.get("tw")
        csc = request.form.get("csc").strip()

        errors = []

        try:
            in_date_dt = datetime.strptime(in_date, "%Y-%m-%d")
            mfg_date_dt = datetime.strptime(mfg_date, "%Y-%m-%d")
            if mfg_date_dt >= in_date_dt:
                errors.append("Manufacturing Date must be earlier than In Date.")
        except:
            errors.append("Invalid date format.")

        try:
            gw_val = float(gw)
            tw_val = float(tw)
            if gw_val <= tw_val:
                errors.append("Gross Weight must be greater than Tare Weight.")
        except:
            errors.append("GW and TW must be numeric.")

        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("estimation"))

        session["estimation_data"] = {
            "container_no": container_no,
            "in_date": in_date,
            "mfg_date": mfg_date,
            "line": line,
            "size": size,
            "gw": gw,
            "tw": tw,
            "csc": csc,
        }
        flash("Container details saved successfully!", "success")
        return redirect(url_for("page3"))

    return render_template("estimation.html", username=session["user"])

# ---------------- PAGE 3 ----------------
@app.route("/page3")
def page3():
    if "estimation_data" not in session:
        return redirect(url_for("estimation"))
    categories = [c[0] for c in db.session.query(Tariff.category).distinct().all()]
    categories.sort()
    return render_template("page3.html",
                           username=session["user"],
                           container=session["estimation_data"],
                           categories=categories)

def get_storage_base():
    """
    Return a base folder for storing exported files.
    Preference order:
      1. environment variable FILE_STORAGE_PATH (configurable)
      2. Render persistent disk mount (/mnt/data) if available
      3. /tmp/reports (writable on Render free tier until restart)
      4. local './reports' folder for dev
    """
    # 1) explicit override (good for production)
    path = os.getenv("FILE_STORAGE_PATH")
    if path:
        return path

    # 2) Render persistent disk (recommended if you've added a disk in Render dashboard)
    if os.path.isdir("/mnt/data"):
        return os.path.join("/mnt/data", "reports")

    # 3) Render free-tier tmp
    if os.path.isdir("/tmp"):
        return os.path.join("/tmp", "reports")

    # 4) fallback to local
    return os.path.join(os.getcwd(), "reports")

def ensure_user_dir(username):
    base = get_storage_base()
    user_dir = os.path.join(base, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

from flask import send_from_directory, abort

@app.route("/preview/<int:report_id>")
def preview_report(report_id):
    report = Report.query.get_or_404(report_id)
    if not report.file_path:
        flash("No file attached for this report.", "error")
        return redirect(url_for("reports"))

    directory = os.path.dirname(report.file_path)
    filename = os.path.basename(report.file_path)

    # Security: ensure file is inside allowed base path
    base = os.path.abspath(get_storage_base())
    file_abspath = os.path.abspath(report.file_path)
    if not file_abspath.startswith(base):
        abort(403)

    if not os.path.exists(file_abspath):
        flash("File not found on server.", "error")
        return redirect(url_for("reports"))

    return send_from_directory(directory, filename)

# ---------------- Reports Page ----------------
@app.route("/reports")
def reports():
    if "user" not in session:
        return redirect(url_for("login_register"))

    username = session["user"]
    user = User.query.filter_by(username=username).first()

    if user.role == "admin":
        reports = Report.query.order_by(Report.timestamp.desc()).all()
        users = [u.username for u in User.query.all()]
    else:
        reports = Report.query.filter_by(username=username).order_by(Report.timestamp.desc()).all()
        users = []

    return render_template("reports.html", 
                           username=username, 
                           reports=reports, 
                           total_reports=len(reports),
                           is_admin=(user.role == "admin"),
                           users=users)

# @app.route("/preview/<int:report_id>")
# def preview_report(report_id):
    report = Report.query.get_or_404(report_id)
    try:
        # Build absolute path safely
        file_path = os.path.abspath(report.file_path)

        # Check file exists
        if not os.path.exists(file_path):
            flash("File not found on server.", "error")
            return redirect(url_for("reports"))

        # Return file inline for browser preview
        if report.file_type == "pdf":
            return send_file(file_path, mimetype="application/pdf")
        elif report.file_type == "excel":
            # Browsers can’t preview Excel → force download instead
            return send_file(file_path, as_attachment=True)
        else:
            flash("Unsupported file type.", "error")
            return redirect(url_for("reports"))

    except Exception as e:
        print("Preview error:", e)
        flash("Could not open file.", "error")
        return redirect(url_for("reports")) 

@app.route("/export_reports_excel")
def export_reports_excel():
    if "user" not in session:
        return redirect(url_for("login_register"))

    username = session["user"]
    user = User.query.filter_by(username=username).first()

    # ✅ Admin sees ALL reports
    if user.role == "admin":
        reports = Report.query.order_by(Report.timestamp.desc()).all()
    else:
        # ✅ Normal user only their reports
        reports = Report.query.filter_by(username=username).order_by(Report.timestamp.desc()).all()

    # Create DataFrame
    if not reports:
        flash("No reports to export.", "info")
        return redirect(url_for("reports"))

    data = []
    for r in reports:
        data.append({
            "Username": r.username,
            "Container No": r.container_no,
            "Line": r.line,
            "File Type": r.file_type.upper(),
            "Date Generated": r.timestamp.strftime("%Y-%m-%d %H:%M"),
            "File Path": r.file_path
        })

    df = pd.DataFrame(data)

    # Create Excel in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Reports")
        ws = writer.sheets["Reports"]

        # Apply neat styling
        from openpyxl.styles import Alignment, Font
        for col in ws.columns:
            max_length = 0
            col_letter = col[0].column_letter
            for cell in col:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max_length + 4, 40)

        # Header bold
        for cell in ws[1]:
            cell.font = Font(bold=True)

    output.seek(0)

    filename = f"All_Reports_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx" if user.role == "admin" else f"{username}_Reports.xlsx"

    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
# ---------------- API Endpoints ----------------
@app.route("/api/descriptions")
def api_descriptions():
    category = request.args.get("category", "").strip()
    if not category:
        return jsonify([])

    rows = Tariff.query.filter_by(category=category).all()
    descriptions = {}
    for r in rows:
        desc = r.description
        if desc not in descriptions:
            descriptions[desc] = []
        if r.dimensions and r.dimensions.strip():
            descriptions[desc].append(r.dimensions)

    out = []
    for desc, dims in descriptions.items():
        out.append({
            "description": desc,
            "has_dimensions": len(dims) > 0,
            "dimensions": sorted(list(set(dims)))
        })
    return jsonify(out)

@app.route("/api/matcost")
def api_matcost():
    category = request.args.get("category", "").strip()
    description = request.args.get("description", "").strip()
    dimension = request.args.get("dimension", "").strip()

    query = Tariff.query.filter_by(category=category, description=description)

    # EVERY ADDITIONAL (special)
    if description.upper().startswith("EVERY ADDITIONAL"):
        row = query.first()
        if not row:
            return jsonify({"mat_cost": 0.0})
        try:
            user_val = float(dimension)
            return jsonify({"mat_cost": round(user_val * row.mat_cost, 2)})
        except:
            return jsonify({"mat_cost": 0.0})

    # If dimension provided, find exact match row
    if dimension:
        row = query.filter_by(dimensions=dimension).first()
        if row:
            return jsonify({"mat_cost": row.mat_cost})

    # fallback to first match
    row = query.first()
    return jsonify({"mat_cost": row.mat_cost if row else 0.0})

# ---------------- Exports (Excel + PDF) ----------------
@app.route("/export_excel", methods=["POST"])
def export_excel():
    payload = request.get_json()
    if payload is None:
        return jsonify({"error": "No data provided"}), 400

    entries = payload.get("entries") if isinstance(payload, dict) else payload
    container_info = payload.get("container") if isinstance(payload, dict) else {}
    if not container_info:
        container_info = session.get("estimation_data", {})

    username = session.get("user", "anonymous")
    container_no_raw = container_info.get("container_no") or "estimation"
    line = container_info.get("line", "N/A")
    container_no = safe_filename(container_no_raw)

    # normalize numbers and add SR
    for i, row in enumerate(entries, start=1):
        row["sr"] = i
        row["mat_cost"] = round(float(row.get("mat_cost", 0) or 0), 2)
        row["lab_cost"] = round(float(row.get("lab_cost", 0) or 0), 2)
        row["total"] = round(float(row.get("total", 0) or 0), 2)

    df = pd.DataFrame(entries)
    total_mat = df["mat_cost"].sum() if not df.empty else 0.0
    total_lab = df["lab_cost"].sum() if not df.empty else 0.0
    total_all = df["total"].sum() if not df.empty else 0.0

    totals_row = {
        "sr": "",
        "category": "GRAND TOTAL",
        "description": "",
        "dimension": "",
        "mat_cost": total_mat,
        "man_hrs": "",
        "lab_cost": total_lab,
        "total": total_all,
    }
    if not df.empty:
        df = pd.concat([df, pd.DataFrame([totals_row])], ignore_index=True)
    else:
        df = pd.DataFrame([totals_row])

    # prepare container info table
    container_headers = ["Container No", "In Date", "Mfg Date", "Line", "Size", "GW", "TW", "CSC"]
    container_data = [[
        container_info.get("container_no", ""),
        container_info.get("in_date", ""),
        container_info.get("mfg_date", ""),
        container_info.get("line", ""),
        container_info.get("size", ""),
        container_info.get("gw", ""),
        container_info.get("tw", ""),
        container_info.get("csc", "")
    ]]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        wb = writer.book

        # safe remove default sheet (if exists)
        if "Sheet" in wb.sheetnames:
            wb.remove(wb["Sheet"])

        # create Report sheet and make it active (prevents "at least one sheet" errors)
        ws = wb.create_sheet(title="Report")
        wb.active = wb.sheetnames.index("Report")

        from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.cell.cell import MergedCell

        thin = Side(border_style="thin", color="000000")
        border = Border(top=thin, left=thin, right=thin, bottom=thin)
        header_fill = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")

        # ---- Header ----
        ws.merge_cells("A1:H1")
        ws["A1"] = "ALLIANCE MARINE TERMINAL"
        ws["A1"].font = Font(bold=True, size=16)
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("A2:H2")
        ws["A2"] = "At.Post-Jasai, Tel-Uran, Dist- Raigad Opp Indian Oil Petrol Pump, Navi Mumbai Pin-400 702"
        ws["A2"].font = Font(bold=True, size=11)
        ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

        # ---- Container Info Table ----
        start_row = 4
        for col_num, header in enumerate(container_headers, 1):
            cell = ws.cell(row=start_row, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.fill = header_fill
            cell.border = border

        for row_idx, row_data in enumerate(container_data, start=start_row + 1):
            for col_num, value in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_num, value=value)
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = border

        # ---- Repair Table ----
        repair_start = start_row + len(container_data) + 3
        headers = ["SR", "Category", "Description", "Dimension", "MAT.COST", "MAN.HRS", "LAB.COST", "TOTAL"]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row=repair_start, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.fill = header_fill
            cell.border = border

        # write entries
        for idx, row in enumerate(df.itertuples(index=False), start=repair_start + 1):
            ws.cell(row=idx, column=1, value=getattr(row, "sr", ""))             # SR
            ws.cell(row=idx, column=2, value=getattr(row, "category", ""))       # Category
            ws.cell(row=idx, column=3, value=getattr(row, "description", ""))    # Description
            ws.cell(row=idx, column=4, value=getattr(row, "dimension", ""))      # Dimension
            ws.cell(row=idx, column=5, value=getattr(row, "mat_cost", ""))       # MAT COST
            ws.cell(row=idx, column=6, value=getattr(row, "man_hrs", ""))        # MAN HRS
            ws.cell(row=idx, column=7, value=getattr(row, "lab_cost", ""))       # LAB COST
            ws.cell(row=idx, column=8, value=getattr(row, "total", ""))          # TOTAL

            for c in range(1, 9):
                cell = ws.cell(row=idx, column=c)
                # skip formatting writable operations on MergeCell placeholders
                if not isinstance(cell, MergedCell):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = border

        # style totals row
        total_row = repair_start + len(df.index)
        for c in range(1, 9):
            cell = ws.cell(row=total_row, column=c)
            cell.font = Font(bold=True)
            cell.fill = header_fill
            # center totals where applicable
            if not isinstance(cell, MergedCell):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border

        # ---- Approved Amount ----
        approved_row = total_row + 3
        ws.merge_cells(f"A{approved_row}:G{approved_row}")
        ws[f"A{approved_row}"] = "Approved Amount:"
        ws[f"A{approved_row}"].font = Font(bold=True)
        ws[f"A{approved_row}"].alignment = Alignment(horizontal="right", vertical="center")
        ws[f"A{approved_row}"].border = border

        ws[f"H{approved_row}"].alignment = Alignment(horizontal="center", vertical="center")
        ws[f"H{approved_row}"].border = border

        # ---- Auto-fit columns safely (skip MergedCell objects) ----
        for col_idx in range(1, ws.max_column + 1):
            col_letter = get_column_letter(col_idx)
            max_length = 0
            for cell in ws[col_letter]:
                # skip merged placeholders
                if isinstance(cell, MergedCell):
                    continue
                if cell.value is not None:
                    vlen = len(str(cell.value))
                    if vlen > max_length:
                        max_length = vlen
                    # set alignment for non-merged cells (already set above but safe to ensure)
                    cell.alignment = Alignment(horizontal="center", vertical="center")
            # set width (min 10, cap at 50)
            ws.column_dimensions[col_letter].width = max(10, min(max_length + 4, 50))

    output.seek(0)

    # save + log in DB
    user_dir = ensure_user_dir(username)
    safe_line = safe_filename(line or "LINE")
    file_path = os.path.join(user_dir, f"{safe_line}_{container_no}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
    with open(file_path, "wb") as f:
        f.write(output.getvalue())

    report = Report(username=username, container_no=container_no_raw, line=line,
                    file_type="excel", file_path=file_path)
    db.session.add(report)
    db.session.commit()

    output.seek(0)
    return send_file(output,
                     as_attachment=True,
                     download_name=f"{safe_line}_{container_no}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    
@app.route("/export_pdf", methods=["POST"])
def export_pdf():
    payload = request.get_json()
    if payload is None:
        return jsonify({"error": "No data provided"}), 400

    entries = payload.get("entries") if isinstance(payload, dict) else payload
    container_info = payload.get("container") if isinstance(payload, dict) else {}
    if not container_info:
        container_info = session.get("estimation_data", {})

    username = session.get("user", "anonymous")
    container_no_raw = container_info.get("container_no") or "estimation"
    line = container_info.get("line", "N/A")
    container_no = safe_filename(container_no_raw)

    styles = getSampleStyleSheet()
    centered = ParagraphStyle(name="centered", parent=styles['Normal'], alignment=TA_CENTER)

    # Container info table
    info_headers = ["Container No", "In Date", "Mfg Date", "Line", "Size", "GW", "TW", "CSC"]
    info_values = []
    for key in ["container_no", "in_date", "mfg_date", "line", "size", "gw", "tw", "csc"]:
        val = container_info.get(key, "")
        if key == "container_no":
            info_values.append(Paragraph(f"<b>{val}</b>", centered))
        else:
            info_values.append(Paragraph(str(val), centered))
    info_table = [info_headers, info_values]

    # Estimations table
    headers = ["Category", "Description", "Dimension", "MAT.COST", "MAN.HRS", "LAB.COST", "TOTAL"]
    table_data = [headers]
    total_sum = total_mat = total_lab = 0.0
    for row in entries:
        mat_cost = float(row.get("mat_cost", 0) or 0)
        lab_cost = float(row.get("lab_cost", 0) or 0)
        man_hrs = row.get("man_hrs", "")
        total = mat_cost + lab_cost
        total_mat += mat_cost
        total_lab += lab_cost
        total_sum += total
        table_data.append([
            Paragraph(row.get("category", ""), styles['Normal']),
            Paragraph(row.get("description", ""), styles['Normal']),
            Paragraph(str(row.get("dimension", "")), styles['Normal']),
            f"{mat_cost:.2f}",
            str(man_hrs),
            f"{lab_cost:.2f}",
            f"{total:.2f}"
        ])

    table_data.append([
        Paragraph("<b>GRAND TOTAL</b>", styles['Normal']), "", "",
        f"{total_mat:.2f}", "",
        f"{total_lab:.2f}",
        f"{total_sum:.2f}"
    ])

    # Build PDF
    buffer = io.BytesIO()
    page_w, page_h = landscape(A4)
    leftMargin, rightMargin, topMargin, bottomMargin = 40, 40, 80, 60
    usable_width = page_w - leftMargin - rightMargin

    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=leftMargin, rightMargin=rightMargin,
                            topMargin=topMargin, bottomMargin=bottomMargin)
    elems = []

    elems.append(Paragraph("<para align='center'><b>ALLIANCE MARINE TERMINAL</b></para>", styles['Title']))
    elems.append(Paragraph(
        "<para align='center'><b>At.Post-Jasai, Tel-Uran, Dist- Raigad Opp Indian Oil Petrol Pump, Navi Mumbai Pin-400 702</b></para>",
        styles['Heading4']
    ))
    elems.append(Spacer(1, 30))

    # Container info table
    colw_info = [usable_width / len(info_headers)] * len(info_headers)
    info_tbl = Table(info_table, colWidths=colw_info, hAlign='CENTER')
    info_tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#f2f2f2")),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    elems.append(info_tbl)
    elems.append(Spacer(1, 24))

    # Estimations table
    colw_est = [
        usable_width * 0.12, usable_width * 0.38, usable_width * 0.10,
        usable_width * 0.10, usable_width * 0.08, usable_width * 0.10,
        usable_width * 0.12
    ]
    tbl = Table(table_data, colWidths=colw_est, repeatRows=1, hAlign='CENTER')
    tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#d9e6f6")),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#f2f2f2")),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
    ]))
    elems.append(tbl)

    # Approved Amount
    elems.append(Spacer(1, 24))
    approved_tbl = Table([[Paragraph("<b>Approved Amount</b>", styles['Normal']), ""]],
                         colWidths=[usable_width * 0.3, usable_width * 0.7], hAlign='CENTER')
    approved_tbl.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    elems.append(approved_tbl)

    doc.build(elems)

    # Save + log
    user_dir = ensure_user_dir(username)
    file_path = os.path.join(user_dir, f"{container_no}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
    with open(file_path, "wb") as f:
        f.write(buffer.getvalue())

    report = Report(username=username, container_no=container_no_raw, line=line,
                    file_type="pdf", file_path=file_path)
    db.session.add(report)
    db.session.commit()

    safe_line = safe_filename(line or "LINE")
    buffer.seek(0)
    return send_file(buffer,
                 as_attachment=True,
                 download_name=f"{safe_line}_{container_no}.pdf",
                 mimetype="application/pdf")


# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
