"""
CV setup checker.

Verifies that the required CV files exist in data/ before running
the Matching Agent. PDF files are required; text files are optional
fallbacks used when PDFs are absent.

Usage:
    python3 setup_cvs.py
"""

from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

# (filename, label, required)
CV_FILES = [
    ("growth_cv.pdf", "Growth CV     (PDF — primary)",   True),
    ("ai_cv.pdf",     "AI/Platform CV (PDF — primary)",  True),
    ("growth_cv.txt", "Growth CV     (text — fallback)", False),
    ("ai_cv.txt",     "AI/Platform CV (text — fallback)", False),
    ("my_cv.txt",     "Generic CV    (last-resort fallback)", False),
]

print()
print("CV Setup Check")
print("=" * 55)

all_required_found = True

for filename, label, required in CV_FILES:
    path = DATA_DIR / filename
    if path.exists():
        size_kb = path.stat().st_size // 1024
        print(f"  {filename:<20} → Found ({size_kb}KB) {'✓ Ready' if required else '(fallback)'}")
    else:
        if required:
            print(f"  {filename:<20} → NOT FOUND  Please copy to data/{filename}")
            all_required_found = False
        else:
            print(f"  {filename:<20} → Not found  (optional)")

print("=" * 55)
print()

if all_required_found:
    print("CV setup complete. Ready to run matching agent.")
else:
    print("Fix the above before running matching agent.")
    print()
    print("Steps:")
    print("  1. Export your CVs from Google Docs or Word as PDF")
    print("  2. Copy growth_cv.pdf to:  data/growth_cv.pdf")
    print("  3. Copy ai_cv.pdf to:      data/ai_cv.pdf")
    print("  4. Re-run:  python3 setup_cvs.py")
