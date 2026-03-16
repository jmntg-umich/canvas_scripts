import re
import os
import json
import mimetypes
from collections import defaultdict

from canvasapi import Canvas


def flatten(text):
    """Remove line breaks and tabs, and extra whitespace."""
    return re.sub(r'[\r\n\t]+', ' ', text.strip()) if text else ""


def norm_first(name):
    """Normalize a first name for matching (lowercase, letters only)."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z]", "", name)
    return name


def parse_first_name(display_name):
    """
    Try to infer first name from Canvas display name.
    Handles "Last, First" and "First Last".
    """
    display_name = (display_name or "").strip()
    if not display_name:
        return ""

    if "," in display_name:
        parts = [p.strip() for p in display_name.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            return parts[1].split()[0]

    return display_name.split()[0]


def build_file_index(folder, allowed_ext=None):
    """
    Index files by normalized first name derived from filename stem.
    Example: "alex.pdf" -> key "alex"
    """
    allowed_ext = set(e.lower() for e in (allowed_ext or []))
    index = defaultdict(list)

    if not os.path.isdir(folder):
        raise FileNotFoundError(f"Files folder not found: {folder}")

    for entry in os.listdir(folder):
        path = os.path.join(folder, entry)
        if not os.path.isfile(path):
            continue

        stem, ext = os.path.splitext(entry)
        ext = ext.lower()

        if allowed_ext and ext not in allowed_ext:
            continue

        key = norm_first(stem)
        if not key:
            continue
        index[key].append(path)

    return index


def list_assignment_submissions_with_users(course, assignment_id):
    assignment = course.get_assignment(assignment_id)
    subs = assignment.get_submissions(include=["user"])

    out = []
    for s in subs:
        u = getattr(s, "user", None) or {}
        name = u.get("name") or ""
        sortable = u.get("sortable_name") or ""
        first = parse_first_name(sortable or name)
        out.append({
            "user_id": int(getattr(s, "user_id")),
            "name": name,
            "sortable_name": sortable,
            "first_key": norm_first(first),
        })
    return out


def post_submission_comment_with_attachment(canvas, course_id, assignment_id, user_id, file_path, comment_text):
    file_name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    # 1) request upload slot
 #   url = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}/comments/files"
    url = f"courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}/comments/files"
    upload_req = {"name": file_name, "size": size, "content_type": content_type}
    upload_json = canvas._requester.request("POST", url, _data=upload_req)

    upload_url = upload_json.get("upload_url")
    upload_params = upload_json.get("upload_params") or {}
    if not upload_url:
        raise RuntimeError(f"Canvas did not return upload_url for {file_name} (user_id={user_id}).")

    # 2) upload bytes to upload_url
    with open(file_path, "rb") as f:
        files = {"file": (file_name, f, content_type)}
        resp = canvas._requester._session.post(upload_url, data=upload_params, files=files)
    resp.raise_for_status()

    try:
        uploaded = resp.json()
    except Exception:
        uploaded = {}

    file_id = uploaded.get("id") or uploaded.get("file_id")
    if not file_id:
        raise RuntimeError(
            f"Could not determine uploaded file_id for {file_name} (user_id={user_id}). "
            f"Response was not JSON or missing id."
        )

    # 3) post comment with attachment
    comment_url = f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    payload = {
        "comment[text_comment]": comment_text or "",
        "comment[file_ids][]": str(file_id),
    }
    canvas._requester.request("PUT", comment_url, _data=payload)
    return int(file_id)


def auto_comment_from_files(
    canvas,
    course_id,
    assignment_id,
    files_folder,
    comment_text="See attached file.",
    allowed_ext=None,
    dry_run=True,
    report_path=None,
):
    course = canvas.get_course(course_id)
    subs = list_assignment_submissions_with_users(course, assignment_id)

    canvas_by_first = defaultdict(list)
    for row in subs:
        canvas_by_first[row["first_key"]].append(row)

    report = {
        # requested error classes:
        "canvas_missing_file": [],            # (a) Canvas student has no associated file
        "canvas_duplicate_first_names": [],   # (b) >1 student with same first name
        "file_missing_canvas_student": [],    # (c) file has no associated Canvas student

        # extra safety:
        "ambiguous_file_for_student": [],     # >1 file matches a (unique) first name
        "uploaded": [],
    }

    # (b) duplicates in Canvas
    for first_key, rows in canvas_by_first.items():
        if first_key and len(rows) > 1:
            report["canvas_duplicate_first_names"].append({
                "first_key": first_key,
                "students": [{
                    "user_id": r["user_id"],
                    "name": r["name"],
                    "sortable_name": r["sortable_name"],
                } for r in rows]
            })

    file_index = build_file_index(files_folder, allowed_ext=allowed_ext)

    # (c) files that don't match any Canvas student first name
    for first_key, paths in file_index.items():
        if first_key not in canvas_by_first:
            report["file_missing_canvas_student"].append({"first_key": first_key, "files": paths})

    # For unique-first-name students, require exactly one matching file
    for first_key, rows in canvas_by_first.items():
        if not first_key:
            for r in rows:
                report["canvas_missing_file"].append({
                    "user_id": r["user_id"],
                    "name": r["name"],
                    "sortable_name": r["sortable_name"],
                    "reason": "Could not parse first name from Canvas user name."
                })
            continue

        if len(rows) > 1:
            # duplicate first name: skip uploading for safety
            continue

        student = rows[0]
        matches = file_index.get(first_key, [])

        # (a) student has no associated file
        if len(matches) == 0:
            report["canvas_missing_file"].append({
                "user_id": student["user_id"],
                "name": student["name"],
                "sortable_name": student["sortable_name"],
                "first_key": first_key,
                "reason": "No matching file found."
            })
            continue

        if len(matches) > 1:
            report["ambiguous_file_for_student"].append({
                "user_id": student["user_id"],
                "name": student["name"],
                "sortable_name": student["sortable_name"],
                "first_key": first_key,
                "files": matches
            })
            continue

        file_path = matches[0]

        if dry_run:
            report["uploaded"].append({
                "user_id": student["user_id"],
                "name": student["name"],
                "file": file_path,
                "status": "DRY_RUN (not uploaded)"
            })
        else:
            file_id = post_submission_comment_with_attachment(
                canvas=canvas,
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=student["user_id"],
                file_path=file_path,
                comment_text=comment_text,
            )
            report["uploaded"].append({
                "user_id": student["user_id"],
                "name": student["name"],
                "file": file_path,
                "file_id": file_id,
                "status": "UPLOADED"
            })

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return report


def prompt_for_assignment_id(course):
    user_input = input("Enter an assignment number to upload comments to: ").strip()
    if not user_input.isdigit():
        raise ValueError(f"Assignment number must be a positive integer. You entered: {user_input!r}")

    assignment_id = int(user_input)

    try:
        a = course.get_assignment(assignment_id)
        _ = a.name
    except Exception as e:
        raise ValueError(
            f"Could not find/access assignment {assignment_id} in this course. "
            f"Double-check the assignment ID and your permissions."
        ) from e

    return assignment_id


if __name__ == "__main__":
    # ---- CONFIG FILE ----
    CONFIG_PATH = "./config.json"
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    API_URL = config["API_URL"]      # must be base domain only (NO /api/v1)
    API_KEY = config["API_KEY"]
    COURSE_ID = config["COURSE_ID"]

    # ---- LOCAL SETTINGS ----
    FILES_FOLDER = "./comment_files"
    COMMENT_TEXT = "Please see the attached feedback file."
    DRY_RUN = True
    REPORT_PATH = "./reports/canvas_comment_upload_report.json"
    ALLOWED_EXT = None  # e.g. [".pdf", ".docx", ".txt"]

    # ---- RUN ----
    canvas = Canvas(API_URL, API_KEY)   # canvasapi will error if API_URL includes /api/v1
    course = canvas.get_course(COURSE_ID)

    assignment_id = prompt_for_assignment_id(course)

    report = auto_comment_from_files(
        canvas=canvas,
        course_id=COURSE_ID,
        assignment_id=assignment_id,
        files_folder=FILES_FOLDER,
        comment_text=COMMENT_TEXT,
        allowed_ext=ALLOWED_EXT,
        dry_run=DRY_RUN,
        report_path=REPORT_PATH,
    )

    print("=== SUMMARY ===")
    print("duplicate first names in Canvas:", len(report["canvas_duplicate_first_names"]))
    print("students missing a file:", len(report["canvas_missing_file"]))
    print("files missing a Canvas student:", len(report["file_missing_canvas_student"]))
    print("ambiguous multiple files for a student:", len(report["ambiguous_file_for_student"]))
    print("would upload/uploaded:", len(report["uploaded"]))
    print(f"report written to: {REPORT_PATH}")