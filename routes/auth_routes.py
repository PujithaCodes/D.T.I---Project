# routes/auth_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from utils.db import get_db_connection
from utils.auth import login_required
from routes.otp_routes import generate_otp
from datetime import datetime, timedelta


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


# ---------------------------------
# ENTRY
# ---------------------------------
@auth_bp.route("/")
def entry():
    return redirect(url_for("auth.login"))


# ---------------------------------
# SIGNUP
# ---------------------------------
@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name")
        mobile = request.form.get("mobile")
        password = request.form.get("password")
        state_id = request.form.get("state_id")
        city_id = request.form.get("city_id")
        ward_id = request.form.get("ward_id")
        assisted = request.form.get("assisted_signup") == "on"

        if not all([name, mobile, password]):
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("auth.signup"))
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT user_id FROM Users WHERE mobile=%s", (mobile,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            flash("Mobile no. already registered. Please verify.", "warning")
            return redirect(url_for("otp_bp.request_otp"))

        cursor.execute("""
            INSERT INTO Users
            (name, mobile, password, role, state_id, city_id, ward_id, verified, assisted_signup)
            VALUES (%s,%s,%s,'citizen',%s,%s,%s,0,%s)
        """, (name, mobile, password, state_id, city_id, ward_id, assisted))

        conn.commit()
        cursor.close()
        conn.close()

        # OTP intent
        session["otp_mobile"] = mobile
        session["otp_purpose"] = "signup"

        flash("Signup successful. Verify your mobile number.", "success")
        otp_code = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=1)

        conn = get_db_connection()
        cursor = conn.cursor()

        # enforce single active OTP
        cursor.execute(
            "DELETE FROM OTP_Verification WHERE mobile=%s",
            (mobile,)
        )

        cursor.execute("""
            INSERT INTO OTP_Verification (mobile, otp_code, expires_at)
            VALUES (%s, %s, %s)
        """, (mobile, otp_code, expires_at))

        conn.commit()
        cursor.close()
        conn.close()
            
        flash(f"Your OTP is {otp_code} (valid for 1 minute)", "success")        
        return redirect(url_for("otp_bp.verify_otp"))

    return render_template("otp/signup.html")


# ---------------------------------
# LOGIN
# ---------------------------------
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    mobile = request.form.get("mobile")
    password = request.form.get("password")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Users WHERE mobile=%s", (mobile,))
    user = cursor.fetchone()

    if not user:
        flash("Invalid mobile number or password.", "danger")
        return redirect(url_for("auth.login"))

    # 🔒 Check if locked
    if user["lock_until"] and datetime.now() < user["lock_until"]:
        flash("Account locked. Try again later.", "danger")
        return redirect(url_for("auth.login"))

    # ❌ Wrong password
    if user["password"] != password:
        attempts = user["login_attempts"] + 1

        if attempts >= 3:
            lock_time = datetime.now() + timedelta(minutes=5)

            cursor.execute("""
                UPDATE Users
                SET login_attempts=%s, lock_until=%s
                WHERE user_id=%s
            """, (attempts, lock_time, user["user_id"]))

            conn.commit()
            flash("Too many failed attempts. Locked for 5 minutes.", "danger")
        else:
            cursor.execute("""
                UPDATE Users
                SET login_attempts=%s
                WHERE user_id=%s
            """, (attempts, user["user_id"]))

            conn.commit()
            remaining = 3 - attempts
            flash(f"Invalid password. {remaining} attempts left.", "warning")

        cursor.close()
        conn.close()
        return redirect(url_for("auth.login"))

    # ❌ Not verified
    if not user["verified"]:
        flash("Account not verified.", "warning")
        cursor.close()
        conn.close()
        return redirect(url_for("auth.login"))

    # ✅ SUCCESS LOGIN
    cursor.execute("""
        UPDATE Users
        SET login_attempts=0, lock_until=NULL
        WHERE user_id=%s
    """, (user["user_id"],))
    conn.commit()

    session.clear()
    session["user_id"] = user["user_id"]
    session["role"] = user["role"]

    cursor.close()
    conn.close()

    flash("Login successful.", "success")
    return redirect(url_for("dashboard.dashboard"))


# ---------------------------------
# LOGOUT
# ---------------------------------
@auth_bp.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("auth.login"))


# ---------------------------------
# FORGOT PASSWORD (ENTRY)
# ---------------------------------
@auth_bp.route("/forgot_password")
def forgot_password():
    session.clear()
    session["otp_purpose"] = "reset_password"
    return redirect(url_for("otp_bp.request_otp"))


# ---------------------------------
# PROFILE PASSWORD RESET (LOGGED IN)
# ---------------------------------
@auth_bp.route("/profile/reset_password", methods=["GET", "POST"])
@login_required
def profile_reset_password():
    user_id = session["user_id"]

    if request.method == "POST":
        current_password = request.form.get("current_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not all([current_password, new_password, confirm_password]):
            flash("All fields are required.", "danger")
            return redirect(url_for("auth.profile_reset_password"))

        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("auth.profile_reset_password"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT password FROM Users WHERE user_id=%s",
            (user_id,)
        )
        user = cursor.fetchone()

        if not user or user["password"] != current_password:
            cursor.close()
            conn.close()
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("auth.profile_reset_password"))
        
        if new_password == current_password:
            cursor.close()
            conn.close()
            flash("New password must be different.", "danger")
            return redirect(url_for("auth.profile_reset_password"))

        cursor.execute(
            "UPDATE Users SET password=%s WHERE user_id=%s",
            (new_password, user_id)
        )

        conn.commit()
        cursor.close()
        conn.close()

        session.clear()
        flash("Password updated successfully. Please login again.", "success")
        return redirect(url_for("auth.login"))

    return render_template("profile/reset_password.html")
