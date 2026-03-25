import threading
import time

def background_task():
    print("Background task started")
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    res = simpledialog.askstring("Test dialog", "Enter something:", parent=root)
    root.destroy()
    print("User entered:", res)

t = threading.Thread(target=background_task)
t.start()
t.join()
print("Done")
