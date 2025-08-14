import os
import platform
import sqlite3
import subprocess
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, Text
import json
import sys
import ctypes
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from cryptography.fernet import Fernet

DB_FILE = "../.sys_billing"
CONFIG_FILE = "../items_config.json"
GST_RATE = 0.05

ITEMS = []  # Populated from JSON
DISPLAY_NAME = {}  # Populated from JSON
ITEM_RATES = {}  # Populated from JSON
shortcut_map = {}  # Populated from JSON

class BillingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("BBK Software Solutions")
        self.root.geometry("1020x650")
        self.items = []
        self.current_estimate_no = None
        self.payment_mode = tk.StringVar(value="Cash")
        self.shortcut_map = {}
        self.item_buttons = []
        self.list_new_item_button = None
        self.remove_item_button = None

        os.makedirs("../reports", exist_ok=True)

        self.conn = sqlite3.connect(DB_FILE)
        self.c = self.conn.cursor()
        self.setup_database()
        self.load_items_and_shortcuts()
        self.build_ui()
        self.bind_shortcuts()
        self.root.bind("<Control-p>", lambda e: self.generate_estimate_action())
        self.update_today_total()

    def setup_database(self):
        # Set hidden attribute on Windows
        if platform.system() == "Windows" and os.path.exists(DB_FILE):
            try:
                ctypes.windll.kernel32.SetFileAttributesW(DB_FILE, 2)  # 2 = FILE_ATTRIBUTE_HIDDEN
            except Exception as e:
                messagebox.showwarning("Warning", f"Could not hide database file: {e}")

        self.c.execute("""CREATE TABLE IF NOT EXISTS estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            estimate_no TEXT,
            date TEXT,
            description TEXT,
            qty REAL,
            unit_price REAL,
            total REAL,
            payment_mode TEXT,
            status TEXT DEFAULT 'Active'
        )""")
        self.c.execute("""CREATE TABLE IF NOT EXISTS estimate_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            estimate_no TEXT,
            date TEXT
        )""")
        self.conn.commit()

        # Ensure database file is hidden after creation
        if platform.system() == "Windows":
            try:
                ctypes.windll.kernel32.SetFileAttributesW(DB_FILE, 2)  # 2 = FILE_ATTRIBUTE_HIDDEN
            except Exception as e:
                messagebox.showwarning("Warning", f"Could not hide database file: {e}")

    def load_items_and_shortcuts(self):
        ITEMS.clear()
        DISPLAY_NAME.clear()
        ITEM_RATES.clear()
        self.shortcut_map.clear()
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                ITEMS.extend(config.get("items", []))
                DISPLAY_NAME.update(config.get("display_names", {}))
                ITEM_RATES.update(config.get("rates", {}))
                self.shortcut_map.update(config.get("shortcuts", {}))
        except FileNotFoundError:
            default_config = {
                "items": ["Soya Oil", "Palm Oil"],
                "display_names": {"Soya Oil": "Soya Oil", "Palm Oil": "Palm Oil"},
                "rates": {"Soya Oil": 0.0, "Palm Oil": 0.0},
                "shortcuts": {"1": "Soya Oil", "2": "Palm Oil"}
            }
            ITEMS.extend(default_config["items"])
            DISPLAY_NAME.update(default_config["display_names"])
            ITEM_RATES.update(default_config["rates"])
            self.shortcut_map.update(default_config["shortcuts"])
            self.save_items_and_shortcuts()

    def save_items_and_shortcuts(self):
        config = {
            "items": ITEMS,
            "display_names": DISPLAY_NAME,
            "rates": ITEM_RATES,
            "shortcuts": self.shortcut_map
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot save items config: {e}\nEnsure '{CONFIG_FILE}' is writable.")

    def build_ui(self):
        top = tk.Frame(self.root)
        top.pack(fill=tk.X, pady=8)
        self.estimate_label = tk.Label(top, text="Estimate No: ", font=("Arial", 14, "bold"))
        self.estimate_label.pack(side=tk.LEFT, padx=12)
        self.total_label = tk.Label(top, text="Total: 0.00", font=("Arial", 14, "bold"))
        self.total_label.pack(side=tk.LEFT, padx=12)

        mode_frame = tk.LabelFrame(self.root, text="Payment Mode")
        mode_frame.pack(fill=tk.X, padx=10, pady=6)
        tk.Radiobutton(mode_frame, text="Cash", variable=self.payment_mode, value="Cash").pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(mode_frame, text="Credit", variable=self.payment_mode, value="Credit").pack(side=tk.LEFT, padx=10)

        self.items_frame = tk.LabelFrame(self.root, text="Items")
        self.items_frame.pack(fill=tk.X, padx=10, pady=6)
        self.canvas = tk.Canvas(self.items_frame, height=80)
        self.scrollbar = tk.Scrollbar(self.items_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.inner_frame = tk.Frame(self.canvas)
        self.canvas_frame = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        self.inner_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        for shortcut, item in sorted(self.shortcut_map.items(), key=lambda x: x[0]):
            btn = tk.Button(self.inner_frame, text=f"{shortcut} - {item}", width=18,
                            command=lambda it=item: self.open_qty_popup(it))
            btn.pack(side=tk.LEFT, padx=6, pady=6)
            self.item_buttons.append(btn)
        self.list_new_item_button = tk.Button(self.inner_frame, text="List New Item", width=18, command=self.list_new_item)
        self.list_new_item_button.pack(side=tk.LEFT, padx=6, pady=6)
        self.remove_item_button = tk.Button(self.inner_frame, text="Remove Items", width=18, command=self.remove_item)
        self.remove_item_button.pack(side=tk.LEFT, padx=6, pady=6)

        cols = ("#", "Description", "Qty", "Unit Price", "Total")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", height=12)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=140 if c != "#" else 60, anchor=tk.CENTER if c in ("#", "Qty") else tk.W)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        actions = tk.Frame(self.root)
        actions.pack(fill=tk.X, padx=10, pady=6)

        tk.Button(actions, text="Remove Selected", command=self.remove_selected_item, width=18).grid(row=0, column=0, padx=5, pady=4)
        tk.Button(actions, text="Set Item Rates", command=self.set_item_rates, width=18).grid(row=0, column=1, padx=5, pady=4)
        tk.Button(actions, text="Preview Estimate", command=self.preview_estimate, width=20).grid(row=0, column=2, padx=5, pady=4)
        tk.Button(actions, text="Save + Print (Ctrl+P)", command=self.generate_estimate_action, width=20).grid(row=0, column=3, padx=5, pady=4)

        tk.Button(actions, text="Cancel Estimate", command=self.cancel_estimate_popup, width=16).grid(row=1, column=0, padx=5, pady=4)
        tk.Button(actions, text="Reports", command=self.open_reports_menu, width=16).grid(row=1, column=1, padx=5, pady=4)
        tk.Button(actions, text="View Estimates", command=self.view_estimates, width=16).grid(row=1, column=2, padx=5, pady=4)
        tk.Button(actions, text="Erase All Data", fg="white", bg="#d9534f", command=self.erase_all_data, width=16).grid(row=1, column=3, padx=5, pady=4)

        self.today_total_label = tk.Label(self.root, text="Today's Sales Total: 0.00", font=("Arial", 12, "bold"))
        self.today_total_label.pack(pady=5)

    def bind_shortcuts(self):
        for shortcut, item in self.shortcut_map.items():
            self.root.bind(shortcut, lambda e, it=item: self.open_qty_popup(it))

    def unbind_shortcuts(self):
        for shortcut in self.shortcut_map:
            self.root.unbind(shortcut)

    def list_new_item(self):
        self.unbind_shortcuts()
        p = tk.Toplevel(self.root)
        p.title("Add New Item")
        p.resizable(False, False)
        p.grab_set()

        tk.Label(p, text="Item Name").grid(row=0, column=0, padx=10, pady=6, sticky="e")
        item_name_var = tk.StringVar()
        tk.Entry(p, textvariable=item_name_var, width=20).grid(row=0, column=1, padx=10, pady=6)

        tk.Label(p, text="Shortcut Number").grid(row=1, column=0, padx=10, pady=6, sticky="e")
        shortcut_var = tk.StringVar()
        tk.Entry(p, textvariable=shortcut_var, width=20).grid(row=1, column=1, padx=10, pady=6)

        def add_item():
            item_name = item_name_var.get().strip()
            shortcut = shortcut_var.get().strip()
            if not item_name:
                messagebox.showerror("Error", "Item name cannot be empty")
                return
            if not shortcut.isdigit():
                messagebox.showerror("Error", "Shortcut must be a number")
                return
            if shortcut in self.shortcut_map:
                messagebox.showerror("Error", f"Shortcut '{shortcut}' is already in use")
                return
            if item_name in ITEMS:
                messagebox.showerror("Error", f"Item '{item_name}' already exists")
                return

            ITEMS.append(item_name)
            DISPLAY_NAME[item_name] = item_name
            ITEM_RATES[item_name] = 0.0
            self.shortcut_map[shortcut] = item_name
            self.save_items_and_shortcuts()

            btn = tk.Button(self.inner_frame, text=f"{shortcut} - {item_name}", width=18,
                            command=lambda it=item_name: self.open_qty_popup(it))
            btn.pack(side=tk.LEFT, padx=6, pady=6)
            self.item_buttons.append(btn)

            self.list_new_item_button.pack_forget()
            self.remove_item_button.pack_forget()
            self.list_new_item_button.pack(side=tk.LEFT, padx=6, pady=6)
            self.remove_item_button.pack(side=tk.LEFT, padx=6, pady=6)

            self.bind_shortcuts()
            messagebox.showinfo("Success", f"Item '{item_name}' added with shortcut '{shortcut}'")
            p.destroy()

        tk.Button(p, text="Add Item", command=add_item, width=14).grid(row=2, column=0, columnspan=2, pady=(6, 10))
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def remove_item(self):
        self.unbind_shortcuts()
        p = tk.Toplevel(self.root)
        p.title("Remove Item")
        p.resizable(False, False)
        p.grab_set()

        tk.Label(p, text="Select Item to Remove").grid(row=0, column=0, padx=10, pady=6, sticky="e")
        item_var = tk.StringVar()
        items_menu = ttk.Combobox(p, textvariable=item_var, values=ITEMS, state="readonly", width=20)
        items_menu.grid(row=0, column=1, padx=10, pady=6)
        items_menu.focus()

        def remove():
            item_name = item_var.get()
            if not item_name:
                messagebox.showerror("Error", "Please select an item to remove")
                return
            if not messagebox.askyesno("Confirm", f"Remove item '{item_name}' and its shortcut?"):
                return

            ITEMS.remove(item_name)
            DISPLAY_NAME.pop(item_name, None)
            ITEM_RATES.pop(item_name, None)
            shortcut = next((k for k, v in self.shortcut_map.items() if v == item_name), None)
            if shortcut:
                self.shortcut_map.pop(shortcut, None)

            for btn in self.item_buttons[:]:
                if btn["text"].endswith(item_name):
                    btn.destroy()
                    self.item_buttons.remove(btn)
                    break

            self.save_items_and_shortcuts()
            self.list_new_item_button.pack_forget()
            self.remove_item_button.pack_forget()
            self.list_new_item_button.pack(side=tk.LEFT, padx=6, pady=6)
            self.remove_item_button.pack(side=tk.LEFT, padx=6, pady=6)

            self.bind_shortcuts()
            messagebox.showinfo("Success", f"Item '{item_name}' removed")
            p.destroy()

        tk.Button(p, text="Remove Item", command=remove, width=14).grid(row=1, column=0, columnspan=2, pady=(6, 10))
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def today_str(self):
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def next_estimate_no(self):
        self.c.execute("SELECT MAX(id) FROM estimate_master")
        mid = self.c.fetchone()[0]
        seq = 1 if mid is None else mid + 1
        return f"abc/{datetime.datetime.now().year}/{seq:04d}"

    def start_new_estimate(self):
        if not self.current_estimate_no:
            self.current_estimate_no = self.next_estimate_no()
            self.estimate_label.config(text=f"Estimate No: {self.current_estimate_no}")

    def open_qty_popup(self, item):
        self.start_new_estimate()
        self.unbind_shortcuts()
        p = tk.Toplevel(self.root)
        p.title(f"Add {item}")
        p.resizable(False, False)
        p.grab_set()

        tk.Label(p, text=f"{item}").grid(row=0, column=0, columnspan=2, padx=10, pady=(10, 6))

        tk.Label(p, text="Quantity").grid(row=1, column=0, padx=10, pady=4, sticky="e")
        qv = tk.StringVar()
        qe = tk.Entry(p, textvariable=qv, width=16)
        qe.grid(row=1, column=1, padx=10, pady=4)
        qe.focus()

        tk.Label(p, text="Rate").grid(row=2, column=0, padx=10, pady=4, sticky="e")
        rv = tk.StringVar()
        rv.set(f"{ITEM_RATES.get(item, 0.0):.2f}")
        re = tk.Entry(p, textvariable=rv, width=16)
        re.grid(row=2, column=1, padx=10, pady=4)

        def add():
            try:
                qty = float(qv.get())
                rate = float(rv.get())
                self.add_item(item, qty, rate)
                p.destroy()
                self.bind_shortcuts()
            except Exception:
                messagebox.showerror("Error", "Invalid entry")

        tk.Button(p, text="Add Item (Enter)", command=add, width=18).grid(row=3, column=0, columnspan=2, padx=10, pady=(8, 10))
        p.bind("<Return>", lambda e: add())
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def add_item(self, desc, qty, rate):
        self.items.append({"desc": desc, "qty": qty, "rate": rate, "total": round(qty * rate, 2)})
        self.refresh_table()

    def refresh_table(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        total = 0.0
        for i, it in enumerate(self.items, start=1):
            self.tree.insert("", "end", values=(i, it["desc"], it["qty"], f"{it['rate']:.2f}", f"{it['total']:.2f}"))
            total += it["total"]
        total_incl = round(total * (1 + GST_RATE), 2)
        self.total_label.config(text=f"Total: {total_incl:.2f}")

    def remove_selected_item(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        if 0 <= idx < len(self.items):
            del self.items[idx]
            self.refresh_table()

    def set_item_rates(self):
        p = tk.Toplevel(self.root)
        p.title("Set Item Rates")
        p.grab_set()
        rate_vars = {}
        for i, item in enumerate(ITEMS):
            tk.Label(p, text=f"{item} Rate").grid(row=i, column=0, padx=10, pady=6, sticky="e")
            rate_var = tk.StringVar()
            rate_var.set(f"{ITEM_RATES.get(item, 0.0):.2f}")
            tk.Entry(p, textvariable=rate_var, width=16, name=f"entry_{item}").grid(row=i, column=1, padx=10, pady=6)
            rate_vars[item] = rate_var

        def save():
            try:
                for item in ITEMS:
                    rate = float(rate_vars[item].get())
                    ITEM_RATES[item] = rate
                self.save_items_and_shortcuts()
                messagebox.showinfo("Saved", "Rates updated.")
                p.destroy()
            except Exception:
                messagebox.showerror("Error", "Invalid rates")

        tk.Button(p, text="Save", command=save, width=14).grid(row=len(ITEMS), column=0, columnspan=2, pady=(6, 10))

    def print_text_content(self, content, title="Print"):
        try:
            if title.startswith("Detailed Sales Report"):
                page_width = 210 * mm  # A4
                max_chars = 67
            else:
                page_width = 80 * mm  # 3-inch thermal
                max_chars = 42
            page_height = 297 * mm
            buffer = BytesIO()
            c = canvas.Canvas(buffer, pagesize=(page_width, page_height))
            c.setFont("Courier", 10)

            y = page_height - 20
            lines = content.split('\n')
            for line in lines:
                if len(line) > max_chars:
                    line = line[:max_chars]
                c.drawString(10, y, line)
                y -= 12
                if y < 20:
                    c.showPage()
                    c.setFont("Courier", 10)
                    y = page_height - 20
            c.save()
            pdf_data = buffer.getvalue()
            buffer.close()

            if platform.system() == "Windows":
                try:
                    process = subprocess.Popen(["print"], stdin=subprocess.PIPE, shell=True)
                    process.communicate(input=pdf_data)
                except Exception as e:
                    messagebox.showwarning("Print Error", f"Failed to print PDF: {e}\nEnsure a printer is installed and set as default.")
                    return
            else:
                try:
                    process = subprocess.Popen(["lp"], stdin=subprocess.PIPE)
                    process.communicate(input=pdf_data)
                except Exception:
                    try:
                        process = subprocess.Popen(["lpr"], stdin=subprocess.PIPE)
                        process.communicate(input=pdf_data)
                    except Exception as e:
                        messagebox.showwarning("Print Error", f"Failed to print PDF using lp/lpr: {e}\nEnsure a printer is configured with lp or lpr.")
                        return
        except ImportError as e:
            messagebox.showerror("Print Error", f"Cannot generate PDF: reportlab is not installed.\nPlease install it using 'pip install reportlab' in the Python environment: {sys.executable}")
            return
        except Exception as e:
            messagebox.showwarning("Print Error", f"Print failed: {e}")
            return

    def open_reports_menu(self):
        p = tk.Toplevel(self.root)
        p.title("Reports Menu")
        p.resizable(False, False)
        p.grab_set()
        tk.Button(p, text="Daily Sales Report", command=self.show_daily_sales_report, width=30).pack(pady=6)
        tk.Button(p, text="Detailed Sales Report", command=self.show_detailed_sales_report, width=30).pack(pady=6)
        tk.Button(p, text="Cancelled Estimates Report", command=self.show_cancelled_estimates_report, width=30).pack(pady=6)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def preview_estimate(self):
        if not self.items:
            return messagebox.showerror("Error", "No items in estimate")
        p = tk.Toplevel(self.root)
        p.title("Estimate Preview")
        p.geometry("600x400")
        p.grab_set()
        self.unbind_shortcuts()

        text = Text(p, wrap=tk.WORD, font=("Courier", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(p, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scrollbar.set)

        content = []
        content.append("BBK Software Solutions")
        content.append("-" * 42)
        display_est = self.current_estimate_no or "N/A"
        content.append(f"Estimate: {display_est:<20}")
        content.append(f"Date: {datetime.datetime.now().strftime('%d-%m-%Y'):<20}")
        content.append("")
        content.append(f"{'Item':<20} {'Qty':>8} {'Rate':>8} {'Total':>8}")
        content.append("-" * 42)

        total = 0.0
        for it in self.items:
            name = DISPLAY_NAME.get(it["desc"], it["desc"])[:20]
            content.append(f"{name:<20} {it['qty']:>8.2f} {it['rate']:>8.2f} {it['total']:>8.2f}")
            total += it["total"]

        total_incl = round(total * (1 + GST_RATE), 2)
        content.append("-" * 42)
        content.append(f"{'Subtotal':<36} {total:>8.2f}")
        content.append(f"{'GST (5%)':<36} {round(total * GST_RATE, 2):>8.2f}")
        content.append(f"{'TOTAL':<36} {total_incl:>8.2f}")
        content.append(f"{'Mode':<36} {self.payment_mode.get():>8}")

        text_content = "\n".join(content)
        text.insert(tk.END, text_content)
        text.config(state=tk.DISABLED)

        button_frame = tk.Frame(p)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(button_frame, text="Print", command=lambda: self.print_text_content(text_content, "Estimate Preview")).pack(side=tk.LEFT, padx=8)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def generate_estimate_action(self):
        if not self.items:
            return messagebox.showerror("Error", "No items in estimate")
        if not self.current_estimate_no:
            self.start_new_estimate()
        date_str = self.today_str()
        self.c.execute("INSERT INTO estimate_master(estimate_no,date) VALUES(?,?)", (self.current_estimate_no, date_str))
        for it in self.items:
            self.c.execute("""INSERT INTO estimates
                              (estimate_no,date,description,qty,unit_price,total,payment_mode,status)
                              VALUES (?,?,?,?,?,?,?, 'Active')""",
                           (self.current_estimate_no, date_str, it["desc"], it["qty"], it["rate"], it["total"], self.payment_mode.get()))
        self.conn.commit()

        content = []
        content.append("BBK Software Solutions")
        content.append("-" * 42)
        content.append(f"Estimate: {self.current_estimate_no:<20}")
        content.append(f"Date: {datetime.datetime.now().strftime('%d-%m-%Y'):<20}")
        content.append("")
        content.append(f"{'Item':<20} {'Qty':>8} {'Rate':>8} {'Total':>8}")
        content.append("-" * 42)

        total = 0.0
        for it in self.items:
            name = DISPLAY_NAME.get(it["desc"], it["desc"])[:20]
            content.append(f"{name:<20} {it['qty']:>8.2f} {it['rate']:>8.2f} {it['total']:>8.2f}")
            total += it["total"]

        total_incl = round(total * (1 + GST_RATE), 2)
        content.append("-" * 42)
        content.append(f"{'Subtotal':<36} {total:>8.2f}")
        content.append(f"{'GST (5%)':<36} {round(total * GST_RATE, 2):>8.2f}")
        content.append(f"{'TOTAL':<36} {total_incl:>8.2f}")
        content.append(f"{'Mode':<36} {self.payment_mode.get():>8}")

        text_content = "\n".join(content)
        self.print_text_content(text_content, f"Estimate {self.current_estimate_no}")

        self.items.clear()
        self.current_estimate_no = None
        self.refresh_table()
        self.estimate_label.config(text="Estimate No: ")
        self.update_today_total()

    def show_daily_sales_report(self):
        t = self.today_str()
        self.c.execute("""SELECT payment_mode, description, SUM(qty) AS q, SUM(total) AS s
                          FROM estimates
                          WHERE date=? AND status='Active'
                          GROUP BY payment_mode, description
                          ORDER BY payment_mode, description""", (t,))
        rows = self.c.fetchall()

        p = tk.Toplevel(self.root)
        p.title("Daily Sales Report")
        p.geometry("600x400")
        p.grab_set()
        self.unbind_shortcuts()

        text = Text(p, wrap=tk.WORD, font=("Courier", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(p, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scrollbar.set)

        content = []
        content.append("Daily Sales Report")
        content.append(f"Date: {t}")
        content.append("-" * 42)
        grand_total = 0.0
        for mode in ("Cash", "Credit"):
            content.append(f"{mode} Sales:")
            content.append(f"{'Item':<20} {'Qty':>8} {'Amount':>8}")
            content.append("-" * 42)
            mode_rows = [r for r in rows if r[0] == mode]
            mode_base = 0.0
            for _, desc, q, s in mode_rows:
                name = DISPLAY_NAME.get(desc, desc)[:20]
                content.append(f"{name:<20} {q:>8.2f} {s:>8.2f}")
                mode_base += float(s or 0)
            mode_incl = round(mode_base * (1 + GST_RATE), 2)
            grand_total += mode_incl
            content.append("-" * 42)
            content.append(f"{'Subtotal':<36} {mode_base:>8.2f}")
            content.append(f"{'GST (5%)':<36} {round(mode_base * GST_RATE, 2):>8.2f}")
            content.append(f"{'Total':<36} {mode_incl:>8.2f}")
            content.append("")

        content.append("-" * 42)
        content.append(f"{'Grand Total':<36} {grand_total:>8.2f}")

        text_content = "\n".join(content)
        text.insert(tk.END, text_content)
        text.config(state=tk.DISABLED)

        button_frame = tk.Frame(p)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(button_frame, text="Print", command=lambda: self.print_text_content(text_content, "Daily Sales Report")).pack(side=tk.LEFT, padx=8)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def show_detailed_sales_report(self):
        t = self.today_str()
        p = tk.Toplevel(self.root)
        p.title("Detailed Sales Report")
        p.geometry("800x600")
        p.grab_set()
        self.unbind_shortcuts()

        text = Text(p, wrap=tk.WORD, font=("Courier", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(p, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scrollbar.set)

        content = []
        content.append("Detailed Sales Report")
        content.append(f"Date: {t}")
        content.append("")
        content.append(f"{'Estimate No':<20} {'Item':<20} {'Qty':>8} {'Rate':>8} {'Total':>8}")
        content.append("-" * 67)

        grand_total = 0.0
        for mode in ("Cash", "Credit"):
            content.append(f"{mode} Estimates")
            content.append(f"{'Estimate No':<20} {'Item':<20} {'Qty':>8} {'Rate':>8} {'Total':>8}")
            content.append("-" * 67)

            self.c.execute("""SELECT estimate_no, description, qty, unit_price, total
                              FROM estimates
                              WHERE date=? AND payment_mode=? AND status='Active'
                              ORDER BY estimate_no""", (t, mode))
            rows = self.c.fetchall()
            mode_base = 0.0
            for est, desc, qty, rate, total in rows:
                name = DISPLAY_NAME.get(desc, desc)[:20]
                content.append(f"{est:<20} {name:<20} {qty:>8.2f} {rate:>8.2f} {total:>8.2f}")
                mode_base += float(total or 0)
            mode_incl = round(mode_base * (1 + GST_RATE), 2)
            grand_total += mode_incl
            content.append("")
            content.append(f"{'Subtotal':<56} {mode_base:>8.2f}")
            content.append(f"{'GST (5%)':<56} {round(mode_base * GST_RATE, 2):>8.2f}")
            content.append(f"{'Total (incl GST)':<56} {mode_incl:>8.2f}")
            content.append("")

        content.append("-" * 67)
        content.append(f"{'Grand Total (incl GST)':<56} {grand_total:>8.2f}")

        text_content = "\n".join(content)
        text.insert(tk.END, text_content)
        text.config(state=tk.DISABLED)

        button_frame = tk.Frame(p)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(button_frame, text="Print", command=lambda: self.print_text_content(text_content, "Detailed Sales Report")).pack(side=tk.LEFT, padx=8)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def show_cancelled_estimates_report(self):
        t = self.today_str()
        p = tk.Toplevel(self.root)
        p.title("Cancelled Estimates Report")
        p.geometry("600x400")
        p.grab_set()
        self.unbind_shortcuts()

        text = Text(p, wrap=tk.WORD, font=("Courier", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(p, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scrollbar.set)

        content = []
        content.append("Cancelled Estimates Report")
        content.append(f"Date: {t}")
        content.append("-" * 42)
        content.append(f"{'Estimate No':<20} {'Amount':>8}")
        content.append("-" * 42)

        self.c.execute("""SELECT estimate_no, SUM(total) as s
                          FROM estimates
                          WHERE date=? AND status='Cancelled'
                          GROUP BY estimate_no
                          ORDER BY estimate_no""", (t,))
        tot = 0.0
        for est, s in self.c.fetchall():
            content.append(f"{est:<20} {(s or 0):>8.2f}")
            tot += float(s or 0)
        content.append("-" * 42)
        content.append(f"{'Total Cancelled':<20} {tot:>8.2f}")

        text_content = "\n".join(content)
        text.insert(tk.END, text_content)
        text.config(state=tk.DISABLED)

        button_frame = tk.Frame(p)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(button_frame, text="Print", command=lambda: self.print_text_content(text_content, "Cancelled Estimates Report")).pack(side=tk.LEFT, padx=8)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def view_estimates(self):
        p = tk.Toplevel(self.root)
        p.title("View Estimates")
        p.geometry("600x400")
        p.grab_set()
        self.unbind_shortcuts()

        cols = ("Estimate No", "Date", "Total")
        tree = ttk.Treeview(p, columns=cols, show="headings", height=12)
        for ccol in cols:
            tree.heading(ccol, text=ccol)
            tree.column(ccol, width=160 if ccol != "Total" else 120, anchor=tk.CENTER)
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.c.execute("""SELECT estimate_no, date, SUM(total) AS s
                          FROM estimates
                          GROUP BY estimate_no, date
                          ORDER BY date, estimate_no""")
        for row in self.c.fetchall():
            tree.insert("", "end", values=row)

        def show_selected(event=None):
            sel = tree.selection()
            if not sel:
                return
            est = tree.item(sel[0], "values")[0]
            self.show_estimate_details(est)

        tree.bind("<Double-1>", show_selected)
        tk.Button(p, text="View Selected", command=show_selected, width=16).pack(side=tk.RIGHT, padx=8, pady=6)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def show_estimate_details(self, estimate_no):
        self.c.execute("""SELECT date, description, qty, unit_price, total, payment_mode
                          FROM estimates
                          WHERE estimate_no=? AND status='Active'""", (estimate_no,))
        rows = self.c.fetchall()
        if not rows:
            messagebox.showerror("Error", f"No details found for estimate {estimate_no}")
            return

        p = tk.Toplevel(self.root)
        p.title(f"Estimate {estimate_no}")
        p.geometry("600x400")
        p.grab_set()
        self.unbind_shortcuts()

        text = Text(p, wrap=tk.WORD, font=("Courier", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        scrollbar = tk.Scrollbar(p, orient=tk.VERTICAL, command=text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=scrollbar.set)

        content = []
        content.append("BBK Software Solutions")
        content.append("-" * 42)
        content.append(f"Estimate: {estimate_no:<20}")
        content.append(f"Date: {rows[0][0]:<20}")
        content.append("")
        content.append(f"{'Item':<20} {'Qty':>8} {'Rate':>8} {'Total':>8}")
        content.append("-" * 42)

        total = 0.0
        for _, desc, qty, rate, total_line, payment_mode in rows:
            name = DISPLAY_NAME.get(desc, desc)[:20]
            content.append(f"{name:<20} {qty:>8.2f} {rate:>8.2f} {total_line:>8.2f}")
            total += float(total_line or 0)

        total_incl = round(total * (1 + GST_RATE), 2)
        content.append("-" * 42)
        content.append(f"{'Subtotal':<36} {total:>8.2f}")
        content.append(f"{'GST (5%)':<36} {round(total * GST_RATE, 2):>8.2f}")
        content.append(f"{'TOTAL':<36} {total_incl:>8.2f}")
        content.append(f"{'Mode':<36} {payment_mode:>8}")

        text_content = "\n".join(content)
        text.insert(tk.END, text_content)
        text.config(state=tk.DISABLED)

        button_frame = tk.Frame(p)
        button_frame.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(button_frame, text="Print", command=lambda: self.print_text_content(text_content, f"Estimate {estimate_no}")).pack(side=tk.LEFT, padx=8)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def cancel_estimate_popup(self):
        p = tk.Toplevel(self.root)
        p.title("Cancel Estimate")
        p.geometry("600x400")
        p.grab_set()
        self.unbind_shortcuts()

        cols = ("Estimate No", "Date", "Total")
        tree = ttk.Treeview(p, columns=cols, show="headings", height=12)
        for ccol in cols:
            tree.heading(ccol, text=ccol)
            tree.column(ccol, width=160 if ccol != "Total" else 120, anchor=tk.CENTER)
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.c.execute("""SELECT estimate_no, date, SUM(total) AS s
                          FROM estimates
                          WHERE status='Active'
                          GROUP BY estimate_no, date
                          ORDER BY date, estimate_no""")
        for row in self.c.fetchall():
            tree.insert("", "end", values=row)

        def cancel_selected():
            sel = tree.selection()
            if not sel:
                return
            est = tree.item(sel[0], "values")[0]
            if not messagebox.askyesno("Confirm", f"Cancel entire estimate {est}?"):
                return
            self.c.execute("UPDATE estimates SET status='Cancelled' WHERE estimate_no=?", (est,))
            self.conn.commit()
            messagebox.showinfo("Cancelled", f"Estimate {est} cancelled")
            p.destroy()
            self.bind_shortcuts()
            self.update_today_total()

        tk.Button(p, text="Cancel Selected", command=cancel_selected, width=16).pack(side=tk.RIGHT, padx=8, pady=6)
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def erase_all_data(self):
        if not messagebox.askyesno("Confirm", "Erase ALL estimate data and reset numbering?"):
            return
        if not messagebox.askyesno("Confirm Again", "This will securely delete all estimate data but preserve items and shortcuts. Continue?"):
            return
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            if os.path.exists(DB_FILE):
                # Secure deletion with AES-256 encryption
                key = Fernet.generate_key()
                f = Fernet(key)
                with open(DB_FILE, "rb") as file:
                    data = file.read()
                encrypted_data = f.encrypt(data)
                with open(DB_FILE, "wb") as file:
                    file.write(encrypted_data)
                # Key is discarded as it goes out of scope

                # Overwrite file multiple times
                file_size = os.path.getsize(DB_FILE)
                for _ in range(3):
                    with open(DB_FILE, "wb") as file:
                        file.write(os.urandom(file_size))
                with open(DB_FILE, "wb") as file:
                    file.write(b'\0' * file_size)

                # Remove hidden attribute before deletion (Windows)
                if platform.system() == "Windows":
                    try:
                        ctypes.windll.kernel32.SetFileAttributesW(DB_FILE, 0)  # 0 = FILE_ATTRIBUTE_NORMAL
                    except Exception:
                        pass
                os.remove(DB_FILE)
        except Exception as e:
            messagebox.showerror("Error", f"Could not securely remove DB: {e}")
            return
        self.conn = sqlite3.connect(DB_FILE)
        self.c = self.conn.cursor()
        self.setup_database()
        self.items.clear()
        self.current_estimate_no = None
        self.refresh_table()
        self.estimate_label.config(text="Estimate No: ")
        self.update_today_total()
        messagebox.showinfo("Done", "All estimate data securely erased and numbering reset.")

    def update_today_total(self):
        t = self.today_str()
        self.c.execute("SELECT SUM(total) FROM estimates WHERE date=? AND status='Active'", (t,))
        base = float(self.c.fetchone()[0] or 0.0)
        incl = round(base * (1 + GST_RATE), 2)
        self.today_total_label.config(text=f"Today's Sales Total: {incl:.2f}")

if __name__ == "__main__":
    root = tk.Tk()
    app = BillingApp(root)
    root.mainloop()