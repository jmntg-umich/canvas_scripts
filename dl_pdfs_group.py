import os
import re
import requests
from canvasapi import Canvas
from canvasapi.requester import Requester

API_URL = 'https://umich.instructure.com/'    # TODO: Your Canvas URL (Ex: "https://school.instructure.com")
API_KEY = '1770~mPy3nBMJT92AZLJLXeNaBxKGQRka68hMvwaXhUaBtkG92YEmJCLEenAB7Bzxtz4R'    # TODO: Your Canvas API key for the URL (Ex: "hjOyO8TQpVb5D4R1ygrMnTl0eO7QNp7y6QnfQkIMBeaMVv2KYRnEYrlN1rtW18Jv")
COURSE_ID = 770628   # TODO: Your course ID from Canvas URL (Ex: the 12345 from https://school.instructure.com/courses/12345/)

def flatten(text):
    return re.sub(r'[\r\n\t]+', ' ', text.strip()) if text else ""

assignment_id_str = input("Enter the Canvas Assignment ID: ").strip()
if not assignment_id_str.isdigit():
    raise ValueError("Assignment ID must be a number.")
ASSIGNMENT_ID = int(assignment_id_str)

canvas = Canvas(API_URL, API_KEY)
course = canvas.get_course(COURSE_ID)
assignment = course.get_assignment(ASSIGNMENT_ID)

# Build group lookup
group_id_to_name = {}
group_id_to_members = {}
for group in course.get_groups():
    group_id_to_name[group.id] = group.name
    group_id_to_members[group.id] = [user.name for user in group.get_users()]

safe_name = re.sub(r'[^\w\- ]', '_', assignment.name)
os.makedirs(safe_name, exist_ok=True)

print(f"📁 Downloading annotated group submissions for: {assignment.name}")

submissions = assignment.get_submissions(grouped=True, include=['user', 'attachments', 'submission_comments', 'group'])
for sub in submissions:
    group_info = getattr(sub, 'group', None)
    if not group_info:
        print(f"  ⚠️ Skipping submission missing group info.")
        continue
    group_id = group_info['id']
    group_name = group_id_to_name.get(group_id, f"Group_{group_id}")
    safe_group_name = re.sub(r'[^\w\- ]', '_', group_name)
    members = group_id_to_members.get(group_id, ["Unknown members"])
    members_str = ", ".join(members)

    # Only proceed if there are attachments (indicating a submission exists)
    if not hasattr(sub, "attachments") or not sub.attachments:
        print(f"  ⚠️ No submission found for {group_name} ({members_str}), skipping.")
        continue

    # The "sub.user_id" points to the actual submitter for this group!
    submitter_id = getattr(sub, 'user_id', None)
    if not submitter_id:
        print(f"  ⚠️ Unable to identify submitter for {group_name} ({members_str}).")
        continue

    # Fetch submission details for the actual submitter
    sub_raw = course._requester.request(
        'GET',
        f"courses/{COURSE_ID}/assignments/{ASSIGNMENT_ID}/submissions/{submitter_id}"
    )
    sub_json = sub_raw.json()
    annotated_pdf_url = sub_json.get('pdf_annotated_url', None)

    if annotated_pdf_url:
        file_path = os.path.join(safe_name, f"{safe_group_name}_ANNOTATED.pdf")
        with requests.get(annotated_pdf_url, headers={"Authorization": f"Bearer {API_KEY}"}, stream=True) as r:
            r.raise_for_status()
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        print(f"  ✓ Saved annotated PDF for {group_name} ({members_str})")
    else:
        print(f"  ⚠️ No annotated PDF for {group_name} ({members_str}).")

    # Instructor comments
    comments = ""
    for c in sub_json.get("submission_comments", []):
        comment_content = flatten(c.get('comment', ''))
        if comment_content:
            comments += comment_content + " | "
    comments = comments.strip(' |')
    if comments:
        comment_file = os.path.join(safe_name, f"{safe_group_name}_comment.txt")
        with open(comment_file, "w", encoding="utf-8") as cf:
            cf.write(comments)
        print(f"  ✓ Saved comment for {group_name}")

print("✅ Done! All available annotated group PDF submissions and comments downloaded.")