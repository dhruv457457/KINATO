"""Reading a catalogue that was not written for us.

The upload endpoint required a CSV whose header row said exactly `sku`,
`name` and `price`, in row one, with prices that `float()` could parse. Real
exports do not look like that. They say "SKU Code" and "Product Title" and
"MRP", they carry a title line and a blank row above the table, and they
write money as "₹1,299.00" or "Rs. 1,299/-". Every one of those failed the
whole file with a message telling the merchant to go and edit their
spreadsheet.

So this module reads the file the merchant actually has.

**Deterministic first, model second.** Header synonyms, junk rows and money
formatting are all decidable by code, and code that decides them is
testable, free, and works with no API key configured. The model is asked
only about headers this module could not resolve on its own - which on a
normal export is none of them.

**And nothing here writes a product.** It proposes a mapping and a preview;
a person confirms it; the existing upsert applies it. That is the same
arrangement as `/policy/propose`, and for the same reason - except the stake
here is arguably higher. `cogs_paise` is what the merchant paid for the
goods, and it is one of the two inputs to the margin floor. A mapping that
quietly puts the selling price in the cogs column, or leaves cogs empty,
changes which discounts are legal for that merchant on every future call.
That is not a decision to infer silently in either direction.
"""
import csv
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# The fields a product row can be built from. `sku`, `name` and `price` are
# the ones without which a row is not a product.
REQUIRED_FIELDS = ("sku", "name", "price")
OPTIONAL_FIELDS = ("cogs", "inventory", "description")
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

# Header spellings seen in real exports. Matched against a header that has
# been lowercased and stripped of punctuation, so "SKU Code" and "sku_code"
# and "SKU-Code" all arrive here as "sku code".
#
# Order matters within each list only for logging; order BETWEEN fields does
# not, because a header is assigned to at most one field (see _best_match).
_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "sku": (
        "sku", "sku code", "sku id", "product code", "product id", "item code",
        "item id", "code", "id", "variant sku", "style code", "article",
    ),
    "name": (
        "name", "product name", "product title", "title", "item name",
        "item", "product", "description name", "style name",
    ),
    "price": (
        "price", "selling price", "sale price", "sell price", "unit price",
        "rate", "mrp", "amount", "list price", "retail price", "price inr",
        "price rs",
    ),
    "cogs": (
        "cogs", "cost", "cost price", "buy price", "purchase price",
        "landed cost", "unit cost", "wholesale price", "cp",
    ),
    "inventory": (
        "inventory", "stock", "quantity", "qty", "stock count", "on hand",
        "available", "units",
    ),
    "description": ("description", "details", "product description", "notes", "about"),
}

# When both appear, the one the customer is charged is the selling price -
# MRP is the printed price and is frequently higher. Getting this backwards
# quotes people a number they were never going to pay.
_PRICE_PREFERENCE = ("selling price", "sale price", "sell price", "price", "unit price", "rate", "mrp")

# The trailing dot in "Rs." is part of the abbreviation, not a decimal
# point, and it has to go with it. Without the `\.?` this read "Rs. 540" as
# ".540" -> 0.54 rupees, turning a ₹540 cost into 54 paise. That is not a
# cosmetic parsing miss: cogs is one of the two inputs to the margin floor,
# so a cost read a thousand times too small makes the margin look nearly
# total and authorises discounts the merchant never agreed to.
_MONEY_NOISE = re.compile(r"[₹$€£]|\b(?:rs|inr|rupees?)\b\.?|/-|,|\s")
_PUNCT = re.compile(r"[^a-z0-9]+")


def _norm_header(raw: str) -> str:
    return _PUNCT.sub(" ", (raw or "").strip().lower()).strip()


