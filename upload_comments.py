import re
import os
import json
import mimetypes
from collections import defaultdict

import requests
from canvasapi import Canvas


def norm_first(name):
    """Normalize a first name for matching (lowercase, letters only)."""
    name = (name or "").strip().lower()
    name = re.sub(r"[^a-z]", "", name)
    return name


def parse_first_name(display_name):
    """
    Infer first name from Canvas display name.
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
        if key:
            index[key].append(path)

    return index


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


def canvas_api_base(api_url):
    """
    Convert config API_URL (domain) into API base.
    e.g. https://umich.instructure.com  -> https://umich.instructure.com/api/v1
    """
    api_url = (api_url or "").strip().rstrip("/")
    if api_url.endswith("/api/v1"):
        # If someone put this in config, normalize it to domain-only first
        api_url = api_url[: -len("/api/v1")]
    return api_url + "/api/v1"


def upload_comment_file_and_post_comment(api_url, api_key, course_id, assignment_id, user_id, file_path, comment_text):
    """
    Pure requests implementation of:
      1) POST /courses/:course_id/assignments/:assignment_id/submissions/:user_id/comments/files
      2) POST upload_url (multipart)
      3) follow success redirect if needed to obtain file id
      4) PUT submission with comment + file_ids
    Returns file_id (int).
    """
    api_base = canvas_api_base(api_url)
    headers = {"Authorization": f"Bearer {api_key}"}

    file_name = os.path.basename(file_path)
    size = os.path.getsize(file_path)
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    # 1) request upload slot
    slot_url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}/comments/files"
    r1 = requests.post(
        slot_url,
        headers=headers,
        data={"name": file_name, "size": size, "content_type": content_type},
        timeout=120,
    )
    r1.raise_for_status()
    slot = r1.json()

    upload_url = slot.get("upload_url")
    upload_params = slot.get("upload_params") or {}
    if not upload_url:
        raise RuntimeError(f"Canvas did not return upload_url for {file_name} (user_id={user_id}).")

    # 2) upload to upload_url
    with open(file_path, "rb") as f:
        files = {"file": (file_name, f, content_type)}
        r2 = requests.post(upload_url, data=upload_params, files=files, allow_redirects=False, timeout=300)

    uploaded = None

    # 3) follow redirect or parse JSON
    if r2.status_code in (301, 302, 303, 307, 308):
        success_url = r2.headers.get("Location")
        if not success_url:
            raise RuntimeError(f"Upload redirect missing Location header for {file_name} (user_id={user_id}).")
        r3 = requests.get(success_url, headers=headers, timeout=120)
        r3.raise_for_status()
        uploaded = r3.json()
    else:
        r2.raise_for_status()
        try:
            uploaded = r2.json()
        except Exception:
            uploaded = None

    if not isinstance(uploaded, dict):
        raise RuntimeError(
            f"Upload returned unexpected response for {file_name} (user_id={user_id}). "
            f"HTTP {r2.status_code}."
        )

    file_id = uploaded.get("id") or uploaded.get("file_id")
    if not file_id:
        raise RuntimeError(
            f"Upload completed but could not determine file id for {file_name} (user_id={user_id}). "
            f"Response keys: {list(uploaded.keys())}"
        )

    # 4) post comment with attachment
    comment_url = f"{api_base}/courses/{course_id}/assignments/{assignment_id}/submissions/{user_id}"
    payload = {
        "comment[text_comment]": comment_text or "",
        "comment[file_ids][]": str(file_id),
    }
    r4 = requests.put(comment_url, headers=headers, data=payload, timeout=120)
    r4.raise_for_status()

    return int(file_id)


def auto_comment_from_files(
    api_url,
    api_key,
    course_id,
    assignment_id,
    files_folder,
    comment_text="See attached file.",
    allowed_ext=None,
    dry_run=False,
    report_path=None,
):
    # canvasapi: just for listing submissions/users
    canvas = Canvas(api_url, api_key)
    course = canvas.get_course(course_id)
    assignment = course.get_assignment(assignment_id)

    submissions = list(assignment.get_submissions(include=["user"]))

    canvas_rows = []
    for sub in submissions:
        u = getattr(sub, "user", None) or {}
        name = u.get("name") or ""
        sortable = u.get("sortable_name") or ""
        first = parse_first_name(sortable or name)
        canvas_rows.append({
            "user_id": int(getattr(sub, "user_id")),
            "name": name,
            "sortable_name": sortable,
            "first_key": norm_first(first),
        })

    canvas_by_first = defaultdict(list)
    for row in canvas_rows:
        canvas_by_first[row["first_key"]].append(row)

    report = {
        "canvas_missing_file": [],            # (a)
        "canvas_duplicate_first_names": [],   # (b)
        "file_missing_canvas_student": [],    # (c)
        "ambiguous_file_for_student": [],
        "uploaded": [],
        "failed_upload_or_comment": [],
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

    # (c) files with no Canvas student match
    for first_key, paths in file_index.items():
        if first_key not in canvas_by_first:
            report["file_missing_canvas_student"].append({"first_key": first_key, "files": paths})

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
            # duplicate first name → skip for safety
            continue

        r = rows[0]
        matches = file_index.get(first_key, [])

        # (a) student has no associated file
        if len(matches) == 0:
            report["canvas_missing_file"].append({
                "user_id": r["user_id"],
                "name": r["name"],
                "sortable_name": r["sortable_name"],
                "first_key": first_key,
                "reason": "No matching file found."
            })
            continue

        if len(matches) > 1:
            report["ambiguous_file_for_student"].append({
                "user_id": r["user_id"],
                "name": r["name"],
                "sortable_name": r["sortable_name"],
                "first_key": first_key,
                "files": matches
            })
            continue

        file_path = matches[0]

        if dry_run:
            report["uploaded"].append({
                "user_id": r["user_id"],
                "name": r["name"],
                "file": file_path,
                "status": "DRY_RUN (not uploaded)"
            })
            continue

        try:
            file_id = upload_comment_file_and_post_comment(
                api_url=api_url,
                api_key=api_key,
                course_id=course_id,
                assignment_id=assignment_id,
                user_id=r["user_id"],
                file_path=file_path,
                comment_text=comment_text,
            )
            report["uploaded"].append({
                "user_id": r["user_id"],
                "name": r["name"],
                "file": file_path,
                "file_id": file_id,
                "status": "UPLOADED"
            })
            print(f"Uploaded/commented: {r['name']} (user_id={r['user_id']}) <- {os.path.basename(file_path)}")
        except Exception as e:
            report["failed_upload_or_comment"].append({
                "user_id": r["user_id"],
                "name": r["name"],
                "file": file_path,
                "error": str(e),
            })
            print(f"FAILED: {r['name']} (user_id={r['user_id']}): {e}")

    if report_path:
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    return report


if __name__ == "__main__":
    # config.json should contain:
    # {
    #   "API_URL": "https://umich.instructure.com",  (NO /api/v1)
    #   "API_KEY": "...",
    #   "COURSE_ID": 12345
    # }
    CONFIG_PATH = "./config.json"
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    API_URL = config["API_URL"].strip().rstrip("/")
    API_KEY = config["API_KEY"].strip()
    COURSE_ID = int(config["COURSE_ID"])

    if "/api/v1" in API_URL:
        raise ValueError(f"API_URL in config must NOT include /api/v1. Got: {API_URL}")

    FILES_FOLDER = "./comment_files"
    COMMENT_TEXT = "Please see the attached feedback file."
    DRY_RUN = False
    REPORT_PATH = "./reports/canvas_comment_upload_report.json"
    ALLOWED_EXT = None  # e.g. [".pdf", ".docx", ".txt"]

    # validate course access early
    canvas = Canvas(API_URL, API_KEY)
    course = canvas.get_course(COURSE_ID)

    assignment_id = prompt_for_assignment_id(course)

    report = auto_comment_from_files(
        api_url=API_URL,
        api_key=API_KEY,
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
    print("failed upload/comment:", len(report["failed_upload_or_comment"]))
    print("uploaded:", len(report["uploaded"]))
    print(f"report written to: {REPORT_PATH}")