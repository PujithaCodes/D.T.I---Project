# routes/admin_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from utils.db import get_db_connection
from utils.auth import login_required, role_required

ROLE_PRIORITY = {
    "super_admin": 7,
    "state_admin": 6,
    "municipal_admin": 5,
    "department_admin": 4,
    "field_staff": 3,
    "facilitator": 2,
    "citizen": 1
}


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# ========================
# FILTER USERS (AJAX)
# ========================
@admin_bp.route("/users/filter")
@login_required
@role_required("super_admin", "state_admin", "municipal_admin")
def filter_users():

    current_user_id = session["user_id"]
    current_role = session["role"]
    current_priority = ROLE_PRIORITY[current_role]

    ui_state_id = request.args.get("state_id")
    ui_city_id = request.args.get("city_id")
    ui_ward_id = request.args.get("ward_id")
    search = request.args.get("search")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # ---- GET PROFILE FROM DB ----
    cursor.execute("""
        SELECT state_id, city_id
        FROM Users
        WHERE user_id = %s
    """, (current_user_id,))
    profile = cursor.fetchone() or {}

    profile_state_id = profile.get("state_id")
    profile_city_id = profile.get("city_id")

    query = """
        SELECT u.user_id, u.name, u.mobile, u.email, u.role,
               u.verified, u.assisted_signup,
               s.name AS state_name,
               c.name AS city_name,
               w.name AS ward_name
        FROM Users u
        LEFT JOIN States s ON u.state_id = s.state_id
        LEFT JOIN Cities c ON u.city_id = c.city_id
        LEFT JOIN Wards w ON u.ward_id = w.ward_id
        WHERE 1=1
    """
    params = []

    # ---- STRICT ROLE HIERARCHY ----
    allowed_roles = [
        role for role, priority in ROLE_PRIORITY.items()
        if priority < current_priority
    ]
    query += " AND u.role IN (%s)" % ",".join(["%s"] * len(allowed_roles))
    params.extend(allowed_roles)

    # ---- HARD GEOGRAPHIC SCOPING ----
    if current_role == "state_admin":
        query += " AND u.state_id = %s"
        params.append(profile_state_id)

    elif current_role == "municipal_admin":
        query += " AND u.city_id = %s"
        params.append(profile_city_id)

    # ---- UI FILTERS (WITHIN ALLOWED SCOPE) ----
    if current_role == "super_admin" and ui_state_id:
        query += " AND u.state_id = %s"
        params.append(ui_state_id)

    if ui_city_id:
        query += " AND u.city_id = %s"
        params.append(ui_city_id)

    if ui_ward_id:
        query += " AND u.ward_id = %s"
        params.append(ui_ward_id)

    if search:
        like = f"%{search}%"
        query += " AND (u.name LIKE %s OR u.mobile LIKE %s)"
        params.extend([like, like])

    query += " ORDER BY u.created_at ASC"

    cursor.execute(query, params)
    users = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({"users": users})


# ========================
# GET CITIES (AJAX)
# ========================
@admin_bp.route("/get_cities")
@login_required
def get_cities():

    role = session.get("role")
    current_user_id = session.get("user_id")
    ui_state_id = request.args.get("state_id")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch authoritative state from DB
    cursor.execute("""
        SELECT state_id
        FROM Users
        WHERE user_id = %s
    """, (current_user_id,))
    profile = cursor.fetchone() or {}

    profile_state_id = profile.get("state_id")

    if role == "super_admin":
        state_id = ui_state_id
    else:
        state_id = profile_state_id   # <-- FIX

    if not state_id:
        cursor.close()
        conn.close()
        return jsonify({"cities": []})

    cursor.execute("""
        SELECT city_id, name
        FROM Cities
        WHERE state_id = %s
        ORDER BY name
    """, (state_id,))

    cities = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({"cities": cities})


# ========================
# GET WARDS (AJAX)
# ========================
@admin_bp.route("/get_wards")
@login_required
def get_wards():

    role = session.get("role")
    current_user_id = session.get("user_id")
    ui_city_id = request.args.get("city_id")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Get authoritative city from DB
    cursor.execute("""
        SELECT city_id
        FROM Users
        WHERE user_id = %s
    """, (current_user_id,))
    profile = cursor.fetchone() or {}

    profile_city_id = profile.get("city_id")

    if role == "super_admin":
        city_id = ui_city_id

    elif role == "state_admin":
        city_id = ui_city_id

    elif role == "municipal_admin":
        city_id = profile_city_id   # <-- FIXED

    if not city_id:
        cursor.close()
        conn.close()
        return jsonify({"wards": []})

    cursor.execute("""
        SELECT ward_id, name
        FROM Wards
        WHERE city_id = %s
        ORDER BY name
    """, (city_id,))

    wards = cursor.fetchall()
    cursor.close()
    conn.close()

    return jsonify({"wards": wards})


