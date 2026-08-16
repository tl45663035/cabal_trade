"""cancel_item() failure paths.

Each case states what the code SHOULD do (from its own docstring and comments)
and asserts what it ACTUALLY does. Divergences are findings.
"""
import harness as H
from harness import Harness, check, note, section, summary, run, where, make_row
import trade

VIP = "Upgrade Core (Ultimate)"


def fresh(**flags):
    h = Harness(rows=[make_row(1, VIP, price=410_000, qty=100),
                      make_row(2, "Force Core (Ultimate)",
                               price=1_500_000, qty=50)])
    for key, value in flags.items():
        setattr(h, key, value)
    return h


# ---------------------------------------------------------------------------
section("1a. the Registration Extension dialog never appears")
# SHOULD: abort, back out, return False, commit nothing, and record the abort.
h = fresh(suppress_extension=True)
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1a returns False", ok is False, f"got {ok!r}")
check("1a raised nothing", exc is None, repr(exc))
check("1a recorded cancel.aborted", "cancel.aborted" in h.labels(),
      str(h.labels()))
check("1a reports committed=False honestly",
      (h.rec("cancel.aborted") or {}).get("committed") is False,
      str(h.rec("cancel.aborted")))
check("1a listing untouched", len(h.rows) == 2 and h.rows[0].name == VIP)
check("1a clicked only Change", len(h.clicks()) == 1, str(h.clicks()))
check("1a printed the diagnostic evidence",
      h.said("dialog_kind sees") and h.said("strongest words"), h.out()[-400:])
check("1a said nothing was changed", h.said("Nothing was changed"), h.out()[-300:])
# after_change is recorded with dialog='none' -- the frame that shows the miss
check("1a recorded cancel.after_change",
      "cancel.after_change" in h.labels(), str(h.labels()))


# ---------------------------------------------------------------------------
section("1b. the dialog appears, then vanishes before its Cancel is found")
# SHOULD: abort before clicking anything else; commit nothing.
h = fresh(extension_vanishes=True)
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1b returns False", ok is False, f"got {ok!r}")
check("1b aborted on the missing Cancel button",
      h.said("no Cancel button"), h.out()[-300:])
check("1b committed=False", (h.rec("cancel.aborted") or {}).get("committed") is False)
check("1b listing untouched", len(h.rows) == 2)
check("1b clicked only Change", len(h.clicks()) == 1, str(h.clicks()))


# ---------------------------------------------------------------------------
section("1c. the Confirmation button is never found")
# SHOULD: abort with nothing committed, back out of the confirm dialog with
# Cancel, and leave the listing on the market.
h = fresh(no_confirm_button=True)
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1c returns False", ok is False, f"got {ok!r}")
check("1c aborted on the missing Confirmation button",
      h.said("no Confirmation button"), h.out()[-400:])
check("1c committed=False", (h.rec("cancel.aborted") or {}).get("committed") is False)
check("1c listing still on the market", len(h.rows) == 2, str(h.rows))
check("1c backed out of the dialog", h.said("Backed out of the open dialog"),
      h.out()[-300:])
check("1c dialog really closed", h.dialog is None, repr(h.dialog))


# ---------------------------------------------------------------------------
section("1d. the dialog stays open after Confirmation -- game REFUSED it")
# The real observed failure. SHOULD: report False, never click again, and say
# the listing is still on the market (which it is).
h = fresh(confirm_sticks=True, commit_on_stick=False)
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1d returns False", ok is False, f"got {ok!r}")
check("1d aborted on 'dialog stayed open'",
      h.said("dialog stayed open after Confirmation"), h.out()[-500:])
# HEDGED, deliberately, and this check used to demand the opposite.
#
# A confirmation dialog still up USUALLY means the game refused -- but the game
# stacks confirmation dialogs (MAX_CONFIRM_STEPS exists for that on the
# register side), so it can commit AND still be showing one. Stating "the game
# did NOT accept it" as fact would send the operator away from a listing that
# had in fact been withdrawn, leaving the stack sitting unlisted in the work
# tab while they looked elsewhere.
#
# So the contract is: say what it usually means, and say plainly that it is not
# proof. Demanding the unhedged sentence was demanding a claim the code cannot
# support from one frame.
check("1d says the cancellation was probably refused",
      h.said("means the game refused the cancellation"), h.out()[-600:])
check("1d does NOT state that as proof",
      h.said("this is not proof"), h.out()[-600:])
check("1d tells the operator to check the listing",
      h.said("CHECK THE LISTING before retrying"), h.out()[-600:])
check("1d listing really is still there", len(h.rows) == 2)
check("1d clicked nothing after Confirmation", len(h.clicks()) == 3,
      str(h.clicks()))
aborted = h.rec("cancel.aborted") or {}
# TWO DIFFERENT FACTS, and this check used to conflate them.
#
# `committed` means only "the Confirmation click was sent". It is set BEFORE
# the click on purpose: with the reverse order there was a window where the
# confirm had been delivered and committed still read False, and _relist_cycle
# retries on `committed is False` -- so with two identical stacks at the same
# price the retry re-resolved to the surviving sibling and withdrew that one
# too. Forcing committed=False here to mean "refused" would put that back.
#
# Whether the game ACCEPTED it is the separate question, and `accepted` is the
# field that answers it -- added precisely because the corpus was storing the
# first while the log printed the second. So the honest assertion is that both
# are recorded, and that they disagree in the way this scenario describes.
check("1d records that the click was sent",
      aborted.get("committed") is True,
      f"committed must stay True -- it means the Confirmation click went out, "
      f"and _relist_cycle retrying on False withdraws a sibling stack. "
      f"got {aborted.get('committed')!r}")
