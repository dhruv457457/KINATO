"""Reading the catalogue a merchant actually has.

The upload endpoint demanded a CSV whose header row said exactly `sku`,
`name` and `price`, in row one, with prices `float()` could parse. Real
exports say "SKU Code" and "Product Title" and "MRP", carry a title line
above the table, and write money as "₹1,299.00" or "Rs. 540/-". Every one
of those rejected the entire file and told the merchant to go and edit
their spreadsheet.

The rule this module follows is the one the rest of the codebase follows:
**decide in code what code can decide.** Header synonyms, junk rows and
money formatting are all decidable, so a model is never asked about them -
which also means catalogue upload works with no API key configured. The
model is consulted only about headers this module could not resolve, and
even then it proposes; a person confirms; the existing upsert applies it.
"""
import pytest

from app.services.catalog_ingest import (
    build_products,
    find_header_row,
    normalise_amount,
    propose_mapping,
    read_table,
)


MESSY = """Spring Collection - exported 12/08/2026 by Priya
,,,,
SKU Code,Product Title,MRP,Selling Price,Cost Price,Qty
KUR-001,Cotton Kurta,"₹1,899.00","₹1,299.00","Rs. 540",12
KUR-002,Silk Dupatta,999,899/-,320,4
,Missing Code,500,450,200,1
KUR-003,Linen Shirt,"2,499","2,499",1100,0
KUR-001,Cotton Kurta (dup),1899,1299,540,3
"""


class TestMoneyAsPeopleWriteIt:
    @pytest.mark.parametrize(
        "text,paise",
        [
            ("₹1,299.00", 129900),
            ("Rs. 540", 54000),
            ("Rs.540", 54000),
            ("899/-", 89900),
            ("2,499", 249900),
            ("INR 1,000.50", 100050),
            ("1,00,000", 10000000),   # Indian grouping
            ("12.50", 1250),
            ("0", 0),
        ],
    )
    def test_currency_decoration_is_not_a_parse_failure(self, text, paise):
        assert normalise_amount(text) == paise

    def test_the_dot_in_rs_is_an_abbreviation_not_a_decimal_point(self):
        """"Rs. 540" read as 0.54 rupees - a cost a thousand times too small.

        Not cosmetic. cogs is one of the two inputs to the margin floor, so
        a cost this wrong makes the margin look nearly total and authorises
        discounts the merchant never agreed to. Found by looking at the
        parsed output of a realistic file, not by a test.
        """
        assert normalise_amount("Rs. 540") == 54000
        assert normalise_amount("Rs. 540") != 54

    @pytest.mark.parametrize("junk", ["", "   ", "abc", ".", "-", None, "-5", "n/a"])
    def test_nonsense_is_none_rather_than_a_wrong_number(self, junk):
        assert normalise_amount(junk) is None


class TestTheHeaderIsNotAlwaysTheFirstRow:
    def test_a_title_and_a_blank_row_are_skipped(self):
        rows = [r.split(",") for r in MESSY.strip().splitlines()]
        assert find_header_row(rows) == 2

    def test_a_plain_file_still_uses_row_zero(self):
        assert find_header_row([["sku", "name", "price"], ["A1", "Shirt", "100"]]) == 0

    def test_a_file_with_no_recognisable_header_does_not_wander(self):
        """Falling back to row zero is the honest answer - better a mapping
        the merchant must correct than a header guessed out of the data."""
        assert find_header_row([["a", "b"], ["c", "d"]]) == 0


class TestColumnsAreMatchedWithoutAModel:
    def setup_method(self):
        self.headers, self.rows, self.hrow = read_table(MESSY)
        self.proposal = propose_mapping(self.headers, self.hrow)

    def test_real_world_header_names_are_understood(self):
        m = self.proposal.mapping
        assert m["sku"] == "SKU Code"
        assert m["name"] == "Product Title"
        assert m["cogs"] == "Cost Price"
        assert m["inventory"] == "Qty"

    def test_selling_price_beats_mrp(self):
        """Both are "price"; only one is what the customer is charged. MRP
        is the printed price and is usually higher, so reading it as the
        price to charge quotes people a number they were never going to pay.
        """
        assert self.proposal.mapping["price"] == "Selling Price"
        assert any("Selling Price" in n for n in self.proposal.notes)

    def test_cost_price_is_not_swallowed_by_price(self):
        """"Cost Price" contains "price". Longest synonym wins, or the
        merchant's cost becomes the number the customer is quoted."""
        assert self.proposal.mapping["cogs"] == "Cost Price"
        assert self.proposal.mapping["price"] != "Cost Price"

    def test_no_two_fields_claim_the_same_column(self):
        used = [h for h in self.proposal.mapping.values() if h]
        assert len(used) == len(set(used))

    def test_a_missing_cost_column_is_said_out_loud(self):
        p = propose_mapping(["sku", "name", "price"])
        assert p.mapping["cogs"] is None
        assert any("margin floor" in n for n in p.notes), (
            "a merchant should be told their margin floor cannot bind, not left to find out"
        )

    def test_a_file_missing_a_required_column_is_not_usable(self):
        assert propose_mapping(["sku", "name"]).is_usable is False
        assert propose_mapping(["SKU Code", "Product Title", "MRP"]).is_usable is True


class TestRowsBecomeProductsOrExplainThemselves:
    def setup_method(self):
        self.headers, self.rows, hrow = read_table(MESSY)
        self.mapping = propose_mapping(self.headers, hrow).mapping
        self.products, self.rejected = build_products(self.rows, self.mapping)

    def test_the_good_rows_come_through_with_real_numbers(self):
        by_sku = {p["product_id"]: p for p in self.products}
        assert by_sku["KUR-001"]["price_paise"] == 129900
        assert by_sku["KUR-001"]["cogs_paise"] == 54000
        assert by_sku["KUR-002"]["price_paise"] == 89900
        assert by_sku["KUR-001"]["inventory_count"] == 12

    def test_blank_spacer_rows_do_not_become_products(self):
        assert all(p["name"] for p in self.products)
        assert len(self.products) == 3

    def test_every_rejection_carries_a_reason(self):
        """A row the merchant cannot see was skipped is a row they believe
        they uploaded, and a quietly short catalogue looks exactly like a
        clean import."""
        assert self.rejected
        for r in self.rejected:
            assert r["reason"] and r["row"]

    def test_a_duplicate_code_is_rejected_rather_than_silently_overwriting(self):
        assert any("duplicate" in r["reason"] for r in self.rejected)

    def test_a_row_with_no_code_is_rejected(self):
        assert any("no product code" in r["reason"] for r in self.rejected)

    def test_a_cost_above_the_price_is_flagged_not_imported(self):
        """Usually a swapped mapping, and the one mistake that silently
        changes which discounts are legal."""
        rows = [{"sku": "X1", "name": "Thing", "price": "100", "cogs": "900"}]
        mapping = {"sku": "sku", "name": "name", "price": "price", "cogs": "cogs",
                   "inventory": None, "description": None}
        products, rejected = build_products(rows, mapping)
        assert products == []
        assert "cost is higher than price" in rejected[0]["reason"]


class TestNothingHereWritesAnything:
    def test_the_module_never_touches_the_database(self):
        """The whole safety argument. It proposes; a person confirms; the
        existing upsert applies. Same arrangement as /policy/propose."""
        import inspect

        from app.services import catalog_ingest

        src = inspect.getsource(catalog_ingest)
        for forbidden in ("upsert_product", "get_db", "INSERT", "UPDATE ", "run_db_async"):
            assert forbidden not in src, f"catalog_ingest reaches for {forbidden}"
