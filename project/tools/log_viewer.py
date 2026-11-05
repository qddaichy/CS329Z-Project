"""Display live agent decisions alongside the current plan and skills."""
import argparse
import json
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext, ttk


def overview_sections(data):
    """Format status data independently of Tk for easy verification."""
    game = data.get("game") or {}
    clock = game.get("time") or {}
    clock_text = f"{clock.get('season', '?')} {clock.get('dayofmonth', '?')} · {clock.get('time', '?')}"
    lines = [("heading", "GOAL"), ("body", str(data.get("goal") or "Waiting for goal")),
             ("heading", "GAME"),
             ("body", f"{game.get('location', '—')}  ·  {clock_text}  ·  {game.get('money', '—')} gold")]
    plan = data.get("plan") or {}
    lines.append(("heading", "CURRENT PLAN"))
    if not plan:
        lines.append(("muted", "Waiting for a plan"))
    elif "day_goal" in plan:
        for label, key in (("Today's goal", "day_goal"), ("Activities", "activities"),
                           ("Verify", "verification"), ("Skill review", "skill_review"),
                           ("Tool opportunity", "tool_opportunity")):
            if plan.get(key):
                lines.append(("body", f"{label}: {plan[key]}"))
    else:
        for item in plan.get("steps", []):
            lines.append(("body", f"• {item.get('goal', '')} — {item.get('verification', '')}"))
    learning = data.get("learning") or {}
    latest = learning.get("latest_review") or {}
    review = latest.get("review")
    if review is not None:
        lines.append(("heading", "DAILY REVIEW"))
        if isinstance(review, str):
            lines.append(("body", review))
        elif isinstance(review, dict):
            lines.append(("body", review.get("allocation_plan", "")))
            for item in review.get("activities", []):
                lines.append(("body", f"{item['activity']} [{item['status']}]: {item['decision']}"))
                lines.append(("muted", f"Estimated net gold: {item['net_gold_estimate']} | "
                              f"Active minutes: {item['active_minutes_estimate']} | Payback days: {item['payback_days_estimate']}"))
                lines.append(("muted", f"Evidence: {', '.join(item['evidence_ids']) or 'none'} | "
                              f"Uncertainty: {item['uncertainty']} | Next test: {item['next_test']}"))
    lines.append(("heading", "SKILLS"))
    skills = data.get("skills") or {}
    if not skills:
        lines.append(("muted", "No skills recorded"))
    for name, skill in skills.items():
        state = "verified" if skill.get("verified") else "candidate"
        description = skill.get("description") or ""
        lines.append(("body", f"{name}  ·  {state}  {description}"))
    return lines


def display_number(value):
    return "unknown" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)


