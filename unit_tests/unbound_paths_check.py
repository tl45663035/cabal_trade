"""Names read on a path that never bound them.

Four of these in one day, all shipped, one of them into a live run:

  * `scrolling`  -- read 40 lines before its assignment; would have killed
    every run within three cycles.
  * `free_inside` -- bound only inside `if scope:`, read unconditionally.
  * `left` -- bound only in the `else` of `if after is None:`.
  * `_px_t0` -- a PROFILING timer, bound only in the `elif` arm of a
    three-arm price branch and read after all three. It raised
    UnboundLocalError on the 12:05 run of 2026-08-15 after the item was
    loaded and priced and BEFORE Register was clicked, so the item sat in
    the shop slot with an uncommitted price, the next cycle returned it to
    the inventory, and the work-tab gate stopped the run. From outside it
    looked like the script listed an item, priced it, then took it back out.

Python raises these at RUNTIME, on the arm that was not taken, which on this
codebase means on the live path and not in any suite. This walks the AST and
reports a read of a local that is not bound on every path reaching it.

Reports only; it does not need the game, a frame, or Tesseract.
"""
import ast
import io
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
TARGET = _ROOT / "trade.py"


class Flow(ast.NodeVisitor):
    """Track names that are DEFINITELY bound, statement by statement."""

    def __init__(self, args, report):
        self.bound = set(args)
        self.report = report
        # Every name assigned anywhere in this function. A read of a name NOT
        # in here is a global or a builtin, and not this checker's business.
        self.local = set()

    # -- helpers ---------------------------------------------------------
    def targets(self, node):
        out = set()
        skip = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
                ast.Lambda)
        stack = [node]
        while stack:
            cur = stack.pop()
            if isinstance(cur, ast.Name) and isinstance(cur.ctx, ast.Store):
                out.add(cur.id)
            for child in ast.iter_child_nodes(cur):
                if not isinstance(child, skip):
                    stack.append(child)
        return out

    def reads(self, node):
        # Comprehensions and lambdas each have their OWN scope in Python 3, so
        # their targets and args are not this function's locals and their reads
        # are not this function's reads. Walking into them produced 1,100 false
        # findings on the first cut -- every `for w in ...` inside a genexp.
        skip = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
                ast.Lambda)
        stack = [node]
        while stack:
            cur = stack.pop()
            for child in ast.iter_child_nodes(cur):
                if isinstance(child, skip):
                    continue
                if isinstance(child, ast.Name) and isinstance(child.ctx,
                                                              ast.Load):
                    yield child
                stack.append(child)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            yield node

    def check(self, node):
        for n in self.reads(node):
            if n.id in self.local and n.id not in self.bound:
                self.report(n.lineno, n.id)

    NORETURN = {"bail", "halt", "sys.exit", "exit", "error", "fail",
                "halt_buying", "abort"}

    def escapes(self, body):
        """True when this branch cannot fall through to the join.

        A branch ending in return/raise/continue/break never reaches the code
        after the `if`, so a name it fails to bind cannot be read there. Same
        for a call that never returns -- `bail(...)` and `p.error(...)` in this
        file both raise, and treating them as ordinary calls produced every
        remaining false finding.
        """
        if not body:
            return False
        last = body[-1]
        if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return True
        if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
            f = last.value.func
            name = getattr(f, "attr", None) or getattr(f, "id", None)
            if name in self.NORETURN:
                return True
        if isinstance(last, ast.If) and last.orelse:
            return self.escapes(last.body) and self.escapes(last.orelse)
        return False

    def run(self, body):
        """Walk a statement list, returning the names bound on EVERY path."""
        for stmt in body:
            self.stmt(stmt)
        return self.bound

    def branch(self, body):
        """Run a branch in a copy, so it cannot leak bindings to its sibling."""
        sub = Flow((), self.report)
        sub.bound = set(self.bound)
        sub.local = self.local
        return sub.run(body), sub

    def stmt(self, node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            # A nested def has its own scope; its body is checked separately by
            # the module-level walk. Binding the NAME is what matters here.
            self.bound.add(node.name)
            return

        if isinstance(node, ast.If):
            self.check(node.test)
            then_bound, _ = self.branch(node.body)
            then_out = self.escapes(node.body)
            if node.orelse:
                else_bound, _ = self.branch(node.orelse)
                else_out = self.escapes(node.orelse)
                # Only what BOTH arms bind survives the join -- and an arm that
                # cannot REACH the join imposes no requirement on it.
                if then_out and not else_out:
                    self.bound |= else_bound
                elif else_out and not then_out:
                    self.bound |= then_bound
                elif not (then_out or else_out):
                    self.bound |= (then_bound & else_bound)
            elif then_out:
                # `if X: return` -- past here, the else-path is the only one.
                pass
            return

        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            self.check(getattr(node, "iter", None) or node.test)
            # A loop may run zero times, so its body binds nothing for certain
            # OUTSIDE the loop -- but inside it, the target is bound.
            saved = set(self.bound)
            if isinstance(node, (ast.For, ast.AsyncFor)):
                self.bound |= self.targets(node.target)
            self.branch(node.body)
            self.bound = saved
            if node.orelse:
                self.branch(node.orelse)
            return

        if isinstance(node, ast.Try):
            # Anything in the try may raise part-way, so the handlers see only
            # what was bound BEFORE it.
            self.branch(node.body)
            for h in node.handlers:
                if h.name:
                    self.bound.add(h.name)
                self.branch(h.body)
            # The else runs only when the try completed, so it does see it.
            body_bound, _ = self.branch(node.body)
            keep = set(self.bound)
            self.bound = body_bound
            if node.orelse:
                self.run(node.orelse)
            else:
                self.bound = keep | body_bound
            for stmt in node.finalbody:
                self.stmt(stmt)
            return

        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.check(item.context_expr)
                if item.optional_vars:
                    self.bound |= self.targets(item.optional_vars)
            self.run(node.body)
            return

        # A plain statement: everything it READS must already be bound, and
        # what it assigns becomes bound afterwards.
        self.check(node)
        self.bound |= self.targets(node)


def main():
    src = io.open(TARGET, encoding="utf-8-sig").read()
    tree = ast.parse(src)
    lines = src.splitlines()
    findings = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = fn.args
        args = {x.arg for x in (a.posonlyargs + a.args + a.kwonlyargs)}
        if a.vararg:
            args.add(a.vararg.arg)
        if a.kwarg:
            args.add(a.kwarg.arg)

        found = []
        flow = Flow(args, lambda ln, name: found.append((ln, name)))
        # Locals = assigned anywhere in this function, minus anything declared
        # global/nonlocal (those are not locals and are bound elsewhere).
        declared = set()
        for n in ast.walk(fn):
            if isinstance(n, (ast.Global, ast.Nonlocal)):
                declared |= set(n.names)
        assigned = set()
        skip = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
                ast.Lambda)
        stack = list(ast.iter_child_nodes(fn))
        walked = []
        while stack:
            cur = stack.pop()
            if isinstance(cur, skip):
                continue
            walked.append(cur)
            stack.extend(ast.iter_child_nodes(cur))
        for n in walked:
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                assigned.add(n.id)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assigned.add(n.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                assigned.add(n.name)
        flow.local = assigned - declared - args
        try:
            flow.run(fn.body)
        except RecursionError:
            continue
        for ln, name in found:
            findings.append((ln, fn.name, name, lines[ln - 1].strip()[:78]))

    # KNOWN AND ACCEPTED. measure_layout binds these inside a `for` over the
    # fitted anchors. A zero-iteration loop would leave them unbound, which is
    # what this checker reports and is technically true -- but that loop is
    # guarded by a length check above it, so the read is unreachable when it is
    # empty. Listed by NAME rather than silenced wholesale, so a sixth one in
    # the same function is still a finding.
    KNOWN = {("measure_layout", n) for n in
             ("ox", "oy", "scale", "span", "worst")}
    findings = [f for f in findings if (f[1], f[2]) not in KNOWN]

    findings.sort()
    print("=" * 78)
    print("names read on a path that may not have bound them")
    print("=" * 78)
    for ln, fnname, name, text in findings:
        print(f"  trade.py:{ln}  in {fnname}()")
        print(f"      {name!r}  <-  {text}")
    print()
    print("-" * 78)
    print(f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
