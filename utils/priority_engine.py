from datetime import datetime

def calculate_priority(issue, support_count):

    score = 0

    # category weight
    category_weights = {
        "Safety": 6,
        "Health": 5,
        "Road": 4,
        "Garbage": 3,
        "Water": 3,
        "Electricity": 3,
        "Accessibility": 2,
        "Other": 1
    }

    score += category_weights.get(issue["category"], 1)

    # support factor
    score += support_count * 2

    # age factor
    created_at = issue["created_at"]

    if isinstance(created_at, str):
        created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")

    age_days = (datetime.now() - created_at).days

    if age_days > 7:
        score += 4
    elif age_days > 3:
        score += 2

    # priority level
    if score >= 12:
        level = "Critical"
    elif score >= 8:
        level = "High"
    elif score >= 4:
        level = "Medium"
    else:
        level = "Low"

    return score, level

