#This script downloads comments from a single assignment and organizes them by commenter

import re
import json
import os
from collections import defaultdict
from canvasapi import Canvas

OUTPUT_FOLDER = "output"

def sanitize_filename(name):
    # Lowercase, replace spaces with underscores, remove unsafe chars
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

with open('config.json', 'r') as f:
    config = json.load(f)

API_URL = config['API_URL']
API_KEY = config['API_KEY']
COURSE_ID = config['COURSE_ID']

canvas = Canvas(API_URL, API_KEY)
course = canvas.get_course(COURSE_ID, include=["term"])
course_name = getattr(course, "name", f"course_{COURSE_ID}")

user_input = input("Enter an assignment number, or type 'all' to download comments from all assignments: ").strip()

if user_input.lower() == "all":
    print("This output format is for a single assignment only. Please enter a single assignment number.")
else:
    try:
        assignment_number = int(user_input)
    except ValueError:
        print("Invalid input: please enter a number or 'all'.")
        exit(1)

    enrollments = course.get_enrollments()
    idToName = dict((e.user_id, e.user['name']) for e in enrollments)
    assignment = course.get_assignment(assignment_number)
    assignment_name = assignment.name

    filename = create_unique_filename(course_name, assignment_name, OUTPUT_FOLDER)

    # Build a dictionary: commenter -> list of (original_author, comment) tuples
    commenter_dict = defaultdict(list)
    for s in assignment.get_submissions(include=['submission_comments']):
        original_author = idToName.get(s.user_id, "Unknown author")
        for c in s.submission_comments:
            commenter = idToName.get(c['author_id'], "Unknown commenter")
            comment_text = re.sub(r'[\r\n\t]+', ' ', c['comment'])
            commenter_dict[commenter].append((original_author, comment_text))

    with open(filename, 'w', encoding='utf-8') as f:
        for commenter, comments in commenter_dict.items():
            f.write(f"Commenter: {commenter}\n\n")
            for original_author, comment_text in comments:
                f.write(f"Commenting on: {original_author}\n")
                f.write(f"Comment: {comment_text}\n\n")
            f.write('\n\n') # Two blank lines before next commenter

    print(f"Comments for assignment '{assignment_name}' saved to file: {filename}")