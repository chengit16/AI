"""生成 P0-07 文档解析与 OCR 的全合成固定样本。"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).parent
GENERATED = ROOT / "generated"

GLYPHS = {
    " ": ("00000",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "11100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
}


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    payload = chunk_type + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def raster_text(lines: tuple[str, ...], scale: int = 8) -> tuple[int, int, bytes]:
    width = max(len(line) for line in lines) * 6 * scale + 8 * scale
    height = len(lines) * 10 * scale + 6 * scale
    pixels = bytearray([255] * width * height)
    for line_index, line in enumerate(lines):
        origin_y = (3 + line_index * 10) * scale
        for char_index, character in enumerate(line):
            glyph = GLYPHS[character]
            origin_x = (4 + char_index * 6) * scale
            for row_index, row in enumerate(glyph):
                for column_index, bit in enumerate(row):
                    if bit == "0":
                        continue
                    for y_offset in range(scale):
                        start = (origin_y + row_index * scale + y_offset) * width
                        for x_offset in range(scale):
                            pixels[start + origin_x + column_index * scale + x_offset] = 0
    return width, height, bytes(pixels)


def make_png(width: int, height: int, pixels: bytes) -> bytes:
    scanlines = b"".join(b"\x00" + pixels[row * width : (row + 1) * width] for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + png_chunk(b"IEND", b"")
    )


def pdf(objects: list[bytes]) -> bytes:
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    output.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def text_pdf() -> bytes:
    stream = (
        b"BT /F1 20 Tf 72 750 Td (Synthetic Travel Policy) Tj "
        b"0 -36 Td /F1 12 Tf (Expense claims must be filed within 30 days.) Tj "
        b"0 -24 Td (Manager approval is required above 500 units.) Tj ET"
    )
    return pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        ]
    )


def table_pdf() -> bytes:
    commands = [
        "1 w",
        "72 700 m 400 700 l S",
        "72 670 m 400 670 l S",
        "72 640 m 400 640 l S",
        "72 700 m 72 640 l S",
        "220 700 m 220 640 l S",
        "400 700 m 400 640 l S",
        "BT /F1 12 Tf 82 680 Td (Level) Tj ET",
        "BT /F1 12 Tf 230 680 Td (Approver) Tj ET",
        "BT /F1 12 Tf 82 650 Td (Restricted) Tj ET",
        "BT /F1 12 Tf 230 650 Td (Department owner) Tj ET",
    ]
    stream = "\n".join(commands).encode()
    return pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
        ]
    )


def scanned_pdf(width: int, height: int, pixels: bytes) -> bytes:
    compressed = zlib.compress(pixels, level=9)
    rendered_height = round(540 * height / width)
    content = f"q 540 0 0 {rendered_height} 36 560 cm /Im0 Do Q".encode()
    return pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(compressed)} >>\nstream\n".encode()
            + compressed
            + b"\nendstream",
            f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
        ]
    )


def zip_entry(archive: ZipFile, name: str, content: str) -> None:
    info = ZipInfo(name, date_time=(2026, 8, 13, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    archive.writestr(info, content.encode("utf-8"))


def make_docx(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        zip_entry(
            archive,
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" '
            'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zip_entry(
            archive,
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        zip_entry(
            archive,
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            "<w:r><w:t>Synthetic Access Policy</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Visitors must register before entering the "
            "laboratory.</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Level</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Approver</w:t></w:r></w:p></w:tc></w:tr>"
            "<w:tr><w:tc><w:p><w:r><w:t>Restricted</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Department owner</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "<w:sectPr/></w:body></w:document>",
        )


def main() -> None:
    GENERATED.mkdir(parents=True, exist_ok=True)
    (GENERATED / "synthetic-policy.txt").write_text(
        "合成数据: 访客进入实验室前必须登记。\n紧急联系人信息不得写入公开知识库。\n",
        encoding="utf-8",
    )
    (GENERATED / "synthetic-handbook.md").write_text(
        "# 合成产品手册\n\n## 账号停用\n\n连续 90 天未登录的合成测试账号进入停用复核。\n\n"
        "| 状态 | 处理方式 |\n| --- | --- |\n| 待复核 | 通知所有者 |\n| 已停用 | 禁止登录 |\n",
        encoding="utf-8",
    )
    (GENERATED / "synthetic-text.pdf").write_bytes(text_pdf())
    (GENERATED / "synthetic-pdf-table.pdf").write_bytes(table_pdf())
    make_docx(GENERATED / "synthetic-table.docx")
    width, height, pixels = raster_text(("TEST POLICY",))
    (GENERATED / "synthetic-ocr.png").write_bytes(make_png(width, height, pixels))
    (GENERATED / "synthetic-scan.pdf").write_bytes(scanned_pdf(width, height, pixels))

    entries = []
    for path in sorted(GENERATED.iterdir()):
        content = path.read_bytes()
        entries.append(
            {
                "file": path.name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "synthetic": True,
            }
        )
    (ROOT / "manifest.json").write_text(
        json.dumps({"dataset_version": "p0-07-v1", "files": entries}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
