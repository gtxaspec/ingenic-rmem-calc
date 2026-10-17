"""Excel semantics the vendor calculators rely on.

Only the functions and operators the workbooks use are implemented. Numeric
behaviour follows LibreOffice's Excel-compatible functions, which the golden
vectors come from: results within a few ulps of an integer quotient snap to
it before CEILING/ROUNDUP/ROUNDDOWN round, as rtl::math::approx* does.
"""
import math

EMPTY = None


class XLError(Exception):
    """An Excel error value; propagates through every operation."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind

    def __repr__(self):
        return f"#{self.kind}"


def err(kind):
    return XLError(kind)


def _check(*vals):
    for v in vals:
        if isinstance(v, XLError):
            raise v


def num(v):
    """Coerce for arithmetic: empty is 0, booleans are 0/1, numeric text parses."""
    _check(v)
    if v is EMPTY:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            raise err("VALUE")
        try:
            return float(s)
        except ValueError:
            raise err("VALUE")
    raise err("VALUE")


def text(v):
    """Coerce for '&': numbers print as Excel's General format does."""
    _check(v)
    if v is EMPTY:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return general(float(v))
    return str(v)


def general(x):
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    s = f"{x:.15g}"
    if "e" in s:
        mant, exp = s.split("e")
        return f"{mant}E{'+' if int(exp) >= 0 else '-'}{abs(int(exp)):02d}"
    return s


def truth(v):
    _check(v)
    if v is EMPTY:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        if v.upper() == "TRUE":
            return True
        if v.upper() == "FALSE":
            return False
    raise err("VALUE")


def _approx_int(q):
    r = round(q)
    if abs(q - r) <= 1e-12 * max(1.0, abs(q)):
        return float(r)
    return q


# ---- operators -------------------------------------------------------------

def add(a, b):
    return num(a) + num(b)


def sub(a, b):
    return num(a) - num(b)


def mul(a, b):
    return num(a) * num(b)


def div(a, b):
    d = num(b)
    if d == 0:
        raise err("DIV/0")
    return num(a) / d


def neg(a):
    return -num(a)


def concat(a, b):
    return text(a) + text(b)


def _rank(v):
    if isinstance(v, bool):
        return 2
    if isinstance(v, str):
        return 1
    return 0


def compare(a, b, op):
    """Excel comparison: empty matches 0/""/FALSE, text compares case-blind,
    numbers < text < logicals across types."""
    _check(a, b)
    if a is EMPTY:
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0.0)
    if b is EMPTY:
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0.0)
    ra, rb = _rank(a), _rank(b)
    if ra != rb:
        x, y = ra, rb
    elif ra == 1:
        x, y = a.lower(), b.lower()
    elif ra == 2:
        x, y = int(a), int(b)
    else:
        x, y = float(a), float(b)
        if abs(x - y) <= 1e-15 * max(abs(x), abs(y)):
            x = y
    return {"=": x == y, "<>": x != y, "<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]


# ---- functions -------------------------------------------------------------

def IF(cond, then=False, otherwise=False):
    return then if truth(cond) else otherwise


def IFS(*args):
    for i in range(0, len(args) - 1, 2):
        if truth(args[i]):
            return args[i + 1]
    raise err("NA")


def AND(*args):
    vals = [v for v in _flat(args) if not (isinstance(v, str) or v is EMPTY)]
    if not vals:
        raise err("VALUE")
    return all(truth(v) for v in vals)


def OR(*args):
    vals = [v for v in _flat(args) if not (isinstance(v, str) or v is EMPTY)]
    if not vals:
        raise err("VALUE")
    return any(truth(v) for v in vals)


def _flat(args):
    for a in args:
        if isinstance(a, list):
            yield from a
        else:
            yield a


def SUM(*args):
    total = 0.0
    for a in args:
        if isinstance(a, list):
            for v in a:
                _check(v)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    total += v
        else:
            total += num(a)
    return total


def MAX(*args):
    vals = []
    for a in args:
        if isinstance(a, list):
            for v in a:
                _check(v)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals.append(float(v))
        else:
            vals.append(num(a))
    return max(vals) if vals else 0.0


def CEILING(x, sig=1.0):
    """Excel CEILING: positive x rounds up to a multiple of sig; negative x
    with positive sig rounds toward zero, with negative sig away from it."""
    x, sig = num(x), num(sig)
    if x == 0 or sig == 0:
        return 0.0
    if x > 0 and sig < 0:
        raise err("NUM")
    if sig < 0:
        return -math.ceil(_approx_int(-x / -sig)) * -sig
    return math.ceil(_approx_int(x / sig)) * sig


def _round_digits(x, digits, up):
    x, digits = num(x), int(num(digits))
    f = 10.0 ** digits
    q = _approx_int(abs(x) * f)
    r = (math.ceil(q) if up else math.floor(q)) / f
    return math.copysign(r, x) if r else 0.0


def ROUNDUP(x, digits=0):
    return _round_digits(x, digits, True)


def ROUNDDOWN(x, digits=0):
    return _round_digits(x, digits, False)


def INT(x):
    return float(math.floor(_approx_int(num(x))))


def MOD(a, b):
    a, b = num(a), num(b)
    if b == 0:
        raise err("DIV/0")
    return a - b * math.floor(_approx_int(a / b))


def DEC2HEX(n, places=None):
    n = num(n)
    if n < -549755813888 or n > 549755813887:
        raise err("NUM")
    v = int(math.floor(n)) if n >= 0 else int(math.ceil(n))
    if v < 0:
        return format(v + (1 << 40), "X")
    s = format(v, "X")
    if places is not None and places is not EMPTY:
        p = int(num(places))
        if p < len(s) or p > 10:
            raise err("NUM")
        s = s.zfill(p)
    return s


def HEX2DEC(s):
    _check(s)
    s = text(s).strip()
    if not s or len(s) > 10:
        raise err("NUM")
    try:
        v = int(s, 16)
    except ValueError:
        raise err("NUM")
    if len(s) == 10 and v & (1 << 39):
        v -= 1 << 40
    return float(v)


def na():
    """IFS with no true condition."""
    raise err("NA")