def normalise_amount(value: Any) -> Optional[int]:
    """"₹1,299.00" -> 129900 paise. None when there is no number in it.

    Catalogue exports are written for people, so they carry currency
    symbols, thousands separators and the Indian "/-" suffix. `float()`
    threw on every one of them, and a throw meant the row was dropped.

    The figure is always read as the merchant's display currency - rupees -
    because that is what a human-readable catalogue contains. A file of
    paise would be a different, and much rarer, thing; guessing between the
    two by magnitude would mean a ₹50 product and a ₹5,000 one being read
    differently, which is worse than being consistently wrong.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None if value < 0 else round(float(value) * 100)

    text = str(value).strip().lower()
    if not text:
        return None
    text = _MONEY_NOISE.sub("", text)
    # A trailing or leading dash is decoration; a leading minus is a real
    # negative, and a negative price is bad data rather than a discount.
    if not text or text in ("-", "."):
        return None
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount < 0:
        return None
    return round(amount * 100)


def _best_match(header: str) -> Optional[str]:
    """Which field this header is, or None if code cannot tell.

    Exact synonym first, then containment. Containment is checked longest-
    synonym-first so that "cost price" is read as cogs rather than as price
    because it happens to contain "price".
    """
    h = _norm_header(header)
    if not h:
        return None
    for field_name, synonyms in _SYNONYMS.items():
        if h in synonyms:
            return field_name
    ranked = sorted(
        ((f, s) for f, syns in _SYNONYMS.items() for s in syns),
        key=lambda pair: -len(pair[1]),
    )
    for field_name, synonym in ranked:
        if synonym in h:
            return field_name
    return None


def find_header_row(rows: List[List[str]], look_at: int = 12) -> int:
    """Which row is the header. Not always the first one.

    Exports routinely open with a title, an export timestamp, a blank line,
    or a merchant's own note. csv.DictReader took row zero on faith and
    turned "Spring Collection - exported 12/08" into the column names, after
    which every row failed and the file was rejected as malformed.

    Scored rather than pattern-matched: the header is the early row where
    the most cells look like field names we recognise.
    """
    best_row, best_score = 0, -1
    for i, row in enumerate(rows[:look_at]):
        cells = [c for c in row if str(c).strip()]
        if len(cells) < 2:
            continue
        recognised = sum(1 for c in cells if _best_match(c))
        # A header is mostly words. A data row that happens to contain a
        # recognisable word ("Price" as a product name) is outvoted by its
        # numbers.
        numeric = sum(1 for c in cells if normalise_amount(c) is not None)
        score = recognised * 2 - numeric
        if score > best_score:
            best_row, best_score = i, score
    return best_row if best_score > 0 else 0


@dataclass
class ProposedMapping:
    """What we think each column is, and how sure of it we are."""
    mapping: Dict[str, Optional[str]] = field(default_factory=dict)   # field -> header
    unresolved_headers: List[str] = field(default_factory=list)
    header_row: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return all(self.mapping.get(f) for f in REQUIRED_FIELDS)


def propose_mapping(headers: List[str], header_row: int = 0) -> ProposedMapping:
    """Match columns to fields with no model involved.

    A header is claimed by at most one field, and a field takes at most one
    header - two columns both mapped to `price` would make which one is
    charged depend on dictionary order.
    """
    proposal = ProposedMapping(header_row=header_row)
    claimed: Dict[str, str] = {}

    candidates: Dict[str, List[str]] = {f: [] for f in ALL_FIELDS}
    for h in headers:
        f = _best_match(h)
        if f:
            candidates[f].append(h)

    for f, matches in candidates.items():
        if not matches:
            continue
        if f == "price" and len(matches) > 1:
            # Prefer what the customer is actually charged over the printed
            # MRP. Both are "price"; only one is the number to quote.
            matches = sorted(
                matches,
                key=lambda h: next(
                    (i for i, p in enumerate(_PRICE_PREFERENCE) if p in _norm_header(h)),
                    len(_PRICE_PREFERENCE),
                ),
            )
            proposal.notes.append(
                f"Several price-like columns ({', '.join(matches)}); read '{matches[0]}' "
                "as the price to charge."
            )
        chosen = matches[0]
        if chosen in claimed:
            continue
        claimed[chosen] = f
        proposal.mapping[f] = chosen

    for f in ALL_FIELDS:
        proposal.mapping.setdefault(f, None)
    proposal.unresolved_headers = [h for h in headers if h and h not in claimed]

    if not proposal.mapping.get("cogs"):
        # Said out loud rather than left to be noticed. Without cogs the
        # margin floor has nothing to compute against, so the discount
        # ceiling becomes the only limit standing between the agent and the
        # merchant's margin.
        proposal.notes.append(
            "No cost column found. The margin floor cannot protect a product whose cost "
            "is unknown - only your discount ceiling will apply to these."
        )
    return proposal


def read_table(text: str) -> Tuple[List[str], List[Dict[str, str]], int]:
    """(headers, rows, header_row_index) from CSV text of unknown shape."""
    raw_rows = [r for r in csv.reader(io.StringIO(text))]
    if not raw_rows:
        return [], [], 0
    header_row = find_header_row(raw_rows)
    headers = [str(c).strip() for c in raw_rows[header_row]]
    rows: List[Dict[str, str]] = []
    for r in raw_rows[header_row + 1:]:
        if not any(str(c).strip() for c in r):
            continue  # blank spacer row
        row = {headers[i]: (str(r[i]).strip() if i < len(r) else "") for i in range(len(headers))}
        rows.append(row)
    return headers, rows, header_row


def build_products(
    rows: List[Dict[str, str]], mapping: Dict[str, Optional[str]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """(products, rejected). Rejections carry a reason, never a silent drop.

    A row the merchant cannot see was skipped is a row they believe they
    uploaded - and a catalogue that is quietly short is indistinguishable
    from one that imported cleanly.
    """
    products: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    seen: set = set()

    for row in rows:
        def cell(f: str) -> str:
            col = mapping.get(f)
            return (row.get(col) or "").strip() if col else ""

        sku, name = cell("sku"), cell("name")
        price = normalise_amount(cell("price"))
        if not sku:
            rejected.append({"row": name or "(blank)", "reason": "no product code"})
            continue
        if not name:
            rejected.append({"row": sku, "reason": "no product name"})
            continue
        if price is None:
            rejected.append({"row": sku, "reason": f"could not read a price from {cell('price')!r}"})
            continue
        if sku in seen:
            rejected.append({"row": sku, "reason": "duplicate product code in this file"})
            continue
        seen.add(sku)

        cogs = normalise_amount(cell("cogs"))
        if cogs is not None and cogs > price:
            # Sold below cost is a real thing merchants do; a cost column
            # that is above the price on EVERY row is usually a swapped
            # mapping, and that is what the preview is for. Kept, flagged.
            rejected.append({"row": sku, "reason": "cost is higher than price - check the mapping"})
            continue

        inv = cell("inventory")
        products.append({
            "product_id": sku,
            "name": name,
            "price_paise": price,
            "cogs_paise": cogs,
            "inventory_count": int(inv) if inv.isdigit() else 0,
            "description": cell("description")[:500],
        })
    return products, rejected
