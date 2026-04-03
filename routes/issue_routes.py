# routes/issue_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, session, flash,jsonify
from utils.db import get_db_connection
from utils.auth import login_required, role_required
from werkzeug.utils import secure_filename
from utils.priority_engine import calculate_priority
from datetime import datetime, timedelta
import os

# -----------------------------
# CONFIGURATION
# -----------------------------
issue_bp = Blueprint("issues", __name__, url_prefix="/issues")
UPLOAD_FOLDER = "static/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -----------------------------
# CREATE NEW ISSUE
# -----------------------------
@issue_bp.route("/create", methods=["GET", "POST"])
@login_required
@role_required("citizen", "facilitator")
def create_issue():
    user_id = session["user_id"]
    role = session["role"]

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category")

        assisted = role == "facilitator"
        source = role

        if not title or not description or not category:
            flash("All required fields must be filled.", "danger")
            return redirect(url_for("issues.create_issue"))

        # Fetch user location
        cursor.execute(
            "SELECT state_id, city_id, ward_id FROM Users WHERE user_id=%s",
            (user_id,)
        )
        location = cursor.fetchone()

        if not location or not all([
            location["state_id"],
            location["city_id"],
            location["ward_id"]
        ]):
            flash("Your location details are incomplete. Please update your profile.", "danger")
            cursor.close()
            conn.close()
            return redirect(url_for("profile.profile_page"))

        # Insert issue (NO DEADLINE HERE)
        cursor.execute("""
            INSERT INTO Issues (
                title, description, category,
                state_id, city_id, ward_id,
                reported_by, source, assisted,
                current_status
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            title, description, category,
            location["state_id"], location["city_id"], location["ward_id"],
            user_id, source, assisted,
            "Reported"
        ))

        issue_id = cursor.lastrowid

        # Initial status entry
        cursor.execute("""
            INSERT INTO Status_Updates (issue_id, status, remarks, updated_by)
            VALUES (%s,%s,%s,%s)
        """, (issue_id, "Reported", "Issue reported", user_id))

        # Image uploads
        images = request.files.getlist("images")
        for img in images:
            if img and img.filename:
                filename = secure_filename(img.filename)
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                img.save(filepath)

                cursor.execute("""
                    INSERT INTO Issue_Images (issue_id, image_file, uploaded_by)
                    VALUES (%s,%s,%s)
                """, (issue_id, filepath, user_id))

        conn.commit()
        cursor.close()
        conn.close()

        flash("Issue reported successfully!", "success")
        return redirect(url_for("dashboard.dashboard"))

    # -------- SIMILAR ISSUES (GET REQUEST ONLY) --------
    similar_issues = []

    # Fetch user location
    cursor.execute(
        "SELECT state_id, city_id, ward_id FROM Users WHERE user_id=%s",
        (user_id,)
    )
    location = cursor.fetchone()

    if location and location["ward_id"]:
        cursor.execute("""
            SELECT i.issue_id, i.title, i.current_status,
                COUNT(s.support_id) AS support_count
            FROM Issues i
            LEFT JOIN Issue_Support s ON i.issue_id = s.issue_id
            WHERE i.ward_id = %s
            AND i.current_status NOT IN ('Resolved','Rejected')
            GROUP BY i.issue_id
            ORDER BY i.created_at DESC
            LIMIT 5
        """, (location["ward_id"],))

        similar_issues = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("issue_create.html", similar_issues=similar_issues)



# -----------------------------
# ISSUE DETAIL
# -----------------------------
@issue_bp.route("/<int:issue_id>")
@login_required
def issue_detail(issue_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Fetch issue with location names
    cursor.execute("""
        SELECT i.*, 
               s.name AS state_name, 
               c.name AS city_name, 
               w.name AS ward_name
        FROM Issues i
        LEFT JOIN States s ON i.state_id = s.state_id
        LEFT JOIN Cities c ON i.city_id = c.city_id
        LEFT JOIN Wards w ON i.ward_id = w.ward_id
        WHERE i.issue_id=%s
    """, (issue_id,))
    issue = cursor.fetchone()

    if not issue:
        cursor.close()
        conn.close()
        return "Issue not found", 404
    
    # ---- PRIORITY CALCULATION ----
    cursor.execute(
        "SELECT COUNT(*) as count FROM Issue_Support WHERE issue_id=%s",
        (issue_id,)
    )
    support = cursor.fetchone()
    support_count = support["count"]

    score, level = calculate_priority(issue, support_count)

    issue["priority_score"] = score
    issue["priority_level"] = level

    # Fetch status timeline
    cursor.execute("""
        SELECT su.status, su.remarks, su.updated_at, u.name
        FROM Status_Updates su
        JOIN Users u ON su.updated_by = u.user_id
        WHERE su.issue_id=%s
        ORDER BY su.updated_at ASC
    """, (issue_id,))
    timeline = cursor.fetchall()

    # Fetch images
    cursor.execute("SELECT image_file AS file_path FROM Issue_Images WHERE issue_id=%s", (issue_id,))
    images = cursor.fetchall()

    # Fetch issue reporter info
    cursor.execute("""
        SELECT u.name, u.mobile
        FROM Users u
        WHERE u.user_id = %s
    """, (issue['reported_by'],))
    reporter = cursor.fetchone()
    
    
    cursor.close()
    conn.close()
    return render_template(
    "issue_detail.html",
    issue=issue,
    timeline=timeline,
    images=images,
    reporter=reporter
)


# -----------------------------
# UPDATE ISSUE STATUS
# -----------------------------
@issue_bp.route("/<int:issue_id>/update", methods=["POST"])
@login_required
@role_required("department_admin", "field_staff")
def update_issue_status(issue_id):
    user_id = session["user_id"]
    role = session["role"]
    new_status = request.form.get("status")
    remarks = request.form.get("remarks")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT current_status, assigned_department
        FROM Issues
        WHERE issue_id=%s
    """, (issue_id,))
    issue = cursor.fetchone()

    if not issue:
        cursor.close()
        conn.close()
        return "Issue not found", 404

    current_status = issue["current_status"]
    assigned_department = issue["assigned_department"]
    
    # 🔒 Lock if rejected or resolved
    if current_status in ["Rejected", "Resolved"]:
        flash("This issue is locked and cannot be updated.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    # 🚫 If not assigned yet
    if not assigned_department:
        flash("Issue must be assigned before updating status.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    # Strict forward-only workflow
    workflow = {
        "Assigned": ["In Progress","Rejected"],
        "In Progress": ["In Review"],
        "In Review": ["Resolved"]
    }

    allowed_next = workflow.get(current_status, [])

    if new_status not in allowed_next:
        flash("Invalid status transition.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    # Update
    cursor.execute("""
        UPDATE Issues
        SET current_status=%s, updated_at=NOW()
        WHERE issue_id=%s
    """, (new_status, issue_id))

    cursor.execute("""
        INSERT INTO Status_Updates (issue_id, status, remarks, updated_by)
        VALUES (%s,%s,%s,%s)
    """, (issue_id, new_status, remarks, user_id))

    conn.commit()
    cursor.close()
    conn.close()

    flash("Status updated successfully!", "success")
    return redirect(url_for("issues.issue_detail", issue_id=issue_id))


# -----------------------------
# ASSIGN ISSUE
# -----------------------------
@issue_bp.route("/<int:issue_id>/assign", methods=["GET", "POST"])
@login_required
@role_required("municipal_admin")
def assign_issue(issue_id):

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM Issues WHERE issue_id=%s", (issue_id,))
    issue = cursor.fetchone()

    if not issue:
        cursor.close()
        conn.close()
        return "Issue not found", 404

    if request.method == "POST":

        # 🔒 LOCK CONDITIONS
        if issue["current_status"] in ["Rejected", "Resolved"] or issue["is_closed"]:
            cursor.close()
            conn.close()
            flash("This issue cannot be assigned.", "danger")
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))

        # 🔒 Prevent reassignment
        if issue["assigned_department"] is not None:
            cursor.close()
            conn.close()
            flash("Issue is already assigned.", "danger")
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))

        department_id = request.form.get("department_id")
        deadline_str = request.form.get("deadline")
        remarks = request.form.get("remarks")

        # ✅ Validate deadline (must be at least 3 hours from now)
        try:
            deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Invalid deadline format.", "danger")
            cursor.close()
            conn.close()
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))

        if deadline_dt < datetime.now() + timedelta(hours=3):
            flash("Deadline must be at least 3 hours from now.", "danger")
            cursor.close()
            conn.close()
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))

        # Update issue assignment
        cursor.execute("""
            UPDATE Issues 
            SET assigned_department=%s, deadline=%s, current_status='Assigned'
            WHERE issue_id=%s
        """, (department_id, deadline_dt, issue_id))

        # Log status update
        cursor.execute("""
            INSERT INTO Status_Updates (issue_id, status, remarks, updated_by)
            VALUES (%s,'Assigned',%s,%s)
        """, (issue_id, remarks, session["user_id"]))

        conn.commit()
        cursor.close()
        conn.close()

        flash("Issue assigned successfully!", "success")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    # GET: departments in city
    cursor.execute("""
        SELECT department_id, name FROM Departments WHERE city_id=%s
    """, (issue["city_id"],))
    departments = cursor.fetchall()

    # 🔹 Minimum deadline for frontend (3 hours from now)
    min_deadline = (datetime.now() + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")

    cursor.close()
    conn.close()
    return render_template(
        "issue_assign.html", 
        issue=issue, 
        departments=departments,
        min_deadline=min_deadline
    )

@issue_bp.route("/<int:issue_id>/feedback", methods=["POST"])
@login_required
@role_required("citizen", "facilitator")
def submit_feedback(issue_id):
    user_id = session["user_id"]
    rating = request.form.get("rating")
    feedback = request.form.get("feedback")

    if not rating or not feedback:
        flash("Rating and feedback are required.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except ValueError:
        flash("Invalid rating value.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT current_status, reported_by, is_closed
        FROM Issues
        WHERE issue_id=%s
    """, (issue_id,))
    issue = cursor.fetchone()

    if not issue:
        cursor.close()
        conn.close()
        return "Issue not found", 404

    # 🔐 Only original reporter can rate
    if issue["reported_by"] != user_id:
        cursor.close()
        conn.close()
        flash("You are not authorized to rate this issue.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    # 🧭 Must be resolved and not already closed
    if issue["current_status"] != "Resolved" or issue["is_closed"]:
        cursor.close()
        conn.close()
        flash("Feedback not allowed at this stage.", "danger")
        return redirect(url_for("issues.issue_detail", issue_id=issue_id))

    cursor.execute("""
        UPDATE Issues
        SET rating=%s,
            feedback=%s,
            is_closed=1,
            feedback_submitted_at=NOW()
        WHERE issue_id=%s
    """, (rating, feedback, issue_id))
    
    conn.commit()
    cursor.close()
    conn.close()

    flash("Thank you for your feedback!", "success")
    return redirect(url_for("issues.issue_detail", issue_id=issue_id))

@issue_bp.route("/support/<int:issue_id>", methods=["POST"])
@login_required
def support_issue(issue_id):
    user_id = session.get("user_id")
    role = session.get("role")

    if role != "citizen":
        return jsonify({"success": False, "message": "Only citizens can support issues."}), 403

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Check who reported the issue
    cursor.execute("SELECT reported_by FROM Issues WHERE issue_id=%s", (issue_id,))
    issue = cursor.fetchone()
    if not issue:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Issue not found."}), 404

    if issue["reported_by"] == user_id:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "You cannot support your own issue."}), 403

    # Check if already supported
    cursor.execute("SELECT support_id FROM Issue_Support WHERE issue_id=%s AND user_id=%s", (issue_id, user_id))
    already = cursor.fetchone()
    if already:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "You already supported this issue."}), 400

    # Insert support
    cursor.execute("INSERT INTO Issue_Support (issue_id, user_id) VALUES (%s, %s)", (issue_id, user_id))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({"success": True, "message": "Support recorded."})