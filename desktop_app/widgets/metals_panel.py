"""Physical Silver Eagles / bullion inventory and clearly hypothetical hedges."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from crypto_research.timeutil import now_ms, to_iso
from paper_trading import metals
from ..tooltip import Tooltip
from .scroll_frame import ScrollFrame


class MetalsPanel(ScrollFrame):
    def __init__(self, parent, path):
        super().__init__(parent)
        self.path = path
        self.content.configure(padding=8)
        ttk.Label(self.content, text="Physical metals — manual USD inventory and downside research", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(self.content, text="Enter actual fine troy ounces and dealer buyback bids, not advertised retail asking prices. "
                  "Keep Eagle premiums separate from silver-price exposure. No hedge is placed here.", wraplength=840).pack(anchor="w", pady=5)
        form = ttk.Frame(self.content)
        form.pack(fill="x")
        self.values = {}
        fields = (("metal", "Metal", "silver"), ("description", "Product / purchase lot", "American Silver Eagles"),
                  ("ounces", "Fine troy ounces", ""), ("cost", "Total paid, including acquisition costs ($)", ""),
                  ("bid", "Dealer buyback bid per fine oz ($)", ""), ("spot", "Spot at this quote, per fine oz ($)", ""),
                  ("quote_date", "Quote date (YYYY-MM-DD, UTC)", to_iso(now_ms())[:10]),
                  ("exit_cost", "Estimated holding/resale/shipping costs ($)", "0"))
        for index, (key, text, default) in enumerate(fields):
            row, column = divmod(index, 2)
            cell = ttk.Frame(form)
            cell.grid(row=row, column=column, sticky="ew", padx=(0, 18), pady=2)
            ttk.Label(cell, text=text).pack(anchor="w")
            variable = self.values[key] = tk.StringVar(value=default)
            widget = ttk.Combobox(cell, textvariable=variable, values=("silver", "gold"), state="readonly") if key == "metal" else ttk.Entry(cell, textvariable=variable, width=45)
            widget.pack(fill="x")
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        ttk.Button(self.content, text="Save purchase lot", command=self.add).pack(anchor="w", pady=6)
        listing_frame = ttk.Frame(self.content)
        listing_frame.pack(fill="x")
        self.listing = ttk.Treeview(listing_frame, columns=("product", "ounces", "cost", "bid", "state"), show="headings", height=4)
        for key, title in (("product", "Product"), ("ounces", "Fine oz"), ("cost", "Cost $"), ("bid", "Bid $/oz"), ("state", "Inventory")):
            self.listing.heading(key, text=title)
            self.listing.column(key, width=145 if key == "product" else 100)
        scrollbar = ttk.Scrollbar(listing_frame, orient="vertical", command=self.listing.yview)
        self.listing.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.listing.pack(side="left", fill="x", expand=True)
        self.listing.bind("<<TreeviewSelect>>", self.load_selected)
        ttk.Button(self.content, text="Update selected lot's quote and estimated costs", command=self.update_quote).pack(anchor="w", pady=5)
        ttk.Button(self.content, text="Record completed physical sale (selected lot)", command=self.mark_sold).pack(anchor="w", pady=5)
        self.summary = tk.StringVar()
        ttk.Label(self.content, textvariable=self.summary, wraplength=840).pack(anchor="w", pady=5)
        ttk.Label(self.content, text="Hypothetical protection at expiry — not a quoted product or insurance policy", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.hedge = {}
        for key, title, default in (("spot", "Scenario starting spot ($/fine oz)", ""),
                                     ("premium_change", "Change in dealer premium/discount ($/oz)", "0"),
                                     ("ounces", "Hypothetical covered ounces (blank = no hedge)", ""),
                                     ("strike", "Hypothetical strike ($/oz)", ""),
                                     ("premium", "Total hedge price/fees per covered oz ($)", ""),
                                     ("expiry", "Analyze at expiry (YYYY-MM-DD)", "")):
            row = ttk.Frame(self.content)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=title, width=60).pack(side="left")
            self.hedge[key] = tk.StringVar(value=default)
            ttk.Entry(row, textvariable=self.hedge[key], width=24).pack(side="left")
        button = ttk.Button(self.content, text="Compare price scenarios for selected metal", command=self.scenarios)
        button.pack(anchor="w", pady=6)
        Tooltip(button, lambda: "Uses an idealized cash put payoff against the metal price at expiry, not an actual ETF or futures option. "
                "Contract size, tracking differences, early resale, expiry timing, taxes and actual quotes need separate review. "
                "This does not insure your purchase cost or coin premium.")
        self.results = tk.StringVar(value="Scenarios are not forecasts. Theft/damage insurance is separate from price protection.")
        ttk.Label(self.content, textvariable=self.results, wraplength=840, justify="left").pack(anchor="w", pady=6)
        self.refresh()

    def refresh(self):
        try:
            self.lots = metals.load(self.path)
        except Exception as exc:
            self.lots = None
            self.summary.set(f"Cannot read inventory; it will not be overwritten: {exc}")
            return
        for row in self.listing.get_children():
            self.listing.delete(row)  # safety-audit: allow -- clears local inventory rows
        for index, value in enumerate(self.lots):
            self.listing.insert("", "end", iid=str(index), values=(value["description"], value["ounces"], value["cost"], value["bid"], "Sold" if value["sold"] else "Held"))
        lines = []
        for metal in ("silver", "gold"):
            total = metals.inventory(self.lots, metal)
            if total["ounces"]:
                lines.append(f"{metal.capitalize()}: {total['ounces']} fine oz | dealer-bid value after estimated resale costs ${total['resale_value']:.2f} | "
                             f"gain/loss vs cost ${total['pnl']:+.2f} | break-even bid ${total['break_even_bid']:.2f}/oz")
        dates = sorted({p["quote_date"] for p in self.lots if not p["sold"]})
        self.summary.set("\n".join(lines + (["Manual quote dates: " + ", ".join(dates) + ". Old quotes must be refreshed before decisions."] if dates else ["No physical inventory recorded yet."])))

    def add(self):
        try:
            if self.lots is None:
                raise ValueError("Repair the unreadable inventory before saving")
            value = metals.lot({key: variable.get() for key, variable in self.values.items()})
            metals.save(self.path, self.lots + [value])
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Inventory not saved", str(exc), parent=self)

    def load_selected(self, event=None):
        selected = self.listing.selection()
        if not selected or self.lots is None:
            return
        value = self.lots[int(selected[0])]
        for key, variable in self.values.items():
            variable.set(value[key])

    def update_quote(self):
        selected = self.listing.selection()
        if not selected or self.lots is None:
            return
        try:
            index = int(selected[0])
            value = dict(self.lots[index])
            if value["sold"]:
                raise ValueError("Choose a held lot to update its resale quote")
            for key in ("bid", "spot", "quote_date", "exit_cost"):
                value[key] = self.values[key].get()
            lots = list(self.lots)
            lots[index] = metals.lot(value)
            metals.save(self.path, lots)
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Quote not updated", str(exc), parent=self)

    def mark_sold(self):
        selected = self.listing.selection()
        if not selected or self.lots is None:
            return
        index = int(selected[0])
        quantity = simpledialog.askstring("Record a completed physical sale", "Fine troy ounces already sold from this lot. This updates the ledger only; it does not sell anything.",
                                          initialvalue=self.lots[index]["ounces"], parent=self)
        if quantity is not None:
            try:
                lots = metals.record_sale(self.lots, index, quantity)
                metals.save(self.path, lots)
                self.refresh()
            except Exception as exc:
                messagebox.showerror("Sale not recorded", str(exc), parent=self)

    def scenarios(self):
        try:
            if self.lots is None:
                raise ValueError("Inventory is unreadable")
            values = {key: variable.get() for key, variable in self.hedge.items()}
            hedge = values if values["ounces"] else None
            lines = ["Price move | unhedged gain/loss | idealized hedged gain/loss (USD, at expiry)",
                     "Hypothetical put entered." if hedge else "No hedge entered; both columns will match."]
            for shock in ("-0.3", "-0.2", "-0.1", "0", "0.1", "0.2", "0.3"):
                result = metals.scenario(self.lots, self.values["metal"].get(), values["spot"], shock,
                                         premium_change=values["premium_change"], hedge=hedge)
                lines.append(f"{float(shock) * 100:+.0f}% | ${result['unhedged_pnl']:+.2f} | ${result['hypothetical_hedged_pnl']:+.2f}")
            lines.append("A spot-price hedge may leave losses from premiums, dealer spreads, costs and timing. This is not guaranteed protection or an order recommendation.")
            self.results.set("\n".join(lines))
        except Exception as exc:
            messagebox.showerror("Cannot compare scenarios", str(exc), parent=self)
