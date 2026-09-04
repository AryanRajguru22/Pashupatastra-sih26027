import subprocess
import os

repo = r"..\darshini-source"
output = r"..\darshini-source"

result = subprocess.run(
    ["git", "-C", repo, "ls-tree", "-r", "-z", "origin/domain-and-data"],
    capture_output=True,
    check=True
)

entries = result.stdout.split(b"\0")

count = 0

for entry in entries:
    if not entry:
        continue

    header, path_bytes = entry.split(b"\t", 1)
    parts = header.split()

    if len(parts) != 3:
        continue

    sha = parts[2].decode()
    git_path = path_bytes.decode("utf-8", errors="surrogateescape")

    # Convert both kinds of separators into Windows folders
    clean_path = git_path.replace("\\", "/")
    pieces = [p for p in clean_path.split("/") if p]

    destination = os.path.join(output, *pieces)

    os.makedirs(os.path.dirname(destination), exist_ok=True)

    data = subprocess.check_output(
        ["git", "-C", repo, "cat-file", "blob", sha]
    )

    with open(destination, "wb") as f:
        f.write(data)

    count += 1

print("Extraction complete.")
print("Files extracted:", count)