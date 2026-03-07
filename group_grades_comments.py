import re
import os
import json
from canvasapi import Canvas

def flatten(text):
    """Remove line breaks and tabs, and extra whitespace."""
    return re.sub(r'[\r\n\t]+', ' ', text.strip()) if text else ""

def sanitize_filename(name):
    name = name.lower().replace(' ', '_')
    name = re.sub(r'[^a-z0-9_.-]', '', name)
    return name

def create_unique_filename(course_name, assignment_name, folder, base_suffix="_comments.txt"):
    os.makedirs(folder, exist_ok=True)
    course_safe = sanitize_filename(course_name)
    assignment_safe = sanitize_filename(assignment_name)
    base = f"{course_safe}_{assignment_safe}{base_suffix}"
    n = 1
    while True:
        fname = os.path.join(folder, f"{base if n == 1 else f'{course_safe}_{assignment_safe}_{n}{base_suffix}'}")
        if not os.path.exists(fname):
            return fname
        n += 1

def get_rubric_assessment(sub):
    ra = getattr(sub, "rubric_assessment", None)
    if ra:
        return ra
    ra = sub.__dict__.get("rubric_assessment", None)
    if ra:
        return ra
    if hasattr(sub, "_requester_response"):
        try:
            ra = sub._requester_response.json().get("rubric_assessment", None)
            if ra:
                return ra
        except Exception:
            pass
    return {}

# --- Load config ---
with open('config.json', 'r') as f:
    config = json.load(f)

API_URL = config['API_URL']
API_KEY = config['API_KEY']
COURSE_ID = config['COURSE_ID']
COURSE_NAME = config['COURSE_NAME']

OUTPUT_FOLDER = "output"
canvas = Canvas(API_URL, API_KEY)
course = canvas.get_course(COURSE_ID)

# Build group lookup tables
group_id_to_name = {}
group_id_to_members = {}
for group in course.get_groups():
    group_id_to_name[group.id] = group.name
    group_id_to_members[group.id] = [user.name for user in group.get_users()]

user_id_to_name = {}
for user in course.get_users(enrollment_type=['teacher', 'ta', 'student']):
    user_id_to_name[user.id] = user.name

assignment_input = input("Enter an assignment number (group assignment only): ").strip()
try:
    assignment_number = int(assignment_input)
except ValueError:
    print("Invalid input: please enter a number.")
    exit(1)

a = course.get_assignment(assignment_number)
assignment_name = a.name
filename = create_unique_filename(COURSE_NAME, assignment_name, OUTPUT_FOLDER)

rubric_map = {}
rubric = getattr(a, 'rubric', None)
if rubric:
    for crit in rubric:
        rubric_map[crit['id']] = crit['description']

with open(filename, "w", encoding="utf-8") as outfile:
    outfile.write(f"Assignment: {assignment_number} ({assignment_name})\n\n")
    
    for s in a.get_submissions(grouped=True, include=['submission_comments', 'rubric_assessment', 'group', 'score', 'grader_id']):
        group_name = group_id_to_name.get(s.group['id'], f"Group {s.group['id']}")
        members = group_id_to_members.get(s.group['id'], ["Unknown members"])
        members_str = ", ".join(members)
        overall_score = s.score
        grader_id = getattr(s, 'grader_id', None)
        grader = user_id_to_name.get(grader_id, "Unknown grader") if grader_id else "Unknown grader"

        outfile.write(f"Group: {group_name}\n")
        outfile.write(f"Members: {members_str}\n")
        outfile.write(f"Score: {overall_score}\n")
        outfile.write(f"Grader: {grader}\n")
        outfile.write(f"Rubric:\n")
        rubric_assessment = get_rubric_assessment(s)
        
        # Print rubric scores
        for crit_id, crit_text in rubric_map.items():
            points = ""
            if rubric_assessment and crit_id in rubric_assessment:
                crit_obj = rubric_assessment[crit_id]
                points = crit_obj.get('points', None)
            outfile.write(f"  - {crit_text}:  [Score: {points}]\n")
        outfile.write("\n")

        # Rubric comments section
        outfile.write("Rubric comments:\n")
        for crit_id, crit_text in rubric_map.items():
            comment = "No comment."
            if rubric_assessment and crit_id in rubric_assessment:
                crit_obj = rubric_assessment[crit_id]
                crit_comment = flatten(crit_obj.get('comments', ''))
                if crit_comment:
                    comment = crit_comment
            outfile.write(f" - {crit_text}: {comment}\n")
        outfile.write("\n")

        # Assignment comments section
        submission_comments = getattr(s, 'submission_comments', [])
        if submission_comments:
            outfile.write(f"Assignment comment: ")
            comments_str = " | ".join(flatten(c['comment']) for c in submission_comments if 'comment' in c)
            outfile.write(comments_str + "\n")
        else:
            outfile.write("Assignment comment: No assignment comment.\n")
        outfile.write("\n" + "-"*40 + "\n\n")  # Separator between groups

print(f"Done! Output saved to {filename}")