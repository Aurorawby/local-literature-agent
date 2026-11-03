"""确保所有 .bat 文件是 CRLF 换行、UTF-8 无 BOM。"""

from pathlib import Path

for path in sorted(Path(".").glob("*.bat")):
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    if normalized != text or bom:
        path.write_bytes(normalized.encode("utf-8"))
        print(f"{path.name}: 已修正（BOM={bom}）")
    else:
        print(f"{path.name}: 无需修正")
    data = path.read_bytes()
    cr = data.count(b"\x0d")
    lf = data.count(b"\x0a")
    print(f"   {len(data)} 字节, CR={cr}, LF={lf}")