# ========================
# GET DEPARTMENTS (AJAX)
# ========================
@admin_bp.route("/get_departments")
@login_required
def get_departments():

    current_user_id = session.get("user_id")
    role = session.get("role")
    ui_city_id = request.args.get("city_id")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch authoritative city/state from DB
    cursor.execute("""
        SELECT state_id, city_id
        FROM Users
        WHERE user_id = %s
    """, (current_user_id,))
    profile = cursor.fetchone() or {}

    profile_state_id = profile.get("state_id")
    profile_city_id = profile.get("city_id")

    city_id = None

    if role == "super_admin":
        city_id = ui_city_id

    elif role == "state_admin":
        city_id = ui_city_id

        # Validate city belongs to their state
        if city_id:
            cursor.execute("""
                SELECT 1 FROM Cities
                WHERE city_id=%s AND state_id=%s
            """, (city_id, profile_state_id))
            if not cursor.fetchone():
                cursor.close()
                conn.close()
                return jsonify({"departments": []})

    elif role in ["municipal_admin", "department_admin"]:
        city_id = profile_city_id

    if not city_id:
        cursor.close()
        conn.close()
        return jsonify({"departments": []})

    cursor.execute("""
        SELECT department_id, name
        FROM Departments
        WHERE city_id=%s
        ORDER BY name
    """, (city_id,))
    departments = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({"departments": departments})

# ========================
# ADMIN DASHBOARD
# ========================
@admin_bp.route("/dashboard")
@login_required
@role_required("super_admin", "state_admin", "municipal_admin")
def admin_dashboard():
    if not session.get("user_id"):
        flash("Please login first using OTP.", "warning")
        return redirect(url_for("otp.request_otp"))
    return render_template("admin/admin_dashboard.html", role=session["role"])

# ========================
# VIEW USERS
# ========================
@admin_bp.route("/users")
@login_required
@role_required("super_admin", "state_admin", "municipal_admin")
def view_users():

    current_user_id = session["user_id"]
    current_role = session["role"]
    current_priority = ROLE_PRIORITY[current_role]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # ---- GET AUTHORITATIVE PROFILE CONTEXT FROM DB ----
    cursor.execute("""
        SELECT state_id, city_id
        FROM Users
        WHERE user_id = %s
    """, (current_user_id,))
    profile = cursor.fetchone() or {}

    profile_state_id = profile.get("state_id")
    profile_city_id = profile.get("city_id")
    profile_ward_id = profile.get("ward_id")

    # ---- BASE QUERY ----
    query = """
        SELECT u.user_id, u.name, u.mobile, u.email, u.role,
               u.verified, u.assisted_signup, u.created_at,
               s.name AS state_name,
               c.name AS city_name,
               w.name AS ward_name
        FROM Users u
        LEFT JOIN States s ON u.state_id = s.state_id
        LEFT JOIN Cities c ON u.city_id = c.city_id
        LEFT JOIN Wards w ON u.ward_id = w.ward_id
        WHERE 1=1
    """
    params = []

    # ---- STRICT ROLE HIERARCHY ----
    allowed_roles = [
        role for role, priority in ROLE_PRIORITY.items()
        if priority < current_priority
    ]
    query += " AND u.role IN (%s)" % ",".join(["%s"] * len(allowed_roles))
    params.extend(allowed_roles)

    # ---- GEOGRAPHIC HARD SCOPING ----
    if current_role == "state_admin":
        query += " AND u.state_id = %s"
        params.append(profile_state_id)

    elif current_role == "municipal_admin":
        query += " AND u.city_id = %s"
        params.append(profile_city_id)

    query += " ORDER BY u.created_at ASC"

    cursor.execute(query, params)
    users = cursor.fetchall()

    cursor.execute("SELECT state_id, name FROM States ORDER BY name")
    states = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "admin/user.html",
        users=users,
        states=states,
        role=current_role,
        profile_location={
            "state_id": profile_state_id,
            "city_id": profile_city_id,
            "ward_id": profile_ward_id
        }
    )