check("1d records that the game did not accept it",
      aborted.get("accepted") is False,
      f"`accepted` is what says the game refused, and it is the field the "
      f"corpus index should be read on. got {aborted.get('accepted')!r}")
check("1d records the dialog it saw afterwards",
      aborted.get("dialog_after") == "confirm",
      f"the observation the verdict rests on is stored too, so a later reader "
      f"can re-judge it. got {aborted.get('dialog_after')!r}")


# ---------------------------------------------------------------------------
section("1e. the dialog stays open after Confirmation -- game ACCEPTED it")
# A second stacked dialog (the game does exactly this on the register side)
# leaves 'confirm' up while the withdrawal has gone through.
h = fresh(confirm_sticks=True, commit_on_stick=True)
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1e returns False", ok is False, f"got {ok!r}")
check("1e the listing IS gone", len(h.rows) == 1, str(h.rows))
if h.said("the game did NOT accept the cancellation"):
    note("1e cancel_item asserts the opposite of the truth",
         "the listing was withdrawn, yet trade.py:3604-3611 tells the operator "
         "'the listing should still be on the market' purely because a confirm "
         "dialog is still up. _relist_cycle then returns FAILED without "
         "relisting, so the item is stranded in the inventory and the operator "
         "is told nothing was cancelled.")
check("1e must not claim the listing is still on the market",
      not h.said("the game did NOT accept the cancellation"),
      "a stacked second dialog makes the 'still open => refused' inference wrong")


# ---------------------------------------------------------------------------
section("1f. PermissionError raised by the Change click")
# SHOULD (arguably): be reported like any other failure, with the abort frame
# recorded. ACTUALLY: PermissionError is not Aborted, so nothing is recorded.
h = fresh(click_fault={1: PermissionError(trade.CURSOR_BLOCKED_HINT)})
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1f raises PermissionError out of cancel_item",
      isinstance(exc, PermissionError), repr(exc))
check("1f returned nothing", ok is None)
check("1f recorded NO abort", "cancel.aborted" not in h.labels(), str(h.labels()))
check("1f last record is cancel.before_change",
      h.labels()[-1] == "cancel.before_change", str(h.labels()))
note("1f escape path", where(exc))


# ---------------------------------------------------------------------------
section("1g. PermissionError raised by the CONFIRMATION click")
h = fresh(click_fault={3: PermissionError(trade.CURSOR_BLOCKED_HINT)})
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1g raises PermissionError", isinstance(exc, PermissionError), repr(exc))
check("1g recorded no abort and no commit",
      "cancel.aborted" not in h.labels() and "cancel.committed" not in h.labels(),
      str(h.labels()))
note("1g committed flag",
     "committed is set at trade.py:3577, AFTER click() returns, so an "
     "exception from the click loses the flag entirely -- there is no report "
     "of it either way, because the exception bypasses the handler that "
     "would have written record('cancel.aborted', committed=...)")


# ---------------------------------------------------------------------------
section("1h. grab() raises OSError")
h = fresh(grab_fault={1: OSError("gdi32.dll: screen capture failed")})
with h:
    ok, exc = run(trade.cancel_item, 1)

check("1h raises OSError out of cancel_item", isinstance(exc, OSError)
      and not isinstance(exc, PermissionError), repr(exc))
check("1h recorded nothing at all", h.labels() == [], str(h.labels()))
note("1h escape path", where(exc))

# and a grab that fails mid-sequence, after the Change click has gone in
h = fresh()
h.arm_after = {"cancel.before_change": ("grab", OSError("capture failed"))}
with h:
    ok, exc = run(trade.cancel_item, 1)
check("1h2 mid-sequence grab failure escapes as OSError",
      isinstance(exc, OSError), repr(exc))
check("1h2 the Change click had already been sent",
      len(h.clicks()) == 1 and h.dialog == "extension",
      f"clicks={h.clicks()} dialog={h.dialog!r}")
note("1h2 state left behind",
     "the Registration Extension dialog is open and nothing closes it: the "
     "exception skips the except-Aborted recovery at trade.py:3586")


# ---------------------------------------------------------------------------
section("1i. the row moved since the caller chose it (expect= guard)")
h = fresh()
ref = trade.RowRef(VIP, 100, 410_000, 0)
with h:
    h.rows = [make_row(1, "Force Core (Ultimate)", price=1_500_000, qty=50),
              make_row(2, VIP, price=410_000, qty=100)]
    ok, exc = run(trade.cancel_item, 1, expect=ref)
check("1i refuses to cancel the wrong row", ok is False, f"got {ok!r}")
check("1i clicked nothing", h.clicks() == [], str(h.clicks()))
check("1i explained the shift", h.said("no longer holds") or h.said("now at row"),
      h.out()[-400:])


# ---------------------------------------------------------------------------
section("1j. a dialog is already open before starting")
h = fresh(dialog="confirm")
with h:
    ok, exc = run(trade.cancel_item, 1)
check("1j refuses to start", ok is False, f"got {ok!r}")
check("1j aborted on the pre-existing dialog",
      h.said("a dialog was already open"), h.out()[-300:])
if h.dialog is None:
    note("1j recovery clicked Cancel on a dialog it did not open",
         "close_any_dialog() at trade.py:3625 clicks the confirm dialog's "
         "Cancel. On the confirmation dialog that is harmless, but the same "
         "path is taken for a dialog belonging to something else entirely.")

raise SystemExit(summary())
