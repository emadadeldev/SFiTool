"""Font Image Editor - standalone Qt application.

Features implemented (core subset requested):
- Load a single atlas PNG (Font Atlas) and specify cell size or auto-detect grid
- Visual grid, click cells to select
- Overlay Unicode character on each cell
- Import single glyph PNG into a cell (Auto Fit / Center, preserve alpha)
- Batch replace glyphs from a folder where filenames are Unicode codepoints (hex)
- Export modified atlas and JSON metadata (unicode -> cell rect)
- Padding, glyph width adjustment, RTL/LTR preview, drag & drop

This file implements the GUI and connects to `utils.py` helpers.
"""
from typing import Optional, Tuple, Dict
from PIL import ImageFont, ImageDraw
import sys
import os
import math
import unicodedata
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QWidget, QLabel, QPushButton,
    QHBoxLayout, QVBoxLayout, QSpinBox, QCheckBox, QLineEdit, QComboBox,
    QMessageBox, QFrame, QGridLayout, QTextEdit, QSizePolicy
)
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QAction
from PyQt6.QtCore import Qt, QSize, QPoint
from PIL import Image

try:
    # Try relative import first (when used as package)
    from .utils import (
        pil_to_rgba, split_grid, auto_detect_grid, export_metadata_json, image_to_bytes,
        detect_grid_from_metadata_files, parse_grid_from_metadata_text
    )
except ImportError:
    # Fall back to absolute import (when run as standalone script)
    from utils import (
        pil_to_rgba, split_grid, auto_detect_grid, export_metadata_json, image_to_bytes,
        detect_grid_from_metadata_files, parse_grid_from_metadata_text
    )


def pil_to_qpixmap(pil_image: Image.Image) -> QPixmap:
    img = pil_image.convert('RGBA')
    data = img.tobytes('raw', 'RGBA')
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())