def dashboard_data(data):
    """Pure presentation model, also usable with older status files."""
    game, plan = data.get("game") or {}, data.get("plan") or {}
    clock = game.get("time") or {}
    learning, workflow = data.get("learning") or {}, data.get("workflow") or {}
    latest = learning.get("latest_review") or {}
    review = latest.get("review")
    skills = data.get("skills") or {}
    skill_rows = []
    for name, skill in skills.items():
        status = "passed" if skill.get("verified") else "failed" if skill.get("last_status") == "failed" else "candidate"
        condition = json.dumps(skill.get("postcondition", {}), ensure_ascii=False)
        steps = skill.get('code') or "\n".join(f"{i + 1}. {s.get('tool')} {json.dumps(s.get('arguments', {}), ensure_ascii=False)}"
                                                for i, s in enumerate(skill.get("steps", [])))
        detail = (f"{skill.get('description', '')}\n\nRevision {skill.get('revision', 1)} | "
                  f"Trials {skill.get('trials', 0)} | Reuses {skill.get('uses', 0)}\n"
                  f"Passed {skill.get('passes', 0)} | Failed {skill.get('failures', 0)}\n"
                  f"Last outcome: {skill.get('last_status', status)}\n\nPOSTCONDITION\n{condition}\n\nRECIPE\n{steps}")
        if skill.get("last_error"):
            detail += "\n\nLAST ERROR\n" + skill["last_error"]
        skill_rows.append({"id": name, "values": (name, status, skill.get("trials", 0), skill.get("uses", 0)),
                           "tag": status, "detail": detail})
    economy_rows = []
    if isinstance(review, dict):
        for item in review.get("activities", []):
            detail = (f"OBSERVED / CONTEXT\n{item.get('observation', '')}\n\n"
                      f"DECISION\n{item.get('decision', '')}\n\nUNCERTAINTY\n{item.get('uncertainty', '')}\n\n"
                      f"NEXT TEST\n{item.get('next_test', '')}\n\n"
                      f"Evidence: {', '.join(item.get('evidence_ids', [])) or 'none'}\n"
                      f"Estimated active minutes: {display_number(item.get('active_minutes_estimate'))}")
            economy_rows.append({"id": item.get("activity", "?"), "values": (
                item.get("activity", "?"), item.get("status", "unknown"), display_number(item.get("net_gold_estimate")),
                display_number(item.get("payback_days_estimate"))), "tag": item.get("status", "unknown"), "detail": detail})
        for index, item in enumerate(review.get('item_prices', [])):
            label = f"Price: {item.get('item', '?')}"
            detail = (f"UNIT PRICE (gold)\n{display_number(item.get('unit_price'))}\n\n"
                      f"QUALITY\n{item.get('quality', '')}\n\nSALE CHANNEL\n{item.get('sale_channel', '')}\n\n"
                      f"UNCERTAINTY\n{item.get('uncertainty', '')}\n\n"
                      f"Evidence: {', '.join(item.get('evidence_ids', [])) or 'none'}")
            economy_rows.append({'id': f'item_price_{index}', 'values': (label, item.get('status', 'unknown'),
                                 display_number(item.get('unit_price')), '--'),
                                 'tag': item.get('status', 'unknown'), 'detail': detail})
    date = [clock.get(k) for k in ("year", "season", "dayofmonth")]
    stale_plan = bool(plan.get("date") and None not in date and plan["date"] != date)
    pending = workflow.get("plan_required", stale_plan)
    plan_lines = []
    if pending:
        label = 'plan required' if workflow.get('review_mode') == 'none' else 'plan and review required'
        plan_lines.append(f"NEW DAY: {label}.\nPrevious plan shown below.\n")
    if "day_goal" in plan:
        for label, key in (("TODAY'S GOAL", "day_goal"), ("ACTIVITIES / PRIORITIES", "activities"),
                           ("SUCCESS CHECK", "verification"), ("SKILL REVIEW", "skill_review"),
                           ("NEXT SKILL OPPORTUNITY", "tool_opportunity")):
            if key in plan:
                plan_lines.append(f"{label}\n{plan[key]}")
    elif plan.get("steps"):
        plan_lines.extend(f"{i + 1}. {item.get('goal', '')}\nVerify: {item.get('verification', '')}"
                          for i, item in enumerate(plan["steps"]))
    else:
        plan_lines.append("This baseline acts on the overall goal without a daily plan." if workflow.get('review_mode') == 'none'
                          else "Waiting for the first daily plan.")
    review_text = review if isinstance(review, str) else json.dumps(review, indent=2, ensure_ascii=False) if review is not None else plan.get("skill_review", "No review recorded yet.")
    general = learning.get('latest_general_reflection') or {}
    if general.get('review'):
        review_text = f"GENERAL DAILY REFLECTION\n{general['review']}\n\nECONOMIC KNOWLEDGE\n{review_text}"
    if review is None and workflow.get('review_mode') == 'none' and 'skill_review' not in plan:
        review_text = 'This baseline has no required daily review.'
    if latest.get("date"):
        review_text = f"Recorded on {latest['date']} | revision {latest.get('revision', 1)}\n\n" + review_text
    if latest.get("date") and None not in date and latest["date"] != date:
        review_text = "PREVIOUS DATE: awaiting today's review.\n\n" + review_text
    enabled = workflow.get("synthesis_enabled", data.get("skill_synthesis", True))
    usage = data.get("usage") or {}
    tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    phase = (data.get("result") or {}).get("stop_reason") or data.get("phase", "starting")
    progress = game.get("progress") or {}
    target = progress.get("required")
    cash = f"{display_number(game.get('money'))} gold" + (f" | target {target}" if target is not None else "")
    if progress.get("kind") == "profit_horizon":
        cash += (f" | net {display_number(progress.get('net_gain'))} | "
                 f"days {progress.get('elapsed_game_days', 0)}/{progress.get('required_game_days', '?')}")
    supported = sum(row["tag"] == "supported" for row in economy_rows)
    economy_empty = "No economic entries yet." if learning.get("mode") == "economic" else "This variant has no structured economic ledger. See Daily review for its retained lessons."
    plan_status = 'not used' if workflow.get('review_mode') == 'none' else 'DUE' if pending else 'recorded' if plan else 'waiting'
    return {"goal": data.get("goal") or "Waiting for goal", "phase": phase,
            "identity": f"{data.get('strategy', 'agent')}  |  {data.get('model', 'model pending')}",
            "game": f"{clock.get('season', '?')} {clock.get('dayofmonth', '?')}, year {clock.get('year', '?')}  |  "
                    f"{clock.get('time', '?')}  |  {game.get('location', '?')}  |  {cash}",
            "metrics": f"Tokens {tokens:,}   |   Plans / review: {plan_status}   |   "
                       f"Skills {sum(bool(s.get('verified')) for s in skills.values())}/{len(skills)} passed   |   "
                       f"Economic claims {supported}/{len(economy_rows)} supported",
            "plan": "\n\n".join(plan_lines), "skill_rows": skill_rows,
            "skill_caption": "Synthesis ON | passed = declared condition only" if enabled else "Synthesis OFF | --disable-skill-synthesis",
            "skill_empty": ("No skills recorded yet." if workflow.get('review_mode') == 'none' else "No tools yet. See the plan's next skill opportunity.") if enabled else "Tool creation is disabled for this run.",
            "economy_rows": economy_rows, "economy_empty": economy_empty,
            "allocation": review.get("allocation_plan", "") if isinstance(review, dict) else "",
            "review": review_text, "pending": pending}


