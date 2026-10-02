"""Draw the app icon (assets/icon.ico + icon.png) with Qt. Run once; the result is committed."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

ROOT = Path(__file__).resolve().parent


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor("#ffb627"))
    g.setColorAt(1, QColor("#ff6b35"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 52 * s, 52 * s)
    # 2x2 collage of tiles
    p.setBrush(QColor(20, 20, 26, 235))
    for i, (x, y) in enumerate(((36, 36), (132, 36), (36, 132), (132, 132))):
        p.drawRoundedRect(QRectF(x * s, y * s, 88 * s, 88 * s), 16 * s, 16 * s)
    # musical note across the tiles
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(98 * s, 176 * s), 30 * s, 24 * s)
    p.drawEllipse(QPointF(186 * s, 154 * s), 30 * s, 24 * s)
    pen = QPen(QColor("#ffffff"), 14 * s, Qt.SolidLine, Qt.FlatCap)
    p.setPen(pen)
    p.drawLine(QPointF(124 * s, 172 * s), QPointF(124 * s, 66 * s))
    p.drawLine(QPointF(212 * s, 150 * s), QPointF(212 * s, 46 * s))
    p.setPen(Qt.NoPen)
    p.drawPolygon([QPointF(117 * s, 60 * s), QPointF(219 * s, 38 * s), QPointF(219 * s, 64 * s), QPointF(117 * s, 86 * s)])
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def main() -> None:
    QGuiApplication(sys.argv)
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [png_bytes(draw(n)) for n in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries, offset = b"", 6 + 16 * len(sizes)
    for n, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    (out / "icon.ico").write_bytes(header + entries + b"".join(pngs))
    draw(512).save(str(out / "icon.png"))
    print("wrote", out / "icon.ico")


if __name__ == "__main__":
    main()
