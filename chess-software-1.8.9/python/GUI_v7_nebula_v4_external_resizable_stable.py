import sys
import os
import tkinter as tk

# Ensure current python directory is on module search path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui_overlay import ChessOverlay, show_nebula_welcome

def main():
    root = tk.Tk()
    show_nebula_welcome(root, lambda: ChessOverlay(root))
    root.mainloop()

if __name__ == "__main__":
    main()