# ========================
# CREATE USER
# ========================
@admin_bp.route("/create_user", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "state_admin", "municipal_admin")
def create_user():

    current_user_id = session["user_id"]
    current_role = session["role"]
    current_priority = ROLE_PRIORITY[current_role]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # ---- GET CREATOR PROFILE FROM DB (AUTHORITATIVE) ----
    cursor.execute("""
    SELECT 
        u.state_id,
        u.city_id,
        s.name AS state_name,
        c.name AS city_name
    FROM Users u
    LEFT JOIN States s ON u.state_id = s.state_id
    LEFT JOIN Cities c ON u.city_id = c.city_id
    WHERE u.user_id = %s
    """, (current_user_id,))
    profile = cursor.fetchone() or {}


    profile_state_id = profile.get("state_id")
    profile_city_id = profile.get("city_id")

    profile_state_name = profile.get("state_name")
    profile_city_name = profile.get("city_name")


    # ---- GET MASTER DATA ----
    cursor.execute("SELECT state_id, name FROM States ORDER BY name")
    states = cursor.fetchall()

    # ---- LOAD CITIES BASED ON ROLE ----
    if current_role == "super_admin":
        cursor.execute("SELECT city_id, name, state_id FROM Cities ORDER BY name")
        cities = cursor.fetchall()

    elif current_role == "state_admin":
        cursor.execute("""
            SELECT city_id, name, state_id
            FROM Cities
            WHERE state_id = %s
            ORDER BY name
        """, (profile_state_id,))
        cities = cursor.fetchall()

    elif current_role == "municipal_admin":
        cursor.execute("""
            SELECT city_id, name, state_id
            FROM Cities
            WHERE city_id = %s
        """, (profile_city_id,))
        cities = cursor.fetchall()

    # ---- LOAD WARDS BASED ON ROLE ----
    if current_role == "super_admin":
        cursor.execute("SELECT ward_id, name, city_id FROM Wards ORDER BY name")
        wards = cursor.fetchall()

    elif current_role == "state_admin":
        cursor.execute("""
            SELECT w.ward_id, w.name, w.city_id
            FROM Wards w
            JOIN Cities c ON w.city_id = c.city_id
            WHERE c.state_id = %s
            ORDER BY w.name
        """, (profile_state_id,))
        wards = cursor.fetchall()

    elif current_role == "municipal_admin":
        cursor.execute("""
            SELECT ward_id, name, city_id
            FROM Wards
            WHERE city_id = %s
            ORDER BY name
        """, (profile_city_id,))
        wards = cursor.fetchall()


    # ---- ALLOWED ROLES (STRICT HIERARCHY) ----
    allowed_roles = [
        role for role, priority in ROLE_PRIORITY.items()
        if priority < current_priority
    ]

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        mobile = request.form.get("mobile", "").strip()
        password = request.form.get("password", "").strip()
        new_role = request.form.get("role")

        cursor.execute("SELECT user_id FROM Users WHERE mobile=%s", (mobile,))

        if cursor.fetchone():
            cursor.close()
            conn.close()
            flash("Mobile no. already registered. Please verify.", "warning")
            return redirect(url_for("admin.create_user"))
        # ---- ROLE VALIDATION ----
        if new_role not in allowed_roles:
            flash("You cannot create a user with equal or higher role.", "danger")
            return redirect(url_for("admin.create_user"))

        # ---- GEOGRAPHIC AUTO ASSIGNMENT ----
        form_state_id = request.form.get("state_id")
        form_city_id = request.form.get("city_id")
        form_ward_id = request.form.get("ward_id")

        if current_role == "super_admin":
            state_id = int(form_state_id) if form_state_id else None
            city_id = int(form_city_id) if form_city_id else None

        elif current_role == "state_admin":
            if not profile_state_id:
                flash("Your profile state is not set.", "danger")
                return redirect(url_for("admin.create_user"))

            state_id = profile_state_id
            city_id = int(form_city_id) if form_city_id else None

        elif current_role == "municipal_admin":
            if not profile_state_id or not profile_city_id:
                flash("Your profile location is incomplete.", "danger")
                return redirect(url_for("admin.create_user"))

            state_id = profile_state_id
            city_id = profile_city_id

        ward_id = int(form_ward_id) if form_ward_id else None

        # ---- INSERT USER ----
        cursor.execute("""
            INSERT INTO Users (
                name, email, mobile, password, role,
                state_id, city_id, ward_id,
                verified, assisted_signup
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,TRUE)
        """, (
            name, email, mobile, password, new_role,
            state_id, city_id, ward_id
        ))

        conn.commit()
        cursor.close()
        conn.close()

        flash("User created successfully.", "success")
        return redirect(url_for("admin.view_users"))

    cursor.close()
    conn.close()

    return render_template(
        "admin/admin_create_user.html",
        states=states,
        cities=cities,
        wards=wards,
        allowed_roles=allowed_roles,
        role=current_role,
        profile_location={
            "state_id": profile_state_id,
            "city_id": profile_city_id,
            "state_name": profile_state_name,
            "city_name": profile_city_name
        }
    )