# Atlas viewer 
class AtlasViewer(QWidget):
    """Widget that displays atlas pixmap with grid, supports clicks and drag-resize of rows/columns."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.atlas_pixmap: Optional[QPixmap] = None
        self.atlas_image: Optional[Image.Image] = None
        self.cols = 0
        self.rows = 0
        self.padding = 0
        self.selected = None  # (row, col)
        self.overlay_map = {}
        self.scale = 1.0

        # Per-column / per-row widths
        self.col_widths = []
        self.row_heights = []

        # Drag state
        self.dragging_edge = None  # ('col', index) or ('row', index)
        self.drag_start_pos = None
        self.drag_start_size = None

        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def set_atlas(self, pil_img: Image.Image, cell_w: int, cell_h: int, cols: int, rows: int, padding: int = 0):
        self.atlas_image = pil_img.convert('RGBA')
        self.atlas_pixmap = pil_to_qpixmap(self.atlas_image)
        self.cols = cols
        self.rows = rows
        self.padding = padding

        # Initialize per-column/row sizes
        self.col_widths = [cell_w] * cols
        self.row_heights = [cell_h] * rows
        self._update_scale()
        self.update()

    def _update_scale(self):
        if not self.atlas_image:
            self.scale = 1.0
            return
        img_w, img_h = self.atlas_image.size
        widget_w, widget_h = self.width(), self.height()
        self.scale = min(widget_w / max(1, img_w), widget_h / max(1, img_h), 1.0)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._update_scale()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 30))
        if not self.atlas_pixmap:
            painter.setPen(QColor(180, 180, 180))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop an atlas PNG here or open…")
            return

        # Draw scaled pixmap
        scaled_pixmap = self.atlas_pixmap.scaled(
            int(self.atlas_pixmap.width() * self.scale),
            int(self.atlas_pixmap.height() * self.scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        painter.drawPixmap(0, 0, scaled_pixmap)

        # Draw grid
        if self.atlas_image:
            pen = painter.pen()
            pen.setColor(QColor(100, 100, 120))
            pen.setWidth(1)
            painter.setPen(pen)

            # Vertical lines
            x = 0
            for w in self.col_widths:
                painter.drawLine(int(x*self.scale), 0, int(x*self.scale), int(self.atlas_image.size[1]*self.scale))
                x += w + self.padding
            # Last line
            painter.drawLine(int(x*self.scale), 0, int(x*self.scale), int(self.atlas_image.size[1]*self.scale))

            # Horizontal lines
            y = 0
            for h in self.row_heights:
                painter.drawLine(0, int(y*self.scale), int(self.atlas_image.size[0]*self.scale), int(y*self.scale))
                y += h + self.padding
            # Last line
            painter.drawLine(0, int(y*self.scale), int(self.atlas_image.size[0]*self.scale), int(y*self.scale))

        # Selection
        if self.selected:
            r, c = self.selected
            x = sum(self.col_widths[:c]) + self.padding*c
            y = sum(self.row_heights[:r]) + self.padding*r
            w = self.col_widths[c]
            h = self.row_heights[r]
            painter.fillRect(int(x*self.scale), int(y*self.scale), int(w*self.scale), int(h*self.scale), QColor(255, 200, 0, 60))
            painter.setPen(QColor(255, 200, 0))
            painter.drawRect(int(x*self.scale), int(y*self.scale), int(w*self.scale), int(h*self.scale))

        # Overlays
        painter.setPen(QColor(220, 220, 220))
        font = QFont()
        font.setPointSize(max(8, int(10*self.scale)))
        painter.setFont(font)
        for r in range(self.rows):
            y = sum(self.row_heights[:r]) + self.padding*r
            for c in range(self.cols):
                x = sum(self.col_widths[:c]) + self.padding*c
                idx = r*self.cols + c
                u = self.overlay_map.get(idx)
                if u is not None:
                    try:
                        ch = chr(u)
                    except Exception:
                        ch = ''
                    if ch:
                        painter.drawText(int((x+4)*self.scale), int((y+14)*self.scale), ch)

    def mousePressEvent(self, ev):
        if not self.atlas_pixmap or self.scale <= 0:
            return

        pos_x = ev.position().x() / self.scale
        pos_y = ev.position().y() / self.scale
        margin = 5

        # Check if user clicked near a vertical line
        x = 0
        for i, w in enumerate(self.col_widths):
            if abs(pos_x - x) <= margin:
                self.dragging_edge = ('col', i)
                self.drag_start_pos = pos_x
                self.drag_start_size = w
                return
            x += w + self.padding

        # Check horizontal lines
        y = 0
        for i, h in enumerate(self.row_heights):
            if abs(pos_y - y) <= margin:
                self.dragging_edge = ('row', i)
                self.drag_start_pos = pos_y
                self.drag_start_size = h
                return
            y += h + self.padding

        # Regular cell selection
        c = None
        r = None
        x_acc = 0
        for idx, w in enumerate(self.col_widths):
            if x_acc <= pos_x < x_acc + w:
                c = idx
                break
            x_acc += w + self.padding
        y_acc = 0
        for idx, h in enumerate(self.row_heights):
            if y_acc <= pos_y < y_acc + h:
                r = idx
                break
            y_acc += h + self.padding
        if r is not None and c is not None:
            self.selected = (r, c)
            self.update()

    def mouseMoveEvent(self, ev):
        if not self.dragging_edge:
            return
        pos = ev.position().x() / self.scale if self.dragging_edge[0] == 'col' else ev.position().y() / self.scale
        delta = pos - self.drag_start_pos
        new_size = max(1, self.drag_start_size + delta)
        if self.dragging_edge[0] == 'col':
            self.col_widths[self.dragging_edge[1]] = int(new_size)
        else:
            self.row_heights[self.dragging_edge[1]] = int(new_size)
        self.update()

    def mouseReleaseEvent(self, ev):
        self.dragging_edge = None
        self.drag_start_pos = None
        self.drag_start_size = None

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.accept()

    def dropEvent(self, ev):
        for url in ev.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith('.png'):
                try:
                    img = Image.open(path).convert('RGBA')
                    self.parent().load_atlas_image(img)
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to open image: {e}")
                break


# Main Window
class MainWindow(QMainWindow):

    # Window
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Font Image Editor")
        self.atlas_img: Optional[Image.Image] = None
        self.atlas_path: Optional[str] = None
        self.cell_w = 72
        self.cell_h = 72
        self.cols = 0
        self.rows = 0
        self.padding = 0
        self.overlay_map: Dict[int, int] = {}
        self.glyph_adv_map: Dict[int, int] = {}
        self._last_selected_idx: Optional[int] = None

        self._init_ui()

###########################################
# BEGIN FRONTEND
###########################################
    def _init_ui(self):
        
        open_act = QAction("Open Atlas", self)
        open_act.triggered.connect(self.open_atlas)
        save_act = QAction("Export Atlas + Metadata", self)
        save_act.triggered.connect(self.export_all)
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction(open_act)
        file_menu.addAction(save_act)

        central = QWidget()
        self.setCentralWidget(central)
        h = QHBoxLayout(central)

        # Left: viewer
        self.viewer = AtlasViewer(self)
        h.addWidget(self.viewer, 1)

        # Right: controls
        right = QVBoxLayout()

        # Atlas size / cell inputs
        form = QGridLayout()
        form.addWidget(QLabel("Cell Width"), 0, 0)
        self.cell_w_spin = QSpinBox(); self.cell_w_spin.setRange(1, 4096); self.cell_w_spin.setValue(self.cell_w)
        form.addWidget(self.cell_w_spin, 0, 1)
        form.addWidget(QLabel("Cell Height"), 1, 0)
        self.cell_h_spin = QSpinBox(); self.cell_h_spin.setRange(1, 4096); self.cell_h_spin.setValue(self.cell_h)
        form.addWidget(self.cell_h_spin, 1, 1)
        form.addWidget(QLabel("Padding"), 2, 0)
        self.padding_spin = QSpinBox(); self.padding_spin.setRange(0, 128); self.padding_spin.setValue(self.padding)
        form.addWidget(self.padding_spin, 2, 1)
        right.addLayout(form)

        # Character count inputs for improved auto-detect
        right.addWidget(QLabel("─── Detect by Character Count ───"))
        char_form = QGridLayout()
        char_form.addWidget(QLabel("Characters per Row"), 0, 0)
        self.chars_per_col_spin = QSpinBox(); self.chars_per_col_spin.setRange(1, 256); self.chars_per_col_spin.setValue(16)
        char_form.addWidget(self.chars_per_col_spin, 0, 1)
        char_form.addWidget(QLabel("Characters per Column"), 1, 0)
        self.chars_per_row_spin = QSpinBox(); self.chars_per_row_spin.setRange(1, 256); self.chars_per_row_spin.setValue(16)
        char_form.addWidget(self.chars_per_row_spin, 1, 1)
        right.addLayout(char_form)

        # Auto-detect and apply buttons
        autodetect_btn = QPushButton("Auto Detect Grid")
        autodetect_btn.clicked.connect(self.auto_detect)
        load_meta_btn = QPushButton("Load Grid Metadata")
        load_meta_btn.clicked.connect(self.load_grid_metadata)
        detect_by_count_btn = QPushButton("Detect by Character Count")
        detect_by_count_btn.clicked.connect(self.detect_by_char_count)
        apply_btn = QPushButton("Apply Grid")
        apply_btn.clicked.connect(self.apply_grid)
        right.addWidget(autodetect_btn)
        right.addWidget(load_meta_btn)
        right.addWidget(detect_by_count_btn)
        right.addWidget(apply_btn)

        # Selected cell actions
        self.selected_label = QLabel("Selected: -")
        right.addWidget(self.selected_label)

        # Unicode editor for selected cell
        unicode_layout = QHBoxLayout()
        unicode_layout.addWidget(QLabel("Unicode (hex):"))
        self.unicode_edit = QLineEdit()
        self.unicode_edit.setPlaceholderText("e.g., 0041 or 0627")
        self.unicode_edit.setMaxLength(8)
        self.unicode_edit.returnPressed.connect(self.update_selected_unicode)
        unicode_layout.addWidget(self.unicode_edit)
        unicode_btn = QPushButton("Set")
        unicode_btn.clicked.connect(self.update_selected_unicode)
        unicode_layout.addWidget(unicode_btn)
        right.addLayout(unicode_layout)

        import_btn = QPushButton("Import Glyph to Selected")
        import_btn.clicked.connect(self.import_glyph_to_selected)
        right.addWidget(import_btn)

        batch_btn = QPushButton("Batch Replace from Folder")
        batch_btn.clicked.connect(self.batch_replace_from_folder)
        right.addWidget(batch_btn)

        # Glyph advance adjustment
        width_layout = QHBoxLayout()
        width_layout.addWidget(QLabel("Default glyph advance:"))
        self.glyph_adv_spin = QSpinBox()
        self.glyph_adv_spin.setRange(0, 512)
        self.glyph_adv_spin.setValue(0)
        self.glyph_adv_spin.setSpecialValueText("Cell width")
        self.glyph_adv_spin.setToolTip(
            "Global fallback advance used when a glyph has no custom advance.\n"
            "Cell width = use current cell width."
        )
        width_layout.addWidget(self.glyph_adv_spin)
        right.addLayout(width_layout)

        selected_width_layout = QHBoxLayout()
        selected_width_layout.addWidget(QLabel("Selected glyph advance:"))
        self.sel_adv_dec_btn = QPushButton("-")
        self.sel_adv_dec_btn.setFixedWidth(32)
        self.sel_adv_dec_btn.setToolTip("Decrease selected glyph advance (down to Default)")
        self.sel_adv_dec_btn.clicked.connect(lambda: self.adjust_selected_glyph_width(-1))
        selected_width_layout.addWidget(self.sel_adv_dec_btn)
        self.sel_adv_spin = QSpinBox()
        self.sel_adv_spin.setRange(0, 512)
        self.sel_adv_spin.setValue(0)
        self.sel_adv_spin.setSpecialValueText("Default")
        self.sel_adv_spin.setToolTip("Per-glyph advance override. Default = use global default advance.")
        self.sel_adv_spin.valueChanged.connect(self.set_selected_glyph_width)
        selected_width_layout.addWidget(self.sel_adv_spin)
        self.sel_adv_inc_btn = QPushButton("+")
        self.sel_adv_inc_btn.setFixedWidth(32)
        self.sel_adv_inc_btn.setToolTip("Increase selected glyph advance")
        self.sel_adv_inc_btn.clicked.connect(lambda: self.adjust_selected_glyph_width(1))
        selected_width_layout.addWidget(self.sel_adv_inc_btn)
        self.sel_adv_reset_btn = QPushButton("Default")
        self.sel_adv_reset_btn.setToolTip("Reset selected glyph to use global default advance")
        self.sel_adv_reset_btn.clicked.connect(lambda: self.sel_adv_spin.setValue(0))
        selected_width_layout.addWidget(self.sel_adv_reset_btn)
        right.addLayout(selected_width_layout)
        self.sel_adv_spin.setEnabled(False)
        self.sel_adv_dec_btn.setEnabled(False)
        self.sel_adv_inc_btn.setEnabled(False)
        self.sel_adv_reset_btn.setEnabled(False)

        # Preview text
        right.addWidget(QLabel("Preview Text:"))
        self.preview_edit = QLineEdit()
        self.preview_edit.setText("مرحبا Hello 123")
        right.addWidget(self.preview_edit)
        self.rtl_cb = QCheckBox("RTL")
        right.addWidget(self.rtl_cb)
        preview_btn = QPushButton("Render Preview")
        preview_btn.clicked.connect(self.render_preview)
        right.addWidget(preview_btn)
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(360, 80)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.preview_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.preview_label.setStyleSheet("QLabel { background-color: rgb(40, 40, 40); }")
        right.addWidget(self.preview_label)

        right.addStretch()
        h.addLayout(right)

        # Connect some updates
        self.cell_w_spin.valueChanged.connect(lambda v: setattr(self, 'cell_w', v))
        self.cell_h_spin.valueChanged.connect(lambda v: setattr(self, 'cell_h', v))
        self.padding_spin.valueChanged.connect(lambda v: setattr(self, 'padding', v))

        # Timer to update selected label when viewer selection changes
        from PyQt6.QtCore import QTimer
        self._sel_timer = QTimer(self)
        self._sel_timer.setInterval(150)
        self._sel_timer.timeout.connect(self._update_selected_label)
        self._sel_timer.start()

###########################################
# END FRONTEND
###########################################

###########################################
# BEGIN BACKEND
###########################################
    def _parse_codepoint_input(self, text: str) -> int:
        """Parse unicode input as hex (with optional U+) or as a single literal character."""
        raw = text.strip()
        if not raw:
            raise ValueError("empty input")

        if raw.upper().startswith('U+'):
            raw = raw[2:]

        if not raw:
            raise ValueError("empty input")

        try:
            return int(raw, 16)
        except ValueError:
            # Fallback: allow entering a literal character (for example: ا)
            if len(raw) == 1:
                return ord(raw)
            raise

    def _find_glyph_index_for_codepoint(self, codepoint: int, preferred_idx: Optional[int] = None) -> Optional[int]:
        """Find glyph by exact codepoint first, then Arabic compatibility-normalized match."""
        overlay = self.viewer.overlay_map

        if preferred_idx is not None and overlay.get(preferred_idx) == codepoint:
            return preferred_idx

        for glyph_idx, unicode_val in overlay.items():
            if unicode_val == codepoint:
                return glyph_idx

        try:
            target_norm = unicodedata.normalize('NFKC', chr(codepoint))
        except Exception:
            return None

        for glyph_idx, unicode_val in overlay.items():
            try:
                if unicodedata.normalize('NFKC', chr(unicode_val)) == target_norm:
                    return glyph_idx
            except Exception:
                continue

        return None

    def _selected_index(self) -> Optional[int]:
        sel = self.viewer.selected
        if not sel:
            return None
        r, c = sel
        return r * self.viewer.cols + c

    def _effective_glyph_advance(self, idx: int) -> int:
        adv = self.glyph_adv_map.get(idx, 0)
        if adv <= 0:
            adv = self.glyph_adv_spin.value() or self.cell_w
        return max(1, int(adv))

    def set_selected_glyph_width(self, value: int):
        idx = self._selected_index()
        if idx is None:
            return
        if value <= 0:
            self.glyph_adv_map.pop(idx, None)
        else:
            self.glyph_adv_map[idx] = int(value)

    def adjust_selected_glyph_width(self, delta: int):
        idx = self._selected_index()
        if idx is None:
            QMessageBox.warning(self, "Warning", "Select a cell first")
            return
        current = self.sel_adv_spin.value()
        if current <= 0:
            current = self.glyph_adv_spin.value() or self.cell_w
        new_value = max(0, min(512, current + delta))
        self.sel_adv_spin.setValue(new_value)

    def _update_selected_label(self):
        sel = self.viewer.selected
        if sel:
            r, c = sel
            idx = r * self.viewer.cols + c
            selection_changed = idx != self._last_selected_idx
            self._last_selected_idx = idx
            u = self.viewer.overlay_map.get(idx)
            ch = chr(u) if u else '-'
            self.selected_label.setText(f"Selected: {r},{c} (#{idx}) {ch}")
            # Only sync when selection changes; avoid timer overwriting user edits.
            if selection_changed:
                new_text = f"{u:04X}" if u is not None else ""
                if self.unicode_edit.text() != new_text:
                    self.unicode_edit.setText(new_text)
                self.unicode_edit.setModified(False)
                self.sel_adv_spin.blockSignals(True)
                self.sel_adv_spin.setValue(self.glyph_adv_map.get(idx, 0))
                self.sel_adv_spin.blockSignals(False)
            self.sel_adv_spin.setEnabled(True)
            self.sel_adv_dec_btn.setEnabled(True)
            self.sel_adv_inc_btn.setEnabled(True)
            self.sel_adv_reset_btn.setEnabled(True)
        else:
            self.selected_label.setText("Selected: -")
            selection_cleared = self._last_selected_idx is not None
            self._last_selected_idx = None
            if selection_cleared and self.unicode_edit.text():
                self.unicode_edit.clear()
                self.unicode_edit.setModified(False)
            self.sel_adv_spin.blockSignals(True)
            self.sel_adv_spin.setValue(0)
            self.sel_adv_spin.blockSignals(False)
            self.sel_adv_spin.setEnabled(False)
            self.sel_adv_dec_btn.setEnabled(False)
            self.sel_adv_inc_btn.setEnabled(False)
            self.sel_adv_reset_btn.setEnabled(False)

    def update_selected_unicode(self):
        """Update the unicode value for the currently selected cell."""
        sel = self.viewer.selected
        if not sel:
            QMessageBox.warning(self, "Warning", "Select a cell first")
            return
        
        text = self.unicode_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "Warning", "Enter a unicode value (hex)")
            return
        
        # Parse hex input - accept with or without U+ prefix
        try:
            codepoint = self._parse_codepoint_input(text)
            
            # Validate codepoint range
            if codepoint < 0 or codepoint > 0x10FFFF:
                QMessageBox.warning(self, "Warning", "Unicode codepoint must be between 0 and 0x10FFFF")
                return
            
            # Update the overlay map
            r, c = sel
            idx = r * self.viewer.cols + c
            self.overlay_map[idx] = codepoint
            self.viewer.overlay_map[idx] = codepoint
            
            # Refresh the viewer
            self.viewer.update()
            
            # Update the label to show the new character
            self._update_selected_label()
            self.unicode_edit.setText(f"{codepoint:04X}")
            self.unicode_edit.setModified(False)
            
            QMessageBox.information(self, "Success", f"Unicode set to U+{codepoint:04X} ({chr(codepoint)})")
        except ValueError:
            QMessageBox.warning(self, "Warning", f"Invalid hex value: {text}")
        except Exception as e:
            QMessageBox.warning(self, "Warning", f"Error: {e}")

    def open_atlas(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Atlas PNG", "", "PNG Files (*.png);;All Images (*.*)")
        if not path:
            return
        try:
            img = Image.open(path).convert('RGBA')
            self.atlas_img = img
            self.atlas_path = path
            # default: use spins values
            self.apply_grid()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open: {e}")

    def load_atlas_image(self, img: Image.Image):
        self.atlas_img = img
        self.atlas_path = None
        self.apply_grid()

    def auto_detect(self):
        if not self.atlas_img:
            QMessageBox.warning(self, "Warning", "Load an atlas first")
            return

        source = "image heuristic"
        detected = None
        if self.atlas_path:
            detected = detect_grid_from_metadata_files(self.atlas_path, self.atlas_img.size)
        if detected:
            cell_w, cell_h, cols, rows, source_path = detected
            source = f"metadata ({os.path.basename(source_path)})"
        else:
            cell_w, cell_h, cols, rows = auto_detect_grid(self.atlas_img)

        self.cell_w_spin.setValue(cell_w)
        self.cell_h_spin.setValue(cell_h)
        self.chars_per_col_spin.setValue(max(1, min(256, cols)))
        self.chars_per_row_spin.setValue(max(1, min(256, rows)))
        self.apply_grid()
        QMessageBox.information(
            self,
            "Auto Detect",
            f"Detected cell {cell_w}x{cell_h}, {cols}x{rows} cells\nSource: {source}",
        )

    def load_grid_metadata(self):
        """Load a metadata file manually and apply grid values from it."""
        if not self.atlas_img:
            QMessageBox.warning(self, "Warning", "Load an atlas first")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Grid Metadata",
            "",
            "Metadata Files (*.yaml *.yml *.json *.txt *.meta *.cfg *.fnt);;All Files (*.*)",
        )
        if not path:
            return

        text = None
        for enc in ('utf-8-sig', 'utf-8', 'utf-16', 'cp1252'):
            try:
                with open(path, 'r', encoding=enc) as f:
                    text = f.read()
                break
            except Exception:
                continue

        if text is None:
            QMessageBox.critical(self, "Error", "Failed to read metadata file")
            return

        grid = parse_grid_from_metadata_text(text, self.atlas_img.size)
        if not grid:
            QMessageBox.warning(
                self,
                "Metadata",
                "Could not find usable grid fields in metadata.\n"
                "Expected fields like Cell_Width/Cell_Height and RowCount/ColumnCount.",
            )
            return

        cell_w, cell_h, cols, rows = grid
        self.cell_w_spin.setValue(cell_w)
        self.cell_h_spin.setValue(cell_h)
        self.chars_per_col_spin.setValue(max(1, min(256, cols)))
        self.chars_per_row_spin.setValue(max(1, min(256, rows)))
        self.apply_grid()
        QMessageBox.information(
            self,
            "Metadata Loaded",
            f"Applied grid from {os.path.basename(path)}\n"
            f"Cell: {cell_w}x{cell_h}, Grid: {cols}x{rows}",
        )

    def detect_by_char_count(self):
        """Calculate cell size based on number of characters (columns and rows)."""
        if not self.atlas_img:
            QMessageBox.warning(self, "Warning", "Load an atlas first")
            return
        
        cols = self.chars_per_col_spin.value()
        rows = self.chars_per_row_spin.value()
        w, h = self.atlas_img.size
        
        # Calculate cell dimensions by dividing image size by character count
        cell_w = w // cols
        cell_h = h // rows
        
        # Update spinboxes
        self.cell_w_spin.setValue(max(1, cell_w))
        self.cell_h_spin.setValue(max(1, cell_h))
        self.padding_spin.setValue(0)  # Reset padding
        
        QMessageBox.information(self, "Detect by Count", 
                              f"Calculated cell size: {cell_w}×{cell_h}px\n"
                              f"Grid: {cols}×{rows} characters")
        self.apply_grid()

    def apply_grid(self):
        if not self.atlas_img:
            QMessageBox.warning(self, "Warning", "Load an atlas first")
            return
        self.cell_w = self.cell_w_spin.value()
        self.cell_h = self.cell_h_spin.value()
        self.padding = self.padding_spin.value()
        w, h = self.atlas_img.size
        
        # Calculate grid dimensions
        cols = max(1, (w - self.padding) // (self.cell_w + self.padding))
        rows = max(1, (h - self.padding) // (self.cell_h + self.padding))
        
        self.cols = cols
        self.rows = rows
        # if no mapping exists, auto fill sequentially starting at space (0x20)
        if not self.overlay_map:
            total = cols * rows
            self.overlay_map = {i: 0x20 + i for i in range(total)}
        else:
            total = cols * rows
            self.overlay_map = {i: u for i, u in self.overlay_map.items() if 0 <= i < total}
        self.glyph_adv_map = {i: a for i, a in self.glyph_adv_map.items() if 0 <= i < cols * rows}
        self.viewer.overlay_map = self.overlay_map
        self.viewer.set_atlas(self.atlas_img, self.cell_w, self.cell_h, cols, rows, self.padding)
        
        # Show grid info in status
        coverage_w = (cols * self.cell_w + (cols - 1) * self.padding) / w * 100
        coverage_h = (rows * self.cell_h + (rows - 1) * self.padding) / h * 100
        print(f"Grid: {cols}×{rows} cells, coverage {coverage_w:.1f}% × {coverage_h:.1f}%")

    def import_glyph_to_selected(self):
        sel = self.viewer.selected
        if not sel:
            QMessageBox.warning(self, "Warning", "Select a cell first")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import Glyph PNG", "", "PNG Files (*.png);;All Images (*.*)")
        if not path:
            return
        try:
            glyph = Image.open(path).convert('RGBA')
            # Auto-fit: scale preserving aspect ratio to fit into cell
            gw, gh = glyph.size
            max_w = self.cell_w
            max_h = self.cell_h
            ratio = min(max_w / max(1, gw), max_h / max(1, gh))
            new_w = int(gw * ratio)
            new_h = int(gh * ratio)
            glyph = glyph.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Paste centered into atlas, replacing the cell background
            r, c = sel
            x = c * (self.cell_w + self.padding)
            y = r * (self.cell_h + self.padding)
            
            atlas = self.atlas_img.copy()
            cx = (self.cell_w - new_w) // 2
            cy = (self.cell_h - new_h) // 2
            # Build a fresh transparent cell, then overwrite the atlas cell in one paste.
            # This fully removes any previous pixels from that cell.
            new_cell = Image.new('RGBA', (self.cell_w, self.cell_h), (0, 0, 0, 0))
            new_cell.paste(glyph, (cx, cy), glyph)
            atlas.paste(new_cell, (x, y))
            self.atlas_img = atlas
            self.apply_grid()
            QMessageBox.information(self, "Imported", "Glyph imported and placed into selected cell")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to import glyph: {e}")

    def batch_replace_from_folder(self):
        if not self.atlas_img:
            QMessageBox.warning(self, "Warning", "Load an atlas first")
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Folder with PNGs")
        if not folder:
            return
        replaced = 0
        skipped = 0
        for fn in os.listdir(folder):
            if not fn.lower().endswith('.png'):
                continue
            name = os.path.splitext(fn)[0]
            try:
                # accept hex like 0627 or U+0627
                if name.upper().startswith('U+'):
                    hexpart = name[2:]
                else:
                    hexpart = name
                codepoint = int(hexpart, 16)
            except Exception:
                skipped += 1
                continue

            # find glyph index if present in overlay_map by value
            found_index = None
            for idx, u in self.overlay_map.items():
                if u == codepoint:
                    found_index = idx
                    break
            if found_index is None:
                # if not mapped, assume sequential placement: use codepoint as index if small
                # fallback: skip
                skipped += 1
                continue

            # determine row/col
            idx = found_index
            r = idx // self.cols
            c = idx % self.cols
            path = os.path.join(folder, fn)
            try:
                glyph = Image.open(path).convert('RGBA')
                gw, gh = glyph.size
                ratio = min(self.cell_w / max(1, gw), self.cell_h / max(1, gh))
                glyph = glyph.resize((int(gw * ratio), int(gh * ratio)), Image.Resampling.LANCZOS)
                
                x = c * (self.cell_w + self.padding)
                y = r * (self.cell_h + self.padding)
                
                atlas = self.atlas_img.copy()
                cx = (self.cell_w - glyph.size[0]) // 2
                cy = (self.cell_h - glyph.size[1]) // 2
                # Build a fresh transparent cell, then overwrite the atlas cell in one paste.
                new_cell = Image.new('RGBA', (self.cell_w, self.cell_h), (0, 0, 0, 0))
                new_cell.paste(glyph, (cx, cy), glyph)
                atlas.paste(new_cell, (x, y))
                self.atlas_img = atlas
                replaced += 1
            except Exception:
                skipped += 1

        if replaced:
            self.apply_grid()
        QMessageBox.information(self, "Batch Replace", f"Replaced {replaced}, skipped {skipped}")

    def export_all(self):
        if not self.atlas_img:
            QMessageBox.warning(self, "Warning", "No atlas loaded")
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Export Folder")
        if not folder:
            return
        atlas_path = os.path.join(folder, 'atlas_modified.png')
        self.atlas_img.save(atlas_path)

        # build metadata: map unicode -> rect
        mapping = {}
        for idx, u in self.viewer.overlay_map.items():
            r = idx // self.viewer.cols
            c = idx % self.viewer.cols
            x = c * (self.viewer.cell_w + self.viewer.padding)
            y = r * (self.viewer.cell_h + self.viewer.padding)
            mapping[f"U+{u:04X}"] = {
                "unicode": u,
                "index": idx,
                "x": x,
                "y": y,
                "width": self.viewer.cell_w,
                "height": self.viewer.cell_h,
                "advance": self._effective_glyph_advance(idx),
            }

        meta_path = os.path.join(folder, 'atlas_metadata.json')
        export_metadata_json(mapping, meta_path)
        QMessageBox.information(self, "Exported", f"Atlas and metadata exported to {folder}")

    def render_preview(self):
        # Apply pending unicode edit so preview reflects the latest typed hex.
        if self.viewer.selected:
            pending_text = self.unicode_edit.text().strip()
            try:
                r, c = self.viewer.selected
                idx = r * self.viewer.cols + c
                current = self.viewer.overlay_map.get(idx)

                if pending_text:
                    codepoint = self._parse_codepoint_input(pending_text)
                    if codepoint < 0 or codepoint > 0x10FFFF:
                        QMessageBox.warning(self, "Warning", "Unicode codepoint must be between 0 and 0x10FFFF")
                        return

                    # Commit if text differs from current mapping (don't rely on isModified state).
                    if current != codepoint:
                        self.overlay_map[idx] = codepoint
                        self.viewer.overlay_map[idx] = codepoint
                        self.viewer.update()
                        self.unicode_edit.setText(f"{codepoint:04X}")
                        self.unicode_edit.setModified(False)
                        self._update_selected_label()
            except ValueError:
                QMessageBox.warning(self, "Warning", f"Invalid hex value: {pending_text}")
                return

        text = self.preview_edit.text()
        if not text or not self.atlas_img:
            return
        
        rtl = self.rtl_cb.isChecked()
        glyph_advance = self.glyph_adv_spin.value() or self.cell_w
        
        # Render the text using glyphs from the atlas
        text_to_render = text[::-1] if rtl else text
        imgs = []
        selected_idx = None
        if self.viewer.selected:
            sr, sc = self.viewer.selected
            selected_idx = sr * self.viewer.cols + sc
        
        for ch in text_to_render:
            code = ord(ch)
            idx = self._find_glyph_index_for_codepoint(code, selected_idx)
            
            if idx is None:
                # Character not mapped - add a space/blank
                imgs.append(Image.new('RGBA', (glyph_advance, self.cell_h), (0, 0, 0, 0)))
            else:
                effective_advance = self._effective_glyph_advance(idx)
                # Extract this glyph from atlas
                r = idx // self.viewer.cols
                c = idx % self.viewer.cols
                x = c * (self.viewer.cell_w + self.viewer.padding)
                y = r * (self.viewer.cell_h + self.viewer.padding)
                
                # Crop the glyph cell from the atlas
                box = (x, y, x + self.viewer.cell_w, y + self.viewer.cell_h)
                glyph = self.atlas_img.crop(box)
                
                # Apply glyph-specific advance width (or default advance fallback).
                if effective_advance != self.viewer.cell_w:
                    padded = Image.new('RGBA', (effective_advance, self.viewer.cell_h), (0, 0, 0, 0))
                    offset = (effective_advance - glyph.size[0]) // 2
                    padded.paste(glyph, (offset, 0), glyph)
                    imgs.append(padded)
                else:
                    imgs.append(glyph)
        
        if not imgs:
            return
        
        # Combine all glyphs horizontally
        total_w = sum(i.size[0] for i in imgs)
        max_h = max(i.size[1] for i in imgs) if imgs else self.cell_h
        
        preview_img = Image.new('RGBA', (total_w, max_h), (40, 40, 40, 255))
        x_pos = 0
        for img in imgs:
            # Paste each glyph, centered vertically if needed
            y_offset = (max_h - img.size[1]) // 2
            preview_img.paste(img, (x_pos, y_offset), img)
            x_pos += img.size[0]
        
        # Display preview fitted into a fixed box so control buttons never resize.
        target_w = max(1, self.preview_label.width() - 4)
        target_h = max(1, self.preview_label.height() - 4)
        fitted = preview_img.copy()
        fitted.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
        self.preview_label.setPixmap(pil_to_qpixmap(fitted))
###########################################
# End BACKEND
###########################################

def main(argv=None):
    app = QApplication(argv or sys.argv)
    win = MainWindow()
    win.resize(1100, 700)
    win.show()
    return app.exec()

if __name__ == '__main__':
    sys.exit(main())
