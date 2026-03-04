"""Core image utilities used by the editor.

Contains PIL-based helpers for converting to/from QPixmap, splitting
an atlas into grid cells, simple auto-detect heuristics, and metadata
export helpers.
"""
from typing import Any, Tuple, List, Dict, Optional
from PIL import Image
import io
import json
import os
import re

try:
    import yaml
except Exception:
    yaml = None


def pil_to_rgba(img: Image.Image) -> Image.Image:
    if img.mode != 'RGBA':
        return img.convert('RGBA')
    return img


def image_to_bytes(img: Image.Image, fmt: str = 'PNG') -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def split_grid(atlas: Image.Image, cell_w: int, cell_h: int,
               cols: Optional[int] = None, rows: Optional[int] = None,
               padding: int = 0) -> List[Image.Image]:
    """Split `atlas` into cells using given cell size and optional padding.

    Returns a list of PIL Images in row-major order.
    """
    atlas = pil_to_rgba(atlas)
    w, h = atlas.size
    if cols is None:
        cols = w // (cell_w + padding)
    if rows is None:
        rows = h // (cell_h + padding)

    cells = []
    for r in range(rows):
        for c in range(cols):
            x = c * (cell_w + padding)
            y = r * (cell_h + padding)
            box = (x, y, x + cell_w, y + cell_h)
            cell = atlas.crop(box)
            cells.append(cell)
    return cells


def _normalize_meta_key(key: str) -> str:
    return ''.join(ch.lower() for ch in str(key) if ch.isalnum())


def _try_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip().strip(',').strip('"').strip("'")
        if re.fullmatch(r'[+-]?\d+', raw):
            return int(raw)
    return None


