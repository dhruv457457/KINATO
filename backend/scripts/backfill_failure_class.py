"""Classify the checkouts that were abandoned before anything classified them.

`failure_class` was only ever written by the `payment.failed` webhook, so
every cart the sweeper timed out was left NULL - 604 of 608 rows in
production. The sweeper writes `USER_ABANDON` now, but only for carts it
sweeps from here on. This backfills the ones already on the floor.

It is deliberately narrower than it could be. A row is only touched when:

  * `failure_class IS NULL`  - never overwrite a classification that exists,
  * every error field is empty - which is exactly the condition
    `failure_diagnosis.diagnose()` uses to return USER_ABANDON, so this
    records what the classifier would already say rather than deciding
    anything new,
  * `status <> 'paid'` - a paid cart has no failure to describe.

Anything with a partial error object is LEFT ALONE and reported. Those are
rows a webhook touched without a class being stored, and guessing at them
here would be inventing history rather than recording it - see FINDINGS #16
for what a sparse error object does to a classifier that assumes too much.

    python scripts/backfill_failure_class.py            # dry run, changes nothing
    python scripts/backfill_failure_class.py --apply    # writes
"""
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

from app.db.database import get_db  # noqa: E402
from app.services.failure_diagnosis import USER_ABANDON  # noqa: E402

_ERROR_FIELDS = ("error_code", "error_reason", "error_description", "error_source", "error_step")

_NO_ERROR_OBJECT = " AND ".join(f"({f} IS NULL OR {f} = '')" for f in _ERROR_FIELDS)


def main() -> None:
    apply = "--apply" in sys.argv

    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute(
            f"SELECT COUNT(*) AS n FROM checkouts "
            f"WHERE failure_class IS NULL AND status <> 'paid' AND {_NO_ERROR_OBJECT}"
        )
        eligible = dict(cursor.fetchone())["n"]

        # Rows a webhook touched but which still have no class. Reported, not
        # guessed at - a partial error object is a real signal and deserves
        # the real classifier, not this script.
        cursor.execute(
            f"SELECT COUNT(*) AS n FROM checkouts "
            f"WHERE failure_class IS NULL AND status <> 'paid' AND NOT ({_NO_ERROR_OBJECT})"
        )
        has_error_object = dict(cursor.fetchone())["n"]

        cursor.execute("SELECT COUNT(*) AS n FROM checkouts WHERE failure_class IS NOT NULL")
        already = dict(cursor.fetchone())["n"]

        print()
        print(f"  already classified                  {already}")
        print(f"  no error object -> {USER_ABANDON:<14}   {eligible}")
        print(f"  has an error object, still unclassified  {has_error_object}   (left alone)")
        print()

        if not apply:
            print("  Dry run. Re-run with --apply to write.")
            print()
            return

        cursor.execute(
            f"UPDATE checkouts SET failure_class = %s "
            f"WHERE failure_class IS NULL AND status <> 'paid' AND {_NO_ERROR_OBJECT}",
            (USER_ABANDON,),
        )
        print(f"  Updated {cursor.rowcount} rows.")
        print()


if __name__ == "__main__":
    main()