class LiveMonitor:
    BG, CARD, FG, MUTED = "#101826", "#19283a", "#e5eef8", "#9aafc5"

    def __init__(self, window):
        self.window = window
        window.title("StarDojo | Agent Observatory")
        window.geometry("1240x870+20+20")
        window.minsize(920, 650)
        window.configure(bg=self.BG)
        style = ttk.Style(window)
        style.theme_use("clam")
        style.configure("Treeview", background=self.CARD, fieldbackground=self.CARD, foreground=self.FG,
                        rowheight=28, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#25394f", foreground=self.FG,
                        font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#304c69")], foreground=[("selected", "white")])
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#25394f", foreground=self.FG, padding=(15, 7))
        style.map("TNotebook.Tab", background=[("selected", "#365675")])
        head = tk.Frame(window, bg=self.BG, padx=16, pady=10)
        head.pack(fill="x")
        title = tk.Frame(head, bg=self.BG)
        title.pack(fill="x")
        tk.Label(title, text="STARDOJO / AGENT OBSERVATORY", bg=self.BG, fg="#8cc8ff",
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        self.phase = tk.Label(title, bg="#25394f", fg="#8fe0bb", padx=10, pady=3, font=("Segoe UI", 9, "bold"))
        self.phase.pack(side="right")
        pin = tk.BooleanVar(value=False)
        tk.Checkbutton(title, text="Pin", variable=pin, command=lambda: window.attributes("-topmost", pin.get()),
                       bg=self.BG, fg=self.MUTED, selectcolor=self.CARD, activebackground=self.BG).pack(side="right", padx=8)
        self.identity = self.label(head, self.MUTED, 9)
        self.goal = self.label(head, self.FG, 12, True)
        self.game = self.label(head, "#b3d9f4", 10)
        self.metrics = self.label(head, self.MUTED, 9)
        window.bind("<Configure>", lambda event: self.goal.configure(wraplength=max(700, window.winfo_width() - 40))
                    if event.widget == window else None)
        panes = tk.PanedWindow(window, orient="vertical", bg=self.BG, sashwidth=7, bd=0)
        panes.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        cards = tk.PanedWindow(panes, orient="horizontal", bg=self.BG, sashwidth=6, bd=0)
        panes.add(cards, minsize=300, height=430, stretch="always")
        plan = self.card(cards, "01  DAILY PLAN", "Goal, priorities and success criteria")
        self.plan = self.textbox(plan, height=15)
        skills = self.card(cards, "02  SKILLS", "Select a skill to inspect its recipe and outcomes")
        self.skill_caption = self.label(skills, self.MUTED, 9)
        self.skill_table, self.skill_detail = self.table(skills, ("Skill", "State", "Tests", "Uses"), (125, 75, 45, 45))
        economics = self.card(cards, "03  ECONOMIC KNOWLEDGE", "Model estimates; supported claims are not certified facts")
        self.allocation = self.label(economics, self.MUTED, 9)
        self.allocation.configure(wraplength=330)
        self.economy_table, self.economy_detail = self.table(economics, ("Activity / item", "Belief", "Gold", "Payback d"), (110, 85, 65, 65))
        self.skill_table.bind("<<TreeviewSelect>>", lambda event: self.select_detail(self.skill_table, self.skill_detail))
        self.economy_table.bind("<<TreeviewSelect>>", lambda event: self.select_detail(self.economy_table, self.economy_detail))
        tabs = ttk.Notebook(panes)
        panes.add(tabs, minsize=120, height=200, stretch="always")
        self.log = self.textbox(tabs, height=9, pack=False, font="Consolas")
        self.review = self.textbox(tabs, height=9, pack=False)
        tabs.add(self.log, text="Execution log")
        tabs.add(self.review, text="Daily review")
        self.follow = tk.BooleanVar(value=True)
        tk.Checkbutton(window, text="Follow latest log", variable=self.follow, bg=self.BG, fg=self.MUTED,
                       selectcolor=self.CARD, activebackground=self.BG).pack(anchor="e", padx=15, pady=(0, 6))
        self.render({})

    def label(self, parent, color, size, bold=False):
        label = tk.Label(parent, text="", bg=parent.cget("bg"), fg=color, justify="left", anchor="w",
                         font=("Segoe UI", size, "bold" if bold else "normal"))
        label.pack(fill="x", pady=3)
        return label

    def card(self, parent, title, caption):
        card = tk.Frame(parent, bg=self.CARD, padx=12, pady=10)
        parent.add(card, minsize=270, stretch="always")
        self.label(card, "#8cc8ff", 10, True).configure(text=title)
        subtitle = self.label(card, self.MUTED, 9)
        subtitle.configure(text=caption, wraplength=320)
        return card

    def textbox(self, parent, height=6, pack=True, font="Segoe UI"):
        text = scrolledtext.ScrolledText(parent, height=height, wrap="word", bg=self.CARD, fg=self.FG,
                                        relief="flat", padx=6, pady=8, font=(font, 10), spacing3=3)
        text.configure(state="disabled")
        if pack:
            text.pack(fill="both", expand=True, pady=(5, 0))
        return text

    def table(self, parent, columns, widths):
        frame = tk.Frame(parent, bg=self.CARD)
        frame.pack(fill="x", pady=(6, 3))
        table = ttk.Treeview(frame, columns=columns, show="headings", height=5, selectmode="browse")
        for column, width in zip(columns, widths):
            table.heading(column, text=column)
            table.column(column, width=width, minwidth=35, stretch=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        table.pack(fill="x", expand=True)
        for tag, color in {"passed": "#8fe0bb", "supported": "#8fe0bb", "candidate": "#f3d38b",
                           "hypothesis": "#f3d38b", "failed": "#ff9d9d", "contradicted": "#ff9d9d", "unknown": self.MUTED}.items():
            table.tag_configure(tag, foreground=color)
        table.rows = {}
        return table, self.textbox(parent)

    @staticmethod
    def set_text(widget, content):
        if getattr(widget, "last_content", None) == content:
            return
        position = widget.yview()[0]
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", content)
        widget.configure(state="disabled")
        widget.yview_moveto(position)
        widget.last_content = content

    def select_detail(self, table, detail):
        selected = table.selection()
        if selected and selected[0] in table.rows:
            self.set_text(detail, table.rows[selected[0]]["detail"])

    def update_table(self, table, detail, rows, empty):
        updated = {row["id"]: row for row in rows}
        if table.rows != updated:
            selection = table.selection()
            table.delete(*table.get_children())
            table.rows = updated
            for row in rows:
                table.insert("", "end", iid=row["id"], values=row["values"], tags=(row["tag"],))
            if rows:
                table.selection_set(selection[0] if selection and selection[0] in updated else rows[0]["id"])
        if rows:
            self.select_detail(table, detail)
        else:
            self.set_text(detail, empty)

    def render(self, data):
        view = dashboard_data(data)
        for key in ("identity", "goal", "game", "metrics", "phase", "skill_caption", "allocation"):
            getattr(self, key).configure(text=view[key])
        self.metrics.configure(fg="#f3d38b" if view["pending"] else self.MUTED)
        self.set_text(self.plan, view["plan"])
        self.set_text(self.review, view["review"])
        self.update_table(self.skill_table, self.skill_detail, view["skill_rows"], view["skill_empty"])
        self.update_table(self.economy_table, self.economy_detail, view["economy_rows"], view["economy_empty"])

    def append_log(self, chunk):
        if not chunk:
            return
        self.log.configure(state="normal")
        self.log.insert("end", chunk)
        # Keep the UI responsive over long episodes; the complete log remains on disk.
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "1001.0")
        if self.follow.get():
            self.log.see("end")
        self.log.configure(state="disabled")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    window = tk.Tk()
    monitor = LiveMonitor(window)
    source = args.log.open("r", encoding="utf-8")
    status_path = args.log.with_name("live_status.json")
    previous_status = None

    def update():
        nonlocal previous_status
        try:
            raw = status_path.read_text(encoding="utf-8")
            if raw != previous_status:
                monitor.render(json.loads(raw))
                previous_status = raw
        except (OSError, ValueError, TypeError, KeyError):
            pass
        monitor.append_log(source.read(65536))
        window.after(250, update)

    def close():
        source.close()
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", close)
    update()
    window.mainloop()


if __name__ == "__main__":
    main()
