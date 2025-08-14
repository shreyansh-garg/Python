import os
import platform
import sqlite3
import subprocess
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
import json

DB_FILE = "../billing_data.db"
CONFIG_FILE = "../items_config.json"
GST_RATE = 0.05
THERMAL_WIDTH_MM = 80

ITEMS = []  # Will be populated from JSON
DISPLAY_NAME = {}  # Will be populated from JSON
ITEM_RATES = {}  # Will store rates in memory, populated from JSON

class BillingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Ambika Solvex Billing")
        self.root.geometry("1020x650")
        self.items = []
        self.current_invoice_no = None
        self.payment_mode = tk.StringVar(value="Cash")
        self.shortcut_map = {}  # Will be populated from JSON
        self.item_buttons = []
        self.list_new_item_button = None

        os.makedirs("../invoices", exist_ok=True)
        os.makedirs("../reports", exist_ok=True)

        self.conn = sqlite3.connect(DB_FILE)
        self.c = self.conn.cursor()
        self.setup_database()
        self.load_items_and_shortcuts()
        self.build_ui()
        self.bind_shortcuts()
        self.root.bind("<Control-p>", lambda e: self.generate_invoice_action())
        self.update_today_total()

    def setup_database(self):
        self.c.execute("""CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT,
            date TEXT,
            description TEXT,
            qty REAL,
            unit_price REAL,
            total REAL,
            payment_mode TEXT,
            status TEXT DEFAULT 'Active'
        )""")
        self.c.execute("""CREATE TABLE IF NOT EXISTS invoice_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT,
            date TEXT
        )""")
        self.conn.commit()

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
            # Initialize default items if JSON file doesn't exist
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
        self.invoice_label = tk.Label(top, text="Invoice No: ", font=("Arial", 14, "bold"))
        self.invoice_label.pack(side=tk.LEFT, padx=12)
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
        tk.Button(actions, text="Preview Invoice (PDF)", command=self.preview_invoice, width=20).grid(row=0, column=2, padx=5, pady=4)
        tk.Button(actions, text="Save + Print (Ctrl+P)", command=self.generate_invoice_action, width=20).grid(row=0, column=3, padx=5, pady=4)

        tk.Button(actions, text="Cancel Invoice", command=self.cancel_invoice_popup, width=16).grid(row=1, column=0, padx=5, pady=4)
        tk.Button(actions, text="Reports", command=self.open_reports_menu, width=22).grid(row=1, column=1, padx=5, pady=4)
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
            self.list_new_item_button.pack(side=tk.LEFT, padx=6, pady=6)

            self.bind_shortcuts()
            messagebox.showinfo("Success", f"Item '{item_name}' added with shortcut '{shortcut}'")
            p.destroy()

        tk.Button(p, text="Add Item", command=add_item, width=14).grid(row=2, column=0, columnspan=2, pady=(6, 10))
        p.protocol("WM_DELETE_WINDOW", lambda: (p.destroy(), self.bind_shortcuts()))

    def today_str(self):
        return datetime.datetime.now().strftime("%Y-%m-%d")

    def next_invoice_no(self):
        self.c.execute("SELECT MAX(id) FROM invoice_master")
        mid = self.c.fetchone()[0]
        seq = 1 if mid is None else mid + 1
        return f"abc/{datetime.datetime.now().year}/{seq:04d}"

    def start_new_invoice(self):
        if not self.current_invoice_no:
            self.current_invoice_no = self.next_invoice_no()
            self.invoice_label.config(text=f"Invoice No: {self.current_invoice_no}")

    def open_qty_popup(self, item):
        self.start_new_invoice()
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

    def open_reports_menu(self):
        p = tk.Toplevel(self.root)
        p.title("Reports Menu")
        p.grab_set()
        tk.Button(p, text="Daily Sales Report (80mm)", command=self.daily_sales_report, width=30).pack(pady=6)
        tk.Button(p, text="Detailed Sales Report (A4)", command=self.detailed_sales_report, width=30).pack(pady=6)
        tk.Button(p, text="Cancelled Invoices Report", command=self.cancelled_invoices_report, width=30).pack(pady=6)

    def preview_invoice(self):
        if not self.items:
            return messagebox.showerror("Error", "No items in invoice")
        safe = (self.current_invoice_no or "NEW").replace("/", "_")
        temp_file = os.path.join("../invoices", f"PREVIEW_{safe}.pdf")
        self.generate_invoice_pdf(temp_file, for_preview=True)
        self.open_file(temp_file)

    def generate_invoice_action(self):
        if not self.items:
            return messagebox.showerror("Error", "No items in invoice")
        if not self.current_invoice_no:
            self.start_new_invoice()
        date_str = self.today_str()
        self.c.execute("INSERT INTO invoice_master(invoice_no,date) VALUES(?,?)", (self.current_invoice_no, date_str))
        for it in self.items:
            self.c.execute("""INSERT INTO invoices
                              (invoice_no,date,description,qty,unit_price,total,payment_mode,status)
                              VALUES (?,?,?,?,?,?,?, 'Active')""",
                           (self.current_invoice_no, date_str, it["desc"], it["qty"], it["rate"], it["total"], self.payment_mode.get()))
        self.conn.commit()
        safe = self.current_invoice_no.replace("/", "_")
        filename = os.path.join("../invoices", f"{safe}.pdf")
        self.generate_invoice_pdf(filename)
        self.print_file(filename)
        self.items.clear()
        self.current_invoice_no = None
        self.refresh_table()
        self.invoice_label.config(text="Invoice No: ")
        self.update_today_total()

    def print_file(self, filepath):
        try:
            if platform.system() == "Windows":
                try:
                    os.startfile(filepath, "print")
                except Exception:
                    subprocess.Popen(['cmd', '/c', 'start', '/min', '', '/WAIT', filepath, '/print'], shell=True)
            else:
                try:
                    subprocess.Popen(["lp", filepath])
                except Exception:
                    subprocess.Popen(["lpr", filepath])
        except Exception as e:
            messagebox.showwarning("Print Error", f"Print failed: {e}\nFile saved at: {filepath}")

    def open_file(self, filepath):
        try:
            if platform.system() == "Windows":
                os.startfile(filepath)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", filepath])
            else:
                subprocess.Popen(["xdg-open", filepath])
        except Exception:
            messagebox.showinfo("File Saved", f"File saved: {filepath}")

    def generate_invoice_pdf(self, fpath, for_preview=False):
        display_inv = self.current_invoice_no or ""
        width = THERMAL_WIDTH_MM * mm
        line_mm = 6.5
        header_mm = 30
        footer_mm = 18
        rows = max(1, len(self.items))
        h_mm = header_mm + rows * line_mm + footer_mm
        try:
            os.makedirs("../invoices", exist_ok=True)
            if not os.access("../invoices", os.W_OK):
                messagebox.showerror("Error", "Cannot write to 'invoices' directory. Check permissions.")
                return
            c = canvas.Canvas(fpath, pagesize=(width, h_mm * mm))
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot create PDF: {e}\nEnsure the 'invoices' directory is writable.")
            return

        y = (h_mm - 4) * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(6 * mm, y, f"Invoice: {display_inv}")
        y -= 6.5 * mm
        c.setFont("Helvetica", 9)
        c.drawString(6 * mm, y, f"Date: {datetime.datetime.now().strftime('%d-%m-%Y')}")
        y -= 6.5 * mm

        c.setFont("Helvetica-Bold", 9)
        c.drawString(6 * mm, y, "Item")
        c.drawRightString(45 * mm, y, "Qty")
        c.drawRightString(58 * mm, y, "Rate")
        c.drawRightString(73 * mm, y, "Total")
        y -= 5.5 * mm
        c.setFont("Helvetica", 9)

        total = 0.0
        for it in self.items:
            name = DISPLAY_NAME.get(it["desc"], it["desc"])
            c.drawString(6 * mm, y, name[:20])
            c.drawRightString(45 * mm, y, f"{it['qty']:.2f}")
            c.drawRightString(58 * mm, y, f"{it['rate']:.2f}")
            c.drawRightString(73 * mm, y, f"{it['total']:.2f}")
            y -= line_mm * mm
            total += it["total"]

        y -= 3 * mm
        c.setFont("Helvetica-Bold", 10)
        total_incl = round(total * (1 + GST_RATE), 2)
        c.drawRightString(73 * mm, y, f"TOTAL: {total_incl:.2f}")
        y -= 6 * mm
        c.setFont("Helvetica", 8)
        c.drawString(6 * mm, y, f"Mode: {self.payment_mode.get()}")
        try:
            c.save()
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot save PDF: {e}\nEnsure the 'invoices' directory is writable and the file is not open elsewhere.")
            return

    def daily_sales_report(self):
        t = self.today_str()
        self.c.execute("""SELECT payment_mode, description, SUM(qty) AS q, SUM(total) AS s
                          FROM invoices
                          WHERE date=? AND status='Active'
                          GROUP BY payment_mode, description
                          ORDER BY payment_mode, description""", (t,))
        rows = self.c.fetchall()

        display_rows = sum(1 for _ in rows) + 8
        height_mm = 25 + display_rows * 6.5
        width = THERMAL_WIDTH_MM * mm
        filename = os.path.join("../reports", f"DailySales_{t}.pdf")
        try:
            os.makedirs("../reports", exist_ok=True)
            if not os.access("../reports", os.W_OK):
                messagebox.showerror("Error", "Cannot write to 'reports' directory. Check permissions.")
                return
            c = canvas.Canvas(filename, pagesize=(width, height_mm * mm))
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot create PDF: {e}\nEnsure the 'reports' directory is writable.")
            return

        y = (height_mm - 4) * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(6 * mm, y, f"Daily Sales Report - {t}")
        y -= 7 * mm
        c.setFont("Helvetica", 9)
        c.drawString(6 * mm, y, "-" * 40)
        y -= 6 * mm

        grand_total = 0.0
        for mode in ("Cash", "Credit"):
            mode_rows = [r for r in rows if r[0] == mode]
            c.setFont("Helvetica-Bold", 10)
            c.drawString(6 * mm, y, f"{mode} Sales:")
            y -= 6.5 * mm
            mode_base = 0.0
            for _, desc, q, s in mode_rows:
                name = DISPLAY_NAME.get(desc, desc)
                c.setFont("Helvetica", 9)
                c.drawString(6 * mm, y, f"{name}   Qty:{q:g}   Amt:{s:.2f}")
                y -= 6.0 * mm
                mode_base += float(s or 0)
            mode_incl = round(mode_base * (1 + GST_RATE), 2)
            grand_total += mode_incl
            c.setFont("Helvetica-Bold", 10)
            c.drawString(6 * mm, y, f"{mode} Total: {mode_incl:.2f}")
            y -= 7.5 * mm
            c.setFont("Helvetica", 9)
            c.drawString(6 * mm, y, "-" * 40)
            y -= 6 * mm

        c.setFont("Helvetica-Bold", 11)
        c.drawString(6 * mm, y, f"Grand Total: {grand_total:.2f}")
        try:
            c.save()
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot save PDF: {e}\nEnsure the 'reports' directory is writable and the file is not open elsewhere.")
            return
        self.open_file(filename)

    def detailed_sales_report(self):
        t = self.today_str()
        filename = os.path.join("../reports", f"DetailedSales_{t}.pdf")
        try:
            os.makedirs("../reports", exist_ok=True)
            if not os.access("../reports", os.W_OK):
                messagebox.showerror("Error", "Cannot write to 'reports' directory. Check permissions.")
                return
            c = canvas.Canvas(filename, pagesize=A4)
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot create PDF: {e}\nEnsure the 'reports' directory is writable.")
            return

        y = 820
        c.setFont("Helvetica-Bold", 14)
        c.drawString(160, y, f"Detailed Sales Report - {t}")
        y -= 26

        grand_total = 0.0
        for mode in ("Cash", "Credit"):
            c.setFont("Helvetica-Bold", 12)
            c.drawString(50, y, f"{mode} Invoices")
            y -= 18
            c.setFont("Helvetica-Bold", 10)
            c.drawString(50, y, "Invoice No")
            c.drawString(150, y, "Item")
            c.drawRightString(300, y, "Qty")
            c.drawRightString(370, y, "Rate")
            c.drawRightString(470, y, "Line Total")
            y -= 12
            c.setFont("Helvetica", 10)

            self.c.execute("""SELECT invoice_no, description, qty, unit_price, total
                              FROM invoices
                              WHERE date=? AND payment_mode=? AND status='Active'
                              ORDER BY invoice_no""", (t, mode))
            rows = self.c.fetchall()
            mode_base = 0.0
            for inv, desc, qty, rate, total in rows:
                name = DISPLAY_NAME.get(desc, desc)
                c.drawString(50, y, inv)
                c.drawString(150, y, name)
                c.drawRightString(300, y, f"{qty:g}")
                c.drawRightString(370, y, f"{rate:.2f}")
                c.drawRightString(470, y, f"{total:.2f}")
                mode_base += float(total or 0)
                y -= 14
                if y < 80:
                    c.showPage()
                    y = 820
            mode_incl = round(mode_base * (1 + GST_RATE), 2)
            grand_total += mode_incl
            c.setFont("Helvetica-Bold", 11)
            c.drawRightString(470, y, f"{mode} Total (incl GST): {mode_incl:.2f}")
            y -= 22
            if y < 120:
                c.showPage()
                y = 820

        if y < 80:
            c.showPage()
            y = 820
        c.setFont("Helvetica-Bold", 12)
        c.drawRightString(470, y, f"Grand Total (incl GST): {grand_total:.2f}")

        try:
            c.save()
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot save PDF: {e}\nEnsure the 'reports' directory is writable and the file is not open elsewhere.")
            return
        self.open_file(filename)

    def cancelled_invoices_report(self):
        t = self.today_str()
        filename = os.path.join("../reports", f"Cancelled_{t}.pdf")
        try:
            os.makedirs("../reports", exist_ok=True)
            if not os.access("../reports", os.W_OK):
                messagebox.showerror("Error", "Cannot write to 'reports' directory. Check permissions.")
                return
            c = canvas.Canvas(filename, pagesize=A4)
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot create PDF: {e}\nEnsure the 'reports' directory is writable.")
            return

        y = 820
        c.setFont("Helvetica-Bold", 14)
        c.drawString(160, y, f"Cancelled Invoices - {t}")
        y -= 26
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, "Invoice No")
        c.drawString(200, y, "Amount (base)")
        y -= 12
        self.c.execute("""SELECT invoice_no, SUM(total) as s
                          FROM invoices
                          WHERE date=? AND status='Cancelled'
                          GROUP BY invoice_no
                          ORDER BY invoice_no""", (t,))
        tot = 0.0
        c.setFont("Helvetica", 10)
        for inv, s in self.c.fetchall():
            c.drawString(50, y, inv)
            c.drawRightString(470, y, f"{(s or 0):.2f}")
            tot += float(s or 0)
            y -= 14
            if y < 80:
                c.showPage()
                y = 820
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(470, y, f"Total Cancelled (base): {tot:.2f}")
        try:
            c.save()
        except PermissionError as e:
            messagebox.showerror("Error", f"Cannot save PDF: {e}\nEnsure the 'reports' directory is writable and the file is not open elsewhere.")
            return
        self.open_file(filename)

    def cancel_invoice_popup(self):
        p = tk.Toplevel(self.root)
        p.title("Cancel Invoice")
        p.grab_set()
        cols = ("Invoice No", "Date", "Total")
        tree = ttk.Treeview(p, columns=cols, show="headings", height=12)
        for ccol in cols:
            tree.heading(ccol, text=ccol)
            tree.column(ccol, width=160 if ccol != "Total" else 120, anchor=tk.CENTER)
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.c.execute("""SELECT invoice_no, date, SUM(total) AS s
                          FROM invoices
                          WHERE status='Active'
                          GROUP BY invoice_no, date
                          ORDER BY date, invoice_no""")
        for row in self.c.fetchall():
            tree.insert("", "end", values=row)

        btns = tk.Frame(p)
        btns.pack(fill=tk.X, pady=6)
        def cancel_selected():
            sel = tree.selection()
            if not sel:
                return
            inv = tree.item(sel[0], "values")[0]
            if not messagebox.askyesno("Confirm", f"Cancel entire invoice {inv}?"):
                return
            self.c.execute("UPDATE invoices SET status='Cancelled' WHERE invoice_no=?", (inv,))
            self.conn.commit()
            messagebox.showinfo("Cancelled", f"Invoice {inv} cancelled")
            p.destroy()
            self.update_today_total()

        tk.Button(btns, text="Cancel Selected", command=cancel_selected, width=16).pack(side=tk.RIGHT, padx=8)

    def erase_all_data(self):
        if not messagebox.askyesno("Confirm", "Erase ALL invoice data and reset numbering?"):
            return
        if not messagebox.askyesno("Confirm Again", "This will delete all invoice data but preserve items and shortcuts. Continue?"):
            return
        try:
            self.conn.close()
        except Exception:
            pass
        try:
            if os.path.exists(DB_FILE):
                os.remove(DB_FILE)
        except Exception as e:
            messagebox.showerror("Error", f"Could not remove DB: {e}")
            return
        self.conn = sqlite3.connect(DB_FILE)
        self.c = self.conn.cursor()
        self.setup_database()
        self.items.clear()
        self.current_invoice_no = None
        self.refresh_table()
        self.invoice_label.config(text="Invoice No: ")
        self.update_today_total()
        messagebox.showinfo("Done", "All invoice data erased and numbering reset.")

    def update_today_total(self):
        t = self.today_str()
        self.c.execute("SELECT SUM(total) FROM invoices WHERE date=? AND status='Active'", (t,))
        base = float(self.c.fetchone()[0] or 0.0)
        incl = round(base * (1 + GST_RATE), 2)
        self.today_total_label.config(text=f"Today's Sales Total: {incl:.2f}")

if __name__ == "__main__":
    root = tk.Tk()
    app = BillingApp(root)
    root.mainloop()
