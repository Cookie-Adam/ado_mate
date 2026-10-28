"""The little chat box that floats next to her."""
import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

STYLE = """
QWidget#box { background: #151823; border: 1px solid #3b4a7a; border-radius: 12px; }
QLabel#title { color: #8fb4ff; font-weight: bold; }
QPushButton#close { background: transparent; color: #8a90a6; border: none; font-size: 14px; }
QPushButton#close:hover { color: #ff8a8a; }
QTextBrowser { background: #0f111a; color: #e6e8f2; border: none; border-radius: 8px; padding: 4px; font-size: 13px; }
QLineEdit { background: #0f111a; color: #ffffff; border: 1px solid #3b4a7a; border-radius: 8px; padding: 6px; font-size: 13px; }
"""


class ChatBox(QWidget):
    submitted = Signal(str)
    closed = Signal()

    W, H = 330, 300

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)

        frame = QWidget(self)
        frame.setObjectName("box")
        frame.setGeometry(0, 0, self.W, self.H)
        frame.setStyleSheet(STYLE)

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 10)
        top = QHBoxLayout()
        title = QLabel("ado:3")
        title.setObjectName("title")
        close = QPushButton("✕")
        close.setObjectName("close")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.close_box)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(close)
        self.view = QTextBrowser()
        self.input = QLineEdit()
        self.input.setPlaceholderText("say something...  (Enter to send, Esc to close)")
        self.input.returnPressed.connect(self.send)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.input)

        self.log = []          # (who, text)
        self.busy = False

    # ---- content ----
    def render(self):
        parts = []
        for who, text in self.log:
            t = html.escape(text).replace("\n", "<br>")
            if who == "you":
                parts.append(f'<p style="margin:4px 0"><b style="color:#7fd1a8">you</b>&nbsp; {t}</p>')
            elif who == "err":
                parts.append(f'<p style="margin:4px 0;color:#ff9a9a"><i>{t}</i></p>')
            else:
                parts.append(f'<p style="margin:4px 0"><b style="color:#8fb4ff">ado</b>&nbsp; {t}</p>')
        self.view.setHtml("".join(parts))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def load(self, history):
        self.log = [("you" if m["role"] == "user" else "ado", m["content"]) for m in history]
        self.render()

    def add_user(self, text):
        self.log.append(("you", text))
        self.log.append(("ado", "..."))
        self.render()

    def set_reply(self, text):
        if self.log and self.log[-1][0] == "ado":
            self.log[-1] = ("ado", text or "...")
            self.render()

    def set_error(self, text):
        if self.log and self.log[-1] == ("ado", "..."):
            self.log.pop()
        self.log.append(("err", text))
        self.render()

    # ---- actions ----
    def send(self):
        text = self.input.text().strip()
        if not text or self.busy:
            return
        self.input.clear()
        self.submitted.emit(text)

    def set_busy(self, busy):
        self.busy = busy
        self.input.setPlaceholderText("she's thinking..." if busy else
                                      "say something...  (Enter to send, Esc to close)")

    def close_box(self):
        self.hide()
        self.closed.emit()

    def open_box(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_box()
        else:
            super().keyPressEvent(e)
