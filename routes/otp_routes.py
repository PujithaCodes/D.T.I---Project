# routes/otp_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from utils.db import get_db_connection
from datetime import datetime, timedelta
import random

otp_bp = Blueprint("otp_bp", __name__)


# ---------------------------------
# OTP GENERATOR
# ---------------------------------
def generate_otp():
    return str(random.randint(100000, 999999))


# ---------------------------------
# REQUEST OTP
# ---------------------------------
@otp_bp.route("/request_otp", methods=["GET", "POST"])
def request_otp():

    if "otp_purpose" not in session:
        flash("Invalid OTP request.", "danger")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        mobile = request.form.get("mobile")
        purpose = session.get("otp_purpose")

        if not mobile:
            flash("Mobile number is required.", "danger")
            return redirect(url_for("otp_bp.request_otp"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT user_id FROM Users WHERE mobile=%s", (mobile,))
        user = cursor.fetchone()

        if purpose in ("login", "reset_password") and not user:
            flash("Mobile number not registered.", "danger")
            return redirect(url_for("otp_bp.request_otp"))
        
        if purpose == "signup" and user:
            flash("Mobile already registered.", "danger")
            return redirect(url_for("otp_bp.request_otp"))
        

        # Check existing OTP
        cursor.execute("""
            SELECT attempts, locked_until, last_sent_at
            FROM OTP_Verification
            WHERE mobile=%s AND purpose=%s
            ORDER BY created_at DESC
            LIMIT 1
        """, (mobile, purpose))

        record = cursor.fetchone()

        if record:
            # Lock check
            if record["locked_until"] and datetime.now() < record["locked_until"]:
                flash("Too many attempts. Try later.", "danger")
                return redirect(url_for("auth.login"))

            # Cooldown check (60 sec)
            if (datetime.now() - record["last_sent_at"]).seconds < 60:
                flash("Please wait before requesting another OTP.", "warning")
                return redirect(url_for("otp_bp.request_otp"))

            # Delete old OTP for this purpose
            cursor.execute("""
                DELETE FROM OTP_Verification
                WHERE mobile=%s AND purpose=%s
            """, (mobile, purpose))

        otp_code = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=1)

        cursor.execute("""
            INSERT INTO OTP_Verification
            (mobile, otp_code, expires_at, attempts, purpose, last_sent_at)
            VALUES (%s, %s, %s, 0, %s, NOW())
        """, (mobile, otp_code, expires_at, purpose))

        conn.commit()
        cursor.close()
        conn.close()

        session["otp_mobile"] = mobile

        flash(f"Your OTP is {otp_code} (valid 1 minute)", "success")
        return redirect(url_for("otp_bp.verify_otp"))

    return render_template("otp/request_otp.html")

@otp_bp.route("/resend_otp", methods=["POST"])
def resend_otp():
    mobile = session.get("otp_mobile")
    purpose = session.get("otp_purpose")

    if not mobile or not purpose:
        flash("Session expired. Please start again.", "warning")
        return redirect(url_for("auth.login"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch latest OTP record
    cursor.execute("""
        SELECT otp_id, last_sent_at, locked_until
        FROM OTP_Verification
        WHERE mobile=%s AND purpose=%s
        ORDER BY created_at DESC
        LIMIT 1
    """, (mobile, purpose))

    record = cursor.fetchone()

    if not record:
        flash("No active OTP session found.", "danger")
        return redirect(url_for("auth.login"))

    # 🔒 Lock check
    if record["locked_until"] and datetime.now() < record["locked_until"]:
        flash("Too many attempts. Try later.", "danger")
        return redirect(url_for("otp_bp.verify_otp"))

    # ⏳ Cooldown check (60 seconds)
    seconds_passed = (datetime.now() - record["last_sent_at"]).seconds
    if seconds_passed < 60:
        remaining = 60 - seconds_passed
        flash(f"Please wait {remaining} seconds before resending OTP.", "warning")
        return redirect(url_for("otp_bp.verify_otp"))

    # Generate new OTP
    otp_code = generate_otp()
    expires_at = datetime.now() + timedelta(minutes=1)

    # Update same row instead of deleting
    cursor.execute("""
        UPDATE OTP_Verification
        SET otp_code=%s,
            expires_at=%s,
            attempts=0,
            last_sent_at=NOW()
        WHERE otp_id=%s
    """, (otp_code, expires_at, record["otp_id"]))

    conn.commit()
    cursor.close()
    conn.close()

    flash(f"New OTP is {otp_code} (valid 1 minute)", "success")
    return redirect(url_for("otp_bp.verify_otp"))

# ---------------------------------
# VERIFY OTP
# ---------------------------------
@otp_bp.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():
    mobile = session.get("otp_mobile")
    purpose = session.get("otp_purpose")

    if not mobile or not purpose:
        flash("OTP session expired. Please start again.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        otp_input = request.form.get("otp")

        if not otp_input:
            flash("OTP is required.", "danger")
            return redirect(url_for("otp_bp.verify_otp"))

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT otp_id, otp_code, expires_at, attempts, locked_until
            FROM OTP_Verification
            WHERE mobile=%s AND purpose=%s
            ORDER BY created_at DESC
            LIMIT 1
        """, (mobile, purpose))

        record = cursor.fetchone()

        if not record:
            flash("Invalid session. Please restart the process.", "danger")
            return redirect(url_for("auth.login"))

        # Lock check
        if record["locked_until"] and datetime.now() < record["locked_until"]:
            flash("Too many incorrect attempts. Try later.", "danger")
            return redirect(url_for("otp_bp.verify_otp"))

        # Expiry check
        if datetime.now() > record["expires_at"]:
            flash("OTP expired. Please resend.", "danger")
            return redirect(url_for("otp_bp.verify_otp"))

        # Wrong OTP
        if otp_input != record["otp_code"]:
            attempts = record["attempts"] + 1

            if attempts >= 5:
                # Delete current OTP after max attempts
                cursor.execute("""
                    DELETE FROM OTP_Verification
                    WHERE otp_id=%s
                """, (record["otp_id"],))

                conn.commit()
                cursor.close()
                conn.close()

                flash("Maximum attempts reached. Please request a new OTP.", "danger")
                return redirect(url_for("otp_bp.request_otp"))

            else:
                cursor.execute("""
                    UPDATE OTP_Verification
                    SET attempts=%s
                    WHERE otp_id=%s
                """, (attempts, record["otp_id"]))

                
                conn.commit()
                cursor.close()
                conn.close()

                flash(f"Incorrect OTP. Attempt {attempts}/5", "danger")
                return redirect(url_for("otp_bp.verify_otp"))
        
        if otp_input == record["otp_code"]:
            cursor.execute("DELETE FROM OTP_Verification WHERE otp_id=%s",
                        (record["otp_id"],))
            conn.commit()

        # -------- PURPOSE HANDLING --------
        if purpose in ("signup", "login"):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE Users SET verified=1 WHERE mobile=%s",
                (mobile,)
            )
            conn.commit()
            cursor.close()
            conn.close()

            session.pop("otp_mobile", None)
            session.pop("otp_purpose", None)

            flash("Mobile verified successfully. Please login.", "success")
            return redirect(url_for("auth.login"))

        if purpose == "reset_password":
            session["otp_verified"] = True
            return redirect(url_for("otp_bp.change_password"))

        session.clear()
        flash("Invalid OTP flow.", "danger")
        return redirect(url_for("auth.login"))

    return render_template("otp/verify_otp.html", mobile=mobile)


# ---------------------------------
# CHANGE PASSWORD (OTP VERIFIED)
# ---------------------------------
@otp_bp.route("/change_password", methods=["GET", "POST"])
def change_password():
    if session.get("otp_purpose") != "reset_password" or not session.get("otp_verified"):
        flash("Unauthorized access.", "danger")
        return redirect(url_for("auth.login"))

    mobile = session.get("otp_mobile")

    if request.method == "POST":
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not new_password or not confirm_password:
            flash("All fields are required.", "danger")
            return redirect(url_for("otp_bp.change_password"))

        if new_password != confirm_password:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("otp_bp.change_password"))

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE Users SET password=%s WHERE mobile=%s",
            (new_password, mobile)
        )

        conn.commit()
        cursor.close()
        conn.close()

        session.clear()

        flash("Password updated successfully. Please login.", "success")
        return redirect(url_for("auth.login"))

    return render_template("otp/change_password.html")