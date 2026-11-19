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
  <path d="M64 7 L111 34 C114.5 36 116.5 39.7 116.5 43.8 V84.2 C116.5 88.3 114.5 92 111 94 L64 121 L17 94 C13.5 92 11.5 88.3 11.5 84.2 V43.8 C11.5 39.7 13.5 36 17 34 Z"
        fill="#144d54" stroke="#52cbc0" stroke-width="4"/>
  <circle cx="64" cy="64" r="39" fill="#237a84" fill-opacity="0.35" stroke="#7ce5d9" stroke-width="2.4"/>
  <circle cx="64" cy="64" r="20" fill="#13252b" stroke="#ffc857" stroke-width="3"/>
  <g stroke="#ffc857" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
    <path d="M64 25 V14 M60 18 L64 14 L68 18"/>
    <path d="M103 64 H114 M110 60 L114 64 L110 68"/>
    <path d="M64 44 V52 M61 49 L64 52 L67 49"/>
  </g>
  <g fill="#7ef0e3" stroke="#0f282e" stroke-width="1.2">
    <circle cx="64" cy="34" r="3.4"/><circle cx="85" cy="43" r="3.4"/><circle cx="94" cy="64" r="3.4"/>
    <circle cx="85" cy="85" r="3.4"/><circle cx="64" cy="94" r="3.4"/><circle cx="43" cy="85" r="3.4"/>
    <circle cx="34" cy="64" r="3.4"/><circle cx="43" cy="43" r="3.4"/>
  </g>
  <g fill="#ffc857" stroke="#13252b" stroke-width="1.4">
    <circle cx="64" cy="44" r="3.8"/><circle cx="84" cy="64" r="3.8"/><circle cx="64" cy="84" r="3.8"/><circle cx="44" cy="64" r="3.8"/>
    <circle cx="64" cy="25" r="3.8"/><circle cx="103" cy="64" r="3.8"/><circle cx="64" cy="103" r="3.8"/><circle cx="25" cy="64" r="3.8"/>
  </g>
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