def _collect_numeric_meta_values(obj: Any, out: Dict[str, int]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            norm_key = _normalize_meta_key(str(key))
            parsed = _try_int(value)
            if norm_key and parsed is not None and norm_key not in out:
                out[norm_key] = parsed
            _collect_numeric_meta_values(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_numeric_meta_values(item, out)


def _first_meta_value(values: Dict[str, int], keys: Tuple[str, ...]) -> Optional[int]:
    for key in keys:
        if key in values:
            return values[key]
    return None


def parse_grid_from_metadata_text(
    text: str,
    atlas_size: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """Parse grid dimensions from metadata text.

    Supports YAML-like `Key: Value` text and nested sections like:
    `Texture_Glyph: -> Cell_Width/Cell_Height/RowCount/ColumnCount`.
    """
    values: Dict[str, int] = {}

    if yaml is not None:
        try:
            loaded = yaml.safe_load(text)
            _collect_numeric_meta_values(loaded, values)
        except Exception:
            pass

    for raw_line in text.splitlines():
        line = raw_line.split('#', 1)[0].split('//', 1)[0]
        if ':' not in line:
            continue
        key, raw_value = line.split(':', 1)
        norm_key = _normalize_meta_key(key)
        parsed = _try_int(raw_value)
        if norm_key and parsed is not None and norm_key not in values:
            values[norm_key] = parsed

    if not values:
        return None

    cell_w = _first_meta_value(values, (
        'cellwidth',
        'glyphcellwidth',
        'defaultglyphwidth',
        'defaultcharwidth',
        'fontwidth',
    ))
    cell_h = _first_meta_value(values, (
        'cellheight',
        'glyphcellheight',
        'fontheight',
        'linefeed',
    ))
    cols = _first_meta_value(values, (
        'columncount',
        'columns',
        'cellsperrow',
        'charsperrow',
        'charactersperrow',
    ))
    rows = _first_meta_value(values, (
        'rowcount',
        'rows',
        'cellspercolumn',
        'charspercolumn',
        'characterspercolumn',
    ))
    sheet_w = _first_meta_value(values, ('sheetwidth', 'texturewidth', 'atlaswidth'))
    sheet_h = _first_meta_value(values, ('sheetheight', 'textureheight', 'atlasheight'))

    atlas_w = atlas_h = None
    if atlas_size:
        atlas_w, atlas_h = atlas_size

    if cell_w and sheet_w and not cols and cell_w > 0:
        cols = max(1, sheet_w // cell_w)
    if cell_h and sheet_h and not rows and cell_h > 0:
        rows = max(1, sheet_h // cell_h)

    # Trust atlas dimensions when metadata counts are inconsistent.
    if cell_w and atlas_w and (not cols or cols * cell_w > atlas_w):
        cols = max(1, atlas_w // cell_w)
    if cell_h and atlas_h and (not rows or rows * cell_h > atlas_h):
        rows = max(1, atlas_h // cell_h)

    if cols and not cell_w:
        if atlas_w:
            cell_w = max(1, atlas_w // cols)
        elif sheet_w:
            cell_w = max(1, sheet_w // cols)
    if rows and not cell_h:
        if atlas_h:
            cell_h = max(1, atlas_h // rows)
        elif sheet_h:
            cell_h = max(1, sheet_h // rows)

    if not cell_w or not cell_h or not cols or not rows:
        return None

    if atlas_w:
        max_cols = max(1, atlas_w // max(1, cell_w))
        cols = min(cols, max_cols)
    if atlas_h:
        max_rows = max(1, atlas_h // max(1, cell_h))
        rows = min(rows, max_rows)

    if cell_w <= 0 or cell_h <= 0 or cols <= 0 or rows <= 0:
        return None
    return cell_w, cell_h, cols, rows


def detect_grid_from_metadata_files(
    atlas_path: str,
    atlas_size: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int, int, int, str]]:
    """Try sidecar metadata files next to the atlas and extract grid values."""
    if not atlas_path:
        return None

    atlas_path = os.path.abspath(atlas_path)
    folder = os.path.dirname(atlas_path)
    stem = os.path.splitext(os.path.basename(atlas_path))[0]
    exts = ('.yaml', '.yml', '.json', '.txt', '.meta', '.cfg', '.fnt')

    candidates: List[str] = []
    for ext in exts:
        candidates.append(os.path.join(folder, f'{stem}{ext}'))

    for base in ('metadata', 'atlas_metadata'):
        for ext in exts:
            candidates.append(os.path.join(folder, f'{base}{ext}'))

    sheet_match = re.match(r'^(.*?)[_-]?sheet\d+$', stem, flags=re.IGNORECASE)
    if sheet_match:
        prefix = sheet_match.group(1)
        for ext in exts:
            candidates.append(os.path.join(folder, f'{prefix}{ext}'))
            candidates.append(os.path.join(folder, f'{prefix}_metadata{ext}'))

    seen = set()
    ordered_candidates = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered_candidates.append(candidate)

    for candidate in ordered_candidates:
        if not os.path.isfile(candidate):
            continue

        text = None
        for enc in ('utf-8-sig', 'utf-8', 'utf-16', 'cp1252'):
            try:
                with open(candidate, 'r', encoding=enc) as f:
                    text = f.read()
                break
            except Exception:
                continue
        if text is None:
            continue

        grid = parse_grid_from_metadata_text(text, atlas_size=atlas_size)
        if grid:
            return (*grid, candidate)

    return None


def auto_detect_grid(atlas: Image.Image, max_cell: int = 512) -> Tuple[int, int, int, int]:
    """Auto-detect grid by analyzing repetitive spacing patterns in glyphs.

    Analyzes vertical/horizontal alpha projections to find:
    1. Horizontal and vertical separators (low-content lines)
    2. Most common cell width and height
    3. Number of columns and rows based on separators
    
    Returns (cell_w, cell_h, cols, rows).
    """
    atlas = pil_to_rgba(atlas)
    w, h = atlas.size
    
    # Downsample for speed on large images
    scale = max(1, max(w, h) // 1024)
    if scale > 1:
        small = atlas.resize((w // scale, h // scale), Image.Resampling.BILINEAR)
    else:
        small = atlas
    sw, sh = small.size
    
    # Compute alpha projections
    alpha = small.getchannel('A')
    data = alpha.getdata()
    v_proj = [sum(data[y * sw + x] for y in range(sh)) for x in range(sw)]
    h_proj = [sum(data[y * sw + x] for x in range(sw)) for y in range(sh)]
    
    # Find columns (vertical lines) and rows (horizontal lines) with minimal content
    v_nonzero = [v for v in v_proj if v > 0]
    h_nonzero = [h for h in h_proj if h > 0]
    v_threshold = (sum(v_nonzero) / len(v_nonzero) * 0.2) if v_nonzero else 1
    h_threshold = (sum(h_nonzero) / len(h_nonzero) * 0.2) if h_nonzero else 1
    
    # Find separator columns/rows (low-content vertical/horizontal lines)
    sep_cols = [x for x in range(sw) if v_proj[x] < v_threshold]
    sep_rows = [y for y in range(sh) if h_proj[y] < h_threshold]
    
    # Group consecutive separators into regions
    def group_separators(seps, max_gap=2):
        if not seps:
            return []
        groups = [[seps[0]]]
        for sep in seps[1:]:
            if sep - groups[-1][-1] <= max_gap:
                groups[-1].append(sep)
            else:
                groups.append([sep])
        return groups
    
    col_sep_groups = group_separators(sep_cols)
    row_sep_groups = group_separators(sep_rows)
    
    # Number of cells = number of separators (approximate)
    num_col_seps = len(col_sep_groups)
    num_row_seps = len(row_sep_groups)
    
    # Estimate cell size from available space
    if num_col_seps > 0:
        cell_w = sw // max(1, num_col_seps)
    else:
        cell_w = sw // 16  # fallback assumption
    
    if num_row_seps > 0:
        cell_h = sh // max(1, num_row_seps)
    else:
        cell_h = sh // 16  # fallback assumption
    
    # Find actual repeating cell sizes by measuring distances between separators
    col_widths = []
    if len(col_sep_groups) > 1:
        for i in range(len(col_sep_groups) - 1):
            width = col_sep_groups[i + 1][0] - col_sep_groups[i][-1]
            if width > 0:
                col_widths.append(width)
    
    row_heights = []
    if len(row_sep_groups) > 1:
        for i in range(len(row_sep_groups) - 1):
            height = row_sep_groups[i + 1][0] - row_sep_groups[i][-1]
            if height > 0:
                row_heights.append(height)
    
    # Use most common cell size
    from collections import Counter
    
    if col_widths:
        col_counter = Counter(col_widths)
        cell_w = col_counter.most_common(1)[0][0]
    if row_heights:
        row_counter = Counter(row_heights)
        cell_h = row_counter.most_common(1)[0][0]
    
    # Number of cells: count separators + 1 (cells between separators)
    cols = max(1, num_col_seps + 1)
    rows = max(1, num_row_seps + 1)
    
    # Handle edge case: too many separators detected
    if cols > w // 2 or rows > h // 2:
        # Fallback: use divisor method
        for div in [16, 8, 4, 2]:
            if w % div == 0:
                cols = div
                cell_w = w // div
                break
        for div in [16, 8, 4, 2]:
            if h % div == 0:
                rows = div
                cell_h = h // div
                break
    
    # Scale back if downsampled
    if scale > 1:
        cell_w *= scale
        cell_h *= scale
    
    # Final validation
    cols = max(1, min(cols, max(2, w // 4)))
    rows = max(1, min(rows, max(2, h // 4)))
    
    return cell_w, cell_h, cols, rows


def export_metadata_json(mapping: Dict[int, Dict], out_path: str):
    """Write mapping metadata (unicode -> cell info) as JSON."""
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
