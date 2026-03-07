# Canvas Assignment Comments Downloader

## Overview

This script downloads submission comments from a specified Canvas assignment and organizes them by commenter. Output is saved as a text file, grouping each commenter’s remarks and preserving who authored the original submission.

> **Note:** This output format is only for individual assignments, **not** for “all assignments”.

## Requirements

- Python 3.x
- [`canvasapi`](https://github.com/ucfopen/canvasapi) Python library
- A valid `config.json` file with your Canvas API credentials

## Installation

1. **Clone or download the script.**
2. **Install required Python modules:**
   ```bash
   pip install canvasapi
   ```
3. **Update `config_template.json` with your Canvas API and Course number file and rename it config.json**

## Usage

1. **Run the script:**
   ```bash
   python download_assignment_comments.py
   ```
2. **Input:** When prompted, enter a single assignment number (e.g., `120`) for which you want to download comments.  
   - Typing `all` will remind you that the script only works on single assignments.

3. **Output:**  
   The script saves the organized comments in the `output/` folder. The filename is automatically generated, e.g.:  
   ```
   mycoursename_assignmentname_comments.txt
   ```

## Output Format

Each commenter is listed, followed by their comments. For each comment, the name of the original submission’s author is recorded. Example:
```
Commenter: Jane Doe

Commenting on: John Smith
Comment: Great work on the introduction!

Commenting on: Emily Davis
Comment: Please clarify your thesis statement.

...

```

## Troubleshooting

- Make sure your Canvas API token is valid and your `COURSE_ID` matches your intended course.
- If you encounter errors or want to report feedback, contact [4help@umich.edu](mailto:4help@umich.edu) or visit [ITS Help](https://its.umich.edu/help).

## License

MIT (or your preferred license)

---

Let me know if you'd like tweaks for U-M branding, more advanced instructions, or additional examples. Go Blue!