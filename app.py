from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> None:
    try:
        from gui import main as run_gui
    except Exception as exc:
        import tkinter.messagebox as messagebox

        messagebox.showerror("启动失败", f"{exc}\n\n请安装 requirements.txt 中的依赖。")
        raise
    run_gui()


if __name__ == "__main__":
    main()
