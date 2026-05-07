"""one-shot: quote CFN Description fields containing ': ' (otherwise YAML reads as nested mapping)."""
import pathlib

root = pathlib.Path(__file__).resolve().parent.parent / "infra" / "aws"
fixed = 0
for f in root.rglob("*.yaml"):
    txt = f.read_text(encoding="utf-8")
    lines = txt.splitlines(keepends=True)
    if len(lines) < 2:
        continue
    if not lines[1].startswith("Description:"):
        continue
    val = lines[1][len("Description:"):].strip().rstrip("\r\n")
    if val.startswith(('"', "'")):
        continue
    if ": " not in val:
        continue
    new_val = val.replace('"', '\\"')
    eol = "\n" if lines[1].endswith("\n") else ""
    if lines[1].endswith("\r\n"):
        eol = "\r\n"
    lines[1] = f'Description: "{new_val}"{eol}'
    f.write_text("".join(lines), encoding="utf-8")
    fixed += 1
    print(f.relative_to(root))

print(f"fixed: {fixed}")
