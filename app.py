import os
import re
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, flash, send_file, abort
import csv
import io
import hmac

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "secret_key_here")

DB_NAME = "applications.db"

# 🔐 مفتاح التصدير (غيّره من متغيرات البيئة في الإنتاج)
EXPORT_KEY = os.environ.get("RECRUIT_EXPORT_KEY", "hani2025")


# ---------- Helpers ----------
ARABIC_DIGITS_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def normalize_sa_mobile(raw: str) -> str:
    """
    Accepts:
      - 05XXXXXXXX
      - 9665XXXXXXXX
      - +9665XXXXXXXX
      - 009665XXXXXXXX
      - with spaces/dashes
    Returns:
      - 9665XXXXXXXX (digits only)
    Raises ValueError if invalid.
    """
    if not raw:
        raise ValueError("empty")

    # convert Arabic-Indic digits -> Western
    raw = raw.translate(ARABIC_DIGITS_MAP)

    # keep digits only
    digits = re.sub(r"\D+", "", raw)

    # handle leading 00
    if digits.startswith("00"):
        digits = digits[2:]

    # convert local 05xxxxxxxx -> 9665xxxxxxxx
    if digits.startswith("05") and len(digits) == 10:
        digits = "966" + digits[1:]  # remove leading 0

    # now must be international
    # Saudi mobile: 9665 + 8 digits => total 12
    if not re.fullmatch(r"9665\d{8}", digits):
        raise ValueError("invalid_format")

    return digits


def now_ksa_naive():
    """
    Returns current time as naive datetime.
    Avoid ZoneInfo to prevent tzdata issues on some Windows setups.
    If your server is in KSA timezone, this matches KSA time.
    """
    return datetime.now()


# ---------- DB ----------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                gender TEXT NOT NULL,
                nationality TEXT NOT NULL,
                other_nationality TEXT,
                dob TEXT,
                region TEXT,
                city TEXT,
                district TEXT,
                education_level TEXT NOT NULL,
                major TEXT,
                employment_status TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                consent INTEGER NOT NULL,
                reg_date TEXT NOT NULL,
                reg_time TEXT NOT NULL
            )
            """
        )
        conn.commit()


init_db()


# ---------- Routes ----------
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", form_data={})


@app.route("/submit", methods=["POST"])
def submit():
    # Read form values
    form_data = {key: request.form.get(key, "").strip() for key in request.form}

    # ✅ Consent must be checked (server-side)
    # Checkbox sends "on" when checked, nothing when not checked
    if not request.form.get("consent"):
        flash("Error !! , you have to approve.⚠️")
        return render_template("index.html", form_data=form_data)

    # ✅ Required fields
    required_fields = [
        "first_name", "last_name", "gender", "nationality",
        "education_level", "employment_status",
        "email", "phone", "summary"
    ]
    for field in required_fields:
        if not form_data.get(field):
            flash("Please fill all requaired feilds. ⚠️")
            return render_template("index.html", form_data=form_data)

    # ✅ Normalize & validate phone, and store normalized value
    try:
        normalized_phone = normalize_sa_mobile(form_data.get("phone"))
        form_data["phone"] = normalized_phone  # keep it in form_data for re-render
    except ValueError:
        flash("⚠️ Please enter your mobile number : 05XXXXXXXX.")
        return render_template("index.html", form_data=form_data)

    email = form_data.get("email").lower()

    # Basic email cleanup (optional)
    form_data["email"] = email

    # If nationality is saudi, clear other_nationality
    if form_data.get("nationality") != "non_saudi":
        form_data["other_nationality"] = ""

    # ✅ Check duplicates
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM applications WHERE email = ? OR phone = ?",
            (email, normalized_phone),
        )
        if c.fetchone():
            flash("⚠️ Mobile number or Email already registered.")
            return render_template("index.html", form_data=form_data)

    # ✅ Registration date/time
    now_dt = now_ksa_naive()
    reg_date = now_dt.strftime("%Y-%m-%d")
    reg_time = now_dt.strftime("%H:%M:%S")  # 24h format

    # ✅ Insert
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO applications (
                    first_name, last_name, gender, nationality, other_nationality,
                    dob, region, city, district, education_level, major,
                    employment_status, email, phone, summary, consent,
                    reg_date, reg_time
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    form_data.get("first_name"),
                    form_data.get("last_name"),
                    form_data.get("gender"),
                    form_data.get("nationality"),
                    form_data.get("other_nationality"),
                    form_data.get("dob"),
                    form_data.get("region"),
                    form_data.get("city"),
                    form_data.get("district"),
                    form_data.get("education_level"),
                    form_data.get("major"),
                    form_data.get("employment_status"),
                    email,
                    normalized_phone,
                    form_data.get("summary"),
                    1,
                    reg_date,
                    reg_time,
                ),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        # In case of race condition duplicate
        flash("⚠️ تعذر حفظ الطلب لأن البريد الإلكتروني أو رقم الجوال مسجل مسبقًا.")
        return render_template("index.html", form_data=form_data)

    flash("✅ تم استلام طلبك بنجاح وحفظ بياناتك.")
    return redirect("/")


@app.route("/export")
def export_data():
    # ✅ Key protection
    key = request.args.get("key")
    if not key or not hmac.compare_digest(key, EXPORT_KEY):
        abort(401, description="غير مصرح لك بالدخول")

    # ✅ Read DB
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id, first_name, last_name, gender, nationality, other_nationality,
                dob, region, city, district, education_level, major,
                employment_status, email, phone, summary, consent,
                reg_date, reg_time
            FROM applications
            ORDER BY id DESC
            """
        )
        rows = cursor.fetchall()

    # ✅ CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "ID",
        "First Name",
        "Last Name",
        "Gender",
        "Nationality",
        "Other Nationality",
        "Date of Birth",
        "Region",
        "City",
        "District",
        "Education Level",
        "Major",
        "Employment Status",
        "Email",
        "Phone (Normalized)",
        "Summary",
        "Consent",
        "Registration Date",
        "Registration Time",
    ])

    writer.writerows(rows)
    output.seek(0)

    filename = f"applications_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


if __name__ == "__main__":
    app.run(debug=True)
