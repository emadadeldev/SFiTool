"""Small launcher for the My tool Font Image Editor."""
import importlib.util
import os
import sys


def _load_main_from_local_editor(script_dir: str):
    """Load editor.py explicitly by file path as a robust fallback."""
    editor_path = os.path.join(script_dir, "editor.py")
    if not os.path.isfile(editor_path):
        raise ModuleNotFoundError(f"editor.py not found next to launcher: {editor_path}")

    spec = importlib.util.spec_from_file_location("my_tool_editor", editor_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create import spec for: {editor_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    main = getattr(module, "main", None)
    if main is None:
        raise AttributeError(f"'main' function not found in: {editor_path}")
    return main


# Ensure we're in the right directory and can import editor
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

try:
    from editor import main
except Exception:
    main = _load_main_from_local_editor(script_dir)

if __name__ == "__main__":
    sys.exit(main(sys.argv))
