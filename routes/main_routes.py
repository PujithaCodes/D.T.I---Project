
from flask import Blueprint, render_template
from utils.db import get_db_connection

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def home():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Total Issues
    cursor.execute("SELECT COUNT(*) AS total FROM Issues")
    total_issues = cursor.fetchone()["total"]

    # Resolved Issues
    cursor.execute("SELECT COUNT(*) AS total FROM Issues WHERE current_status = 'Resolved'")
    resolved_issues = cursor.fetchone()["total"]

    # In Progress Issues
    cursor.execute("""
        SELECT COUNT(*) AS total 
        FROM Issues 
        WHERE current_status IN ('Assigned', 'In Progress', 'In Review')
    """)
    in_progress_issues = cursor.fetchone()["total"]

    # Pending Issues (Reported but not yet assigned)
    cursor.execute("""
        SELECT COUNT(*) AS total 
        FROM Issues 
        WHERE current_status = 'Reported'
    """)
    pending_issues = cursor.fetchone()["total"]
    # Average Rating (only closed & rated issues)
    cursor.execute("""
        SELECT 
            ROUND(AVG(rating), 2) AS avg_rating,
            COUNT(rating) AS total_rated
        FROM Issues
        WHERE rating IS NOT NULL
    """)
    rating_data = cursor.fetchone()

    avg_rating = rating_data["avg_rating"] if rating_data["avg_rating"] else 0
    total_rated = rating_data["total_rated"]
    

    cursor.close()
    conn.close()

    # Avoid division by zero
    if total_issues > 0:
        resolution_rate = round((resolved_issues / total_issues) * 100, 1)
        active_rate = round(((in_progress_issues + pending_issues) / total_issues) * 100, 1)
    else:
        resolution_rate = 0
        active_rate = 0

    return render_template(
    "info.html",
    total_issues=total_issues,
    resolved_issues=resolved_issues,
    in_progress_issues=in_progress_issues,
    pending_issues=pending_issues,
    resolution_rate=resolution_rate,
    active_rate=active_rate,
    avg_rating=avg_rating,
    total_rated=total_rated
)

