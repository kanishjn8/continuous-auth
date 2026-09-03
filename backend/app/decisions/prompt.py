"""The native always-on-top challenge window.

Run as its own process (``python -m backend.app.decisions.prompt``) so that
dispatching a challenge never blocks the ingestion path. Everything it needs
arrives in the environment; the one-time response token is not passed in argv
because argv is visible in a process listing.

The answer is read from a masked field, posted straight to the C7 response
endpoint, and never written to disk, stdout, or a log. One dispatched
challenge allows exactly one attempt: a wrong answer is a failed response,
which is what PLAN.md Section 11.3 escalates on. If the risk is still
elevated the engine dispatches a fresh challenge on the next window.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 10.0


def _submit(endpoint: str, token: str, answer: str) -> tuple[bool, str]:
    payload = json.dumps({"answer": answer}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed loopback endpoint from settings
        endpoint,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "X-Challenge-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        outcome = str(body.get("outcome", ""))
        return outcome == "ACCEPTED", outcome or "UNKNOWN"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP_{exc.code}"
    except (urllib.error.URLError, OSError, ValueError):
        return False, "UNREACHABLE"


def main() -> int:
    endpoint = os.environ.get("CA_CHALLENGE_ENDPOINT", "")
    question = os.environ.get("CA_CHALLENGE_QUESTION", "")
    token = os.environ.get("CA_CHALLENGE_TOKEN", "")
    blocking = os.environ.get("CA_CHALLENGE_BLOCKING") == "1"
    if not endpoint or not question or not token:
        return 2

    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return 3

    root = tk.Tk()
    root.title("Identity verification required")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    if blocking:
        root.attributes("-fullscreen", True)
        # A forced reauthentication must be answered; the window offers no exit.
        root.protocol("WM_DELETE_WINDOW", lambda: None)
    else:
        root.geometry("460x220")
        root.protocol("WM_DELETE_WINDOW", root.destroy)

    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)

    heading = "Identity verification required" if blocking else "Quick identity check"
    ttk.Label(frame, text=heading, font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            "Unusual interaction behaviour was detected on this session."
            if blocking
            else "Please confirm it is still you."
        ),
        wraplength=520,
    ).pack(anchor="w", pady=(4, 16))
    ttk.Label(frame, text=question, wraplength=520, font=("Segoe UI", 10, "bold")).pack(anchor="w")

    answer = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=answer, show="•", width=40)
    entry.pack(anchor="w", pady=(6, 6), fill="x")

    status = ttk.Label(frame, text="", foreground="#b00020", wraplength=520)
    status.pack(anchor="w")

    def submit() -> None:
        value = answer.get()
        if not value:
            status.configure(text="Enter your security answer.")
            return
        button.configure(state="disabled")
        accepted, outcome = _submit(endpoint, token, value)
        answer.set("")
        if accepted:
            root.destroy()
            return
        if outcome == "UNREACHABLE":
            status.configure(text="Verification service unreachable.")
            button.configure(state="normal")
            return
        # The attempt is consumed; the engine escalates from here.
        status.configure(text="Verification failed.")
        root.after(1500, root.destroy)

    button = ttk.Button(frame, text="Verify", command=submit)
    button.pack(anchor="w", pady=(12, 0))
    entry.bind("<Return>", lambda _event: submit())

    def take_focus() -> None:
        root.lift()
        root.focus_force()
        entry.focus_set()

    root.after(100, take_focus)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
