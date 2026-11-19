"""Small original vector icons rendered with the current Qt foreground colour."""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
 'open': '<path d="M3 7h7l2 3h9l-3 10H3z M3 7V4h7l2 3"/>',
 'export': '<path d="M12 3v12m-4-4 4 4 4-4 M4 15v6h16v-6"/>',
 'reset': '<path d="M4 8a9 9 0 1 1-1 7 M4 3v5h5"/>',
 'grid': '<path d="M3 3h18v18H3z M9 3v18m6-18v18M3 9h18M3 15h18"/>',
 'axes': '<path d="M6 18V3m0 15h15M6 18l10-10 M3 6l3-3 3 3m9 9 3 3-3 3"/>',
 'face': '<path d="m4 7 12-3 4 13-12 3z"/><path d="m12 10 7 3-4 2-2 4z" fill="currentColor"/>',
 'edge': '<path d="m3 18 7-12 10 10"/><circle cx="3" cy="18" r="2"/><circle cx="10" cy="6" r="2"/><circle cx="20" cy="16" r="2"/>',
 'clear': '<path d="M4 4h7M4 4v7m16-7h-7m7 0v7M4 20h7m-7 0v-7m16 7h-7m7 0v-7M9 9l6 6m0-6-6 6"/>',
 'geometry': '<path d="m12 2 9 5v10l-9 5-9-5V7z M3 7l9 5 9-5M12 12v10"/>',
 'faces': '<path d="m12 3 8 5v10l-8 4-8-4V8z" fill="currentColor" fill-opacity=".25"/><path d="m4 8 8 4 8-4M12 12v10"/>',
 'edges': '<path d="m12 2 9 5v10l-9 5-9-5V7z M3 7l9 5 9-5M12 12v10" stroke-dasharray="2 2"/>',
 'samples': ''.join(f'<circle cx="{x}" cy="{y}" r="1.5" fill="currentColor"/>' for x,y in [(5,5),(14,4),(20,8),(9,11),(4,17),(15,15),(20,20),(11,21)]),
 'region': '<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8" cy="8" r="1" fill="currentColor"/><circle cx="15" cy="10" r="1" fill="currentColor"/><circle cx="10" cy="16" r="1" fill="currentColor"/>',
 'isolate': '<circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2" fill="currentColor"/><path d="M12 1v4m0 14v4M1 12h4m14 0h4"/>',
 'context': '<path d="m12 4 7 4v8l-7 4-7-4V8z M5 8l7 4 7-4M12 12v8 M2 5V2h3m14 0h3v3M2 19v3h3m14 0h3v-3"/>',
}


def tool_icon(name, colour):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" color="{colour}" fill="none" stroke="{colour}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>'
    icon = QIcon()
    for size in (24,48):
        pixmap = QPixmap(size,size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        QSvgRenderer(QByteArray(svg.encode())).render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


BRAND_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" width="128" height="128" fill="none">
  <circle cx="64" cy="64" r="56" fill="#16282e" stroke="#4db8ad" stroke-width="5"/>
  <circle cx="64" cy="64" r="46" stroke="#c88a2b" stroke-width="1.8" stroke-opacity="0.7"/>
  <g fill="#ffc857">
    <polygon points="64,11 68,15 64,19 60,15"/>
    <polygon points="117,64 113,68 109,64 113,60"/>
    <polygon points="64,117 68,113 64,109 60,113"/>
    <polygon points="11,64 15,68 19,64 15,60"/>
  </g>
  <path d="M54 34 H78 M70 34 V80 L54 94 L40 82"
        stroke="#eef4f5" stroke-width="8.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <polygon points="88,56 95,64 88,72 81,64" fill="#ffc857"/>
</svg>"""


def app_icon():
    renderer = QSvgRenderer(QByteArray(BRAND_SVG.encode('utf-8')))
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
