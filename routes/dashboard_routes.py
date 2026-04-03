# routes/dashboard_routes.py
from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify, flash
from utils.priority_engine import calculate_priority
from utils.db import get_db_connection
from utils.auth import login_required
from datetime import date

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


# -------------------------------------------------
# DASHBOARD ENTRY (ROLE BASED REDIRECT)
# -------------------------------------------------
@dashboard_bp.route("/")
@login_required
def dashboard():
    role = session.get("role")

    # Admins go to admin dashboard
    if role in ["super_admin", "state_admin", "municipal_admin"]:
        return redirect(url_for("admin.admin_dashboard"))

    # Others go to issues dashboard
    return redirect(url_for("dashboard.issues_dashboard"))


# -------------------------------------------------
# ISSUES DASHBOARD (INITIAL LOAD)
# -------------------------------------------------
@dashboard_bp.route("/issues")
@login_required
def issues_dashboard():

    user_id = session["user_id"]
    role = session["role"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # ---------------- USER CONTEXT ----------------
    cursor.execute("""
        SELECT state_id, city_id, ward_id, department_id
        FROM Users
        WHERE user_id=%s
    """, (user_id,))
    user = cursor.fetchone()

    # ---------------- DEPARTMENTS ----------------
    departments = []

    if user and user.get("city_id"):
        cursor.execute("""
            SELECT department_id, name
            FROM Departments
            WHERE city_id = %s
            ORDER BY name
        """, (user["city_id"],))
        departments = cursor.fetchall()

    if not user:
        cursor.close()
        conn.close()
        flash("User context lost. Please login again.", "danger")
        return redirect(url_for("auth.login"))

    profile_location = {
    "state_id": user.get("state_id"),
    "city_id": user.get("city_id"),
    "ward_id": user.get("ward_id"),
    "department_id": user.get("department_id")
    }

    # ---------------- STATES ----------------
    cursor.execute("SELECT state_id, name FROM States ORDER BY name")
    states = cursor.fetchall()

    # ---------------- BASE WHERE ----------------
    where = " WHERE 1=1 "
    params = []

    # ---------------- ROLE SCOPING ----------------
    if role == "super_admin":
        pass  # No restriction

    elif role == "state_admin" and user.get("state_id"):
        where += " AND i.state_id = %s"
        params.append(user["state_id"])

    elif role in ["municipal_admin", "field_staff"] and user.get("city_id"):
        where += " AND i.city_id = %s"
        params.append(user["city_id"])

    elif role == "facilitator"  and user.get("ward_id"):
        where += " AND i.ward_id = %s"
        params.append(user["ward_id"])

    elif role == "department_admin":

        if user.get("city_id"):
            where += " AND i.city_id = %s"
            params.append(user["city_id"])
        elif user.get("state_id"):
            where += " AND i.state_id = %s"
            params.append(user["state_id"])

    elif role == "citizen":
        citizen_conditions = []
        citizen_params = []

        # Their own issues
        citizen_conditions.append("i.reported_by = %s")
        citizen_params.append(user_id)

        # Their ward issues (if ward exists)
        if user.get("ward_id"):
            citizen_conditions.append("i.ward_id = %s")
            citizen_params.append(user["ward_id"])

        where += " AND (" + " OR ".join(citizen_conditions) + ")"
        params.extend(citizen_params)
    
    # ---------------- ISSUES QUERY ----------------
    issues_query = f"""
        SELECT
            i.issue_id,
            i.title,
            i.category,
            i.current_status AS status,
            i.deadline,
            COUNT(sup.support_id) AS support_count,
            st.name AS state_name,
            c.name AS city_name,
            w.name AS ward_name,
            i.reported_by,
            i.created_at
        FROM Issues i
        LEFT JOIN Issue_Support sup 
            ON i.issue_id = sup.issue_id
        LEFT JOIN States st 
            ON i.state_id = st.state_id
        LEFT JOIN Cities c 
            ON i.city_id = c.city_id
        LEFT JOIN Wards w 
            ON i.ward_id = w.ward_id
        {where}
        GROUP BY 
        i.issue_id,
        i.title,
        i.category,
        i.current_status,
        i.deadline,
        st.name,
        c.name,
        w.name,
        i.reported_by,
        i.created_at
    """
    cursor.execute(issues_query, params)
    issues = cursor.fetchall()

    # ---------------- PRIORITY CALCULATION ----------------
    for issue in issues:
        score, level = calculate_priority(issue, issue["support_count"])
        issue["priority_score"] = score
        issue["priority_level"] = level

    issues.sort(key=lambda x: x["priority_score"], reverse=True)

    # ---------------- STATS QUERY (DYNAMIC) ----------------
    stats_query = f"""
        SELECT
            COUNT(*) AS Total,
            SUM(CASE WHEN LOWER(i.current_status)='reported' THEN 1 ELSE 0 END) AS Reported,
            SUM(CASE WHEN LOWER(i.current_status)='assigned' THEN 1 ELSE 0 END) AS Assigned,
            SUM(CASE WHEN LOWER(i.current_status)='in progress' THEN 1 ELSE 0 END) AS `In Progress`,
            SUM(CASE WHEN LOWER(i.current_status)='in review' THEN 1 ELSE 0 END) AS `In Review`,
            SUM(CASE WHEN LOWER(i.current_status)='resolved' THEN 1 ELSE 0 END) AS Resolved,
            SUM(CASE WHEN LOWER(i.current_status)='rejected' THEN 1 ELSE 0 END) AS Rejected,
            SUM(CASE WHEN LOWER(i.current_status) NOT IN ('resolved','rejected') 
                     AND i.deadline IS NOT NULL 
                     AND DATE(i.deadline) < CURDATE() THEN 1 ELSE 0 END) AS Overdue
        FROM Issues i
        {where}
    """
    cursor.execute(stats_query, params)
    stats = cursor.fetchone()

    # ensure no None
    for key in ['Total','Reported','Assigned','In Progress','In Review','Resolved','Rejected','Overdue']:
        stats[key] = stats.get(key) or 0

    cursor.close()
    conn.close()

    return render_template(
        "dashboard.html",
        role=role,
        issues=issues,
        stats=stats,
        states=states,
        departments=departments,
        profile_location=profile_location
    )



# -------------------------------------------------
# FILTER ISSUES (AJAX)
# -------------------------------------------------
@dashboard_bp.route("/issues/filter")
@login_required
def filter_issues():
    user_id = session.get("user_id")
    role = session.get("role")

    # ---------------- GET FILTER PARAMETERS ----------------
    state_id = request.args.get("state_id")
    city_id = request.args.get("city_id")
    ward_id = request.args.get("ward_id")
    department_id = request.args.get("department_id")
    status = request.args.get("status")
    search = request.args.get("search")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    overdue = request.args.get("overdue")  # 'true' if filtering overdue issues

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # ---------------- USER CONTEXT ----------------
    cursor.execute("""
        SELECT state_id, city_id, ward_id, department_id
        FROM Users
        WHERE user_id=%s
    """, (user_id,))
    user = cursor.fetchone() or {}

    conditions = ["1=1"]
    params = []

    # ---------------- ROLE SCOPING ----------------
    if role == "super_admin":
        pass  # No restriction

    elif role == "state_admin" and user.get("state_id"):
        conditions.append("i.state_id = %s")
        params.append(user["state_id"])

    elif role in ["municipal_admin", "field_staff"] and user.get("city_id"):
        conditions.append("i.city_id = %s")
        params.append(user["city_id"])

    elif role == "facilitator"  and user.get("ward_id"):
        conditions.append("i.ward_id = %s")
        params.append(user["ward_id"])

    elif role == "department_admin":

        # department admins can see issues in their city
        if user.get("city_id"):
            conditions.append("i.city_id = %s")
            params.append(user["city_id"])
        elif user.get("state_id"):
            conditions.append("i.state_id = %s")
            params.append(user["state_id"])

    elif role == "citizen":
        citizen_conditions = []
        citizen_params = []

        citizen_conditions.append("i.reported_by = %s")
        citizen_params.append(user_id)

        if user.get("ward_id"):
            citizen_conditions.append("i.ward_id = %s")
            citizen_params.append(user["ward_id"])

        conditions.append("(" + " OR ".join(citizen_conditions) + ")")
        params.extend(citizen_params)

    # ---------------- USER-APPLIED FILTERS ----------------
    if state_id:
        conditions.append("i.state_id = %s")
        params.append(state_id)
    if city_id:
        conditions.append("i.city_id = %s")
        params.append(city_id)
    if ward_id:
        conditions.append("i.ward_id = %s")
        params.append(ward_id)
    if department_id:
        conditions.append("i.assigned_department = %s")
        params.append(department_id)
    if status:
        conditions.append("LOWER(i.current_status) = LOWER(%s)")
        params.append(status)
    if search:
        conditions.append("(i.title LIKE %s OR CAST(i.issue_id AS CHAR) LIKE %s)")
        like_value = f"%{search}%"
        params.extend([like_value, like_value])

    today = date.today()
    # ---------------- DATE FILTERS ----------------
    warning_messages = []

    # Validate and adjust start_date
    if start_date:
        try:
            start_obj = date.fromisoformat(start_date)
            if start_obj > today:
                warning_messages.append("Start date cannot be in the future. Adjusted to today.")
                start_obj = today
            start_date = start_obj.isoformat()
            conditions.append("DATE(i.created_at) >= %s")
            params.append(start_date)
        except ValueError:
            start_date = None

    # Validate and adjust end_date
    if end_date:
        try:
            end_obj = date.fromisoformat(end_date)
            if end_obj > today:
                warning_messages.append("End date cannot be in the future. Adjusted to today.")
                end_obj = today
            end_date = end_obj.isoformat()
            conditions.append("DATE(i.created_at) <= %s")
            params.append(end_date)
        except ValueError:
            end_date = None

    # ---------------- OVERDUE FILTER ----------------
    if overdue and overdue.lower() == "true":
        conditions.append("i.current_status NOT IN ('Resolved','Rejected') AND i.deadline IS NOT NULL AND DATE(i.deadline) < CURDATE()")

    # Combine conditions
    where_clause = " WHERE " + " AND ".join(conditions)
    warning = " | ".join(warning_messages) if warning_messages else None

    # ---------------- ISSUES QUERY ----------------
    issues_query = f"""
    SELECT
        i.issue_id,
        i.title,
        i.category,
        i.current_status AS status,
        i.deadline,
        COUNT(sup.support_id) AS support_count,
        i.assigned_department,
        st.name AS state_name,
        c.name AS city_name,
        w.name AS ward_name,
        i.reported_by,
        created_at
    FROM Issues i
    LEFT JOIN Issue_Support sup 
        ON i.issue_id = sup.issue_id
    LEFT JOIN States st 
        ON i.state_id = st.state_id
    LEFT JOIN Cities c 
        ON i.city_id = c.city_id
    LEFT JOIN Wards w 
        ON i.ward_id = w.ward_id
    {where_clause}
    GROUP BY 
    i.issue_id,
    i.title,
    i.category,
    i.current_status,
    i.deadline,
    st.name,
    c.name,
    w.name,
    i.reported_by,
    created_at
    ORDER BY i.created_at DESC
    """
    cursor.execute(issues_query, params)
    issues = cursor.fetchall()

    # ---------------- PRIORITY CALCULATION ----------------
    for issue in issues:    
        score, level = calculate_priority(issue, issue["support_count"])
        issue["priority_score"] = score
        issue["priority_level"] = level
        
    # ---------------- STATS (DYNAMIC) ----------------
    stats_query = f"""
        SELECT
            COUNT(*) AS Total,
            SUM(CASE WHEN LOWER(i.current_status)='reported' THEN 1 ELSE 0 END) AS Reported,
            SUM(CASE WHEN LOWER(i.current_status)='assigned' THEN 1 ELSE 0 END) AS Assigned,
            SUM(CASE WHEN LOWER(i.current_status)='in progress' THEN 1 ELSE 0 END) AS `In Progress`,
            SUM(CASE WHEN LOWER(i.current_status)='in review' THEN 1 ELSE 0 END) AS `In Review`,
            SUM(CASE WHEN LOWER(i.current_status)='resolved' THEN 1 ELSE 0 END) AS Resolved,
            SUM(CASE WHEN LOWER(i.current_status)='rejected' THEN 1 ELSE 0 END) AS Rejected,
            SUM(CASE WHEN LOWER(i.current_status) NOT IN ('resolved','rejected') 
                    AND i.deadline IS NOT NULL 
                    AND DATE(i.deadline) < CURDATE() THEN 1 ELSE 0 END) AS Overdue
        FROM Issues i
        {where_clause}
    """
    cursor.execute(stats_query, params)
    stats = cursor.fetchone() or {}

    # ensure no None
    for key in ['Total','Reported','Assigned','In Progress','In Review','Resolved','Rejected','Overdue']:
        stats[key] = stats.get(key) or 0
    
    

    cursor.close()
    conn.close()

    corrected_dates = {}
    if start_date != request.args.get("start_date"):
        corrected_dates["start_date"] = start_date
    if end_date != request.args.get("end_date"):
        corrected_dates["end_date"] = end_date

    return jsonify({
        "issues": issues,
        "stats": stats,
        "warning": warning,
        "corrected_dates": corrected_dates or None
    